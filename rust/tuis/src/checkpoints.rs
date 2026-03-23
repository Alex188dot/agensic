use base64::engine::general_purpose::STANDARD as BASE64_STANDARD;
use base64::Engine;
use clap::Parser;
use flate2::read::GzDecoder;
use serde::{Deserialize, Serialize};
use std::collections::{hash_map::DefaultHasher, BTreeMap, BTreeSet};
use std::fs::{self, File};
use std::hash::{Hash, Hasher};
use std::io::{self, BufRead, BufReader, Read, Write};
use std::path::Path;
use std::process::Command;
use std::time::{Duration, Instant};

const DEFAULT_CHECKPOINT_INTERVAL_MS: u64 = 120;
const DEFAULT_CHECKPOINT_INTERVAL_EVENTS: i64 = 48;
const DEFAULT_RESIZE_SETTLE_MS: u64 = 140;

#[derive(Debug, Parser, Clone)]
#[command(name = "agensic-tuis checkpoints")]
pub struct CheckpointsArgs {
    #[arg(long, default_value = "")]
    out: String,

    #[arg(long, default_value_t = DEFAULT_CHECKPOINT_INTERVAL_MS)]
    interval_ms: u64,

    #[arg(long, default_value_t = DEFAULT_CHECKPOINT_INTERVAL_EVENTS)]
    interval_events: i64,
}

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct CheckpointRecord {
    pub seq: i64,
    pub rows: u16,
    pub cols: u16,
    #[serde(default)]
    pub state_b64: String,
}

#[derive(Clone, Debug, Default, Serialize, Deserialize)]
pub struct GitCheckpointRecord {
    #[serde(default)]
    pub checkpoint_id: String,
    pub seq: i64,
    #[serde(default)]
    pub timestamp: i64,
    #[serde(default)]
    pub reason: String,
    #[serde(default)]
    pub repo_root: String,
    #[serde(default)]
    pub branch: String,
    #[serde(default)]
    pub head: String,
    #[serde(default)]
    pub comparison_base_head: String,
    #[serde(default)]
    pub status_porcelain: String,
    #[serde(default)]
    pub status_fingerprint: String,
    #[serde(default)]
    pub tracked_patch_sha256: String,
    #[serde(default)]
    pub committed_diff_stat: String,
    #[serde(default)]
    pub committed_files: Vec<String>,
    #[serde(default)]
    pub worktree_diff_stat: String,
    #[serde(default)]
    pub changed_files: Vec<String>,
    #[serde(default)]
    pub untracked_paths: Vec<String>,
    #[serde(default)]
    pub fingerprint: String,
    #[serde(skip)]
    pub delta_diff_stat: String,
    #[serde(skip)]
    pub delta_files: Vec<String>,
    #[serde(skip)]
    pub delta_file_markers: BTreeMap<String, String>,
    #[serde(skip)]
    pub cumulative_diff_stat: String,
    #[serde(skip)]
    pub cumulative_files: Vec<String>,
    #[serde(skip)]
    pub cumulative_file_markers: BTreeMap<String, String>,
}

#[derive(Clone, Debug, Default, Deserialize)]
struct CheckpointInputEvent {
    #[serde(default)]
    direction: String,
    #[serde(default)]
    seq: i64,
    rows: Option<u16>,
    cols: Option<u16>,
    #[serde(default)]
    data_b64: String,
}

struct CheckpointRecorder {
    parser: vt100::Parser,
    initialized: bool,
    handle: File,
    interval: Duration,
    interval_events: i64,
    last_checkpoint_at: Instant,
    last_checkpoint_seq: i64,
    last_state_hash: u64,
    last_seen_seq: i64,
    pending_resize_seq: Option<i64>,
    pending_resize_at: Option<Instant>,
}

impl CheckpointRecorder {
    fn new(path: &str, interval_ms: u64, interval_events: i64) -> io::Result<Self> {
        let out_path = Path::new(path);
        if let Some(parent) = out_path.parent() {
            if !parent.as_os_str().is_empty() {
                fs::create_dir_all(parent)?;
            }
        }
        Ok(Self {
            parser: vt100::Parser::new(24, 80, 0),
            initialized: false,
            handle: File::create(out_path)?,
            interval: Duration::from_millis(interval_ms.max(1)),
            interval_events: interval_events.max(1),
            last_checkpoint_at: Instant::now(),
            last_checkpoint_seq: 0,
            last_state_hash: 0,
            last_seen_seq: 0,
            pending_resize_seq: None,
            pending_resize_at: None,
        })
    }

    fn ensure_parser(&mut self, rows: u16, cols: u16) {
        let safe_rows = rows.max(1);
        let safe_cols = cols.max(1);
        if !self.initialized {
            self.parser = vt100::Parser::new(safe_rows, safe_cols, 0);
            self.initialized = true;
            return;
        }
        self.parser.set_size(safe_rows, safe_cols);
    }

    fn handle_event(&mut self, event: CheckpointInputEvent) -> io::Result<()> {
        self.flush_pending_resize_checkpoint(false)?;
        let direction = event.direction.trim().to_lowercase();
        if direction.is_empty() {
            return Ok(());
        }
        if event.seq > 0 {
            self.last_seen_seq = event.seq;
        }
        match direction.as_str() {
            "resize" => {
                self.ensure_parser(event.rows.unwrap_or(24), event.cols.unwrap_or(80));
                self.pending_resize_seq = Some(event.seq.max(self.last_seen_seq));
                self.pending_resize_at = Some(Instant::now());
            }
            "pty" => {
                if !self.initialized {
                    self.ensure_parser(event.rows.unwrap_or(24), event.cols.unwrap_or(80));
                }
                if event.data_b64.is_empty() {
                    return Ok(());
                }
                let Ok(data) = BASE64_STANDARD.decode(event.data_b64.as_bytes()) else {
                    return Ok(());
                };
                if data.is_empty() {
                    return Ok(());
                }
                self.parser.process(&data);
                self.maybe_emit_checkpoint(event.seq, false)?;
            }
            "finish" => {
                self.flush_pending_resize_checkpoint(true)?;
                self.maybe_emit_checkpoint(event.seq.max(self.last_seen_seq), true)?;
            }
            _ => {}
        }
        Ok(())
    }

    fn flush_pending_resize_checkpoint(&mut self, force: bool) -> io::Result<()> {
        let Some(seq) = self.pending_resize_seq else {
            return Ok(());
        };
        let settled = self
            .pending_resize_at
            .map(|at| at.elapsed() >= Duration::from_millis(DEFAULT_RESIZE_SETTLE_MS))
            .unwrap_or(false);
        if !force && !settled {
            return Ok(());
        }
        self.pending_resize_seq = None;
        self.pending_resize_at = None;
        self.maybe_emit_checkpoint(seq.max(self.last_seen_seq), true)
    }

    fn maybe_emit_checkpoint(&mut self, seq: i64, force: bool) -> io::Result<()> {
        if !self.initialized {
            return Ok(());
        }
        let now = Instant::now();
        let seq_gap = seq.saturating_sub(self.last_checkpoint_seq);
        if !force
            && seq_gap < self.interval_events
            && now.duration_since(self.last_checkpoint_at) < self.interval
        {
            return Ok(());
        }
        let state = self.parser.screen().state_formatted();
        let state_hash = hash_bytes(&state);
        if state_hash == self.last_state_hash
            && (!force || seq.max(self.last_seen_seq) == self.last_checkpoint_seq)
        {
            return Ok(());
        }
        let (rows, cols) = self.parser.screen().size();
        let record = CheckpointRecord {
            seq: seq.max(self.last_seen_seq),
            rows,
            cols,
            state_b64: BASE64_STANDARD.encode(state),
        };
        self.handle.write_all(
            serde_json::to_string(&record)
                .unwrap_or_else(|_| "{}".to_string())
                .as_bytes(),
        )?;
        self.handle.write_all(b"\n")?;
        self.handle.flush()?;
        self.last_checkpoint_at = now;
        self.last_checkpoint_seq = record.seq;
        self.last_state_hash = state_hash;
        Ok(())
    }
}

fn hash_bytes(bytes: &[u8]) -> u64 {
    let mut hasher = DefaultHasher::new();
    bytes.hash(&mut hasher);
    hasher.finish()
}

fn artifact_candidate_paths(path: &str) -> Vec<String> {
    let clean = path.trim();
    if clean.is_empty() {
        return Vec::new();
    }
    if clean.ends_with(".gz") {
        return vec![clean.to_string(), clean.trim_end_matches(".gz").to_string()];
    }
    vec![clean.to_string(), format!("{clean}.gz")]
}

fn read_text_artifact(path: &str) -> Option<String> {
    for candidate in artifact_candidate_paths(path) {
        let target = Path::new(&candidate);
        if !target.is_file() {
            continue;
        }
        if candidate.ends_with(".gz") {
            let mut contents = String::new();
            let file = File::open(target).ok()?;
            let mut decoder = GzDecoder::new(file);
            decoder.read_to_string(&mut contents).ok()?;
            return Some(contents);
        }
        return fs::read_to_string(target).ok();
    }
    None
}

pub fn checkpoint_path_for_transcript(transcript_path: &str) -> String {
    let clean = transcript_path.trim();
    if clean.is_empty() {
        return String::new();
    }
    if let Some(prefix) = clean.strip_suffix(".transcript.jsonl.gz") {
        return format!("{prefix}.checkpoints.jsonl.gz");
    }
    if let Some(prefix) = clean.strip_suffix(".transcript.jsonl") {
        return format!("{prefix}.checkpoints.jsonl");
    }
    format!("{clean}.checkpoints.jsonl")
}

pub fn git_checkpoint_path_for_transcript(transcript_path: &str) -> String {
    let clean = transcript_path.trim();
    if clean.is_empty() {
        return String::new();
    }
    if let Some(prefix) = clean.strip_suffix(".transcript.jsonl.gz") {
        return format!("{prefix}.git-checkpoints.jsonl.gz");
    }
    if let Some(prefix) = clean.strip_suffix(".transcript.jsonl") {
        return format!("{prefix}.git-checkpoints.jsonl");
    }
    format!("{clean}.git-checkpoints.jsonl")
}

pub fn decode_checkpoint_state(record: &CheckpointRecord) -> Vec<u8> {
    if record.state_b64.is_empty() {
        return Vec::new();
    }
    BASE64_STANDARD
        .decode(record.state_b64.as_bytes())
        .unwrap_or_default()
}

pub fn load_checkpoint_records(path: &str) -> Vec<CheckpointRecord> {
    let Some(contents) = read_text_artifact(path) else {
        return Vec::new();
    };
    BufReader::new(contents.as_bytes())
        .lines()
        .filter_map(|line| line.ok())
        .filter_map(|line| serde_json::from_str::<CheckpointRecord>(&line).ok())
        .filter(|record| record.rows > 0 && record.cols > 0 && !record.state_b64.is_empty())
        .collect()
}

fn git_changed_files_from_diff_stat(diff_stat: &str) -> Vec<String> {
    let mut files = Vec::new();
    for raw_line in diff_stat
        .lines()
        .map(str::trim_end)
        .filter(|line| !line.is_empty())
    {
        if let Some((path, _)) = raw_line.split_once('|') {
            let clean = path.trim();
            if !clean.is_empty() && !files.iter().any(|item| item == clean) {
                files.push(clean.to_string());
            }
        }
    }
    files
}

fn git_capture(repo_root: &str, args: &[&str]) -> Option<String> {
    let output = Command::new("git")
        .arg("-C")
        .arg(repo_root)
        .args(args)
        .output()
        .ok()?;
    if !output.status.success() {
        return None;
    }
    String::from_utf8(output.stdout)
        .ok()
        .map(|text| text.trim().to_string())
}

fn git_capture_lines(repo_root: &str, args: &[&str]) -> Vec<String> {
    git_capture(repo_root, args)
        .map(|text| {
            text.lines()
                .map(str::trim)
                .filter(|line| !line.is_empty())
                .map(ToOwned::to_owned)
                .collect()
        })
        .unwrap_or_default()
}

fn marker_for_git_status(status: &str) -> &'static str {
    let first = status
        .chars()
        .find(|ch| !ch.is_whitespace())
        .unwrap_or_default();
    match first {
        '?' | 'A' | 'C' => "+",
        'D' => "-",
        'R' => ">",
        'M' => "~",
        'U' => "!",
        _ => "•",
    }
}

fn marker_for_name_status(status: &str) -> &'static str {
    match status.chars().next().unwrap_or_default() {
        'A' | 'C' => "+",
        'D' => "-",
        'R' => ">",
        'M' => "~",
        _ => "•",
    }
}

fn parse_name_status_lines(lines: &[String]) -> (Vec<String>, BTreeMap<String, String>) {
    let mut files = Vec::new();
    let mut markers = BTreeMap::new();
    for line in lines {
        let parts: Vec<&str> = line.split('\t').collect();
        if parts.len() < 2 {
            continue;
        }
        let status = parts[0].trim();
        let path = parts.last().copied().unwrap_or_default().trim();
        if path.is_empty() {
            continue;
        }
        let clean_path = path.to_string();
        if !files.iter().any(|item| item == &clean_path) {
            files.push(clean_path.clone());
        }
        markers.insert(clean_path, marker_for_name_status(status).to_string());
    }
    (files, markers)
}

fn parse_status_porcelain(text: &str) -> (Vec<String>, BTreeMap<String, String>) {
    let mut files = Vec::new();
    let mut markers = BTreeMap::new();
    for raw_line in text
        .lines()
        .map(str::trim_end)
        .filter(|line| !line.is_empty())
    {
        if raw_line.len() < 3 {
            continue;
        }
        let status = raw_line.get(..2).unwrap_or_default();
        let path_part = raw_line.get(3..).unwrap_or_default().trim();
        if path_part.is_empty() {
            continue;
        }
        let path = path_part
            .split_once(" -> ")
            .map(|(_, value)| value.trim())
            .unwrap_or(path_part)
            .trim();
        if path.is_empty() {
            continue;
        }
        let clean_path = path.to_string();
        if !files.iter().any(|item| item == &clean_path) {
            files.push(clean_path.clone());
        }
        markers.insert(clean_path, marker_for_git_status(status).to_string());
    }
    (files, markers)
}

fn parse_diff_stat_files(diff_stat: &str) -> (Vec<String>, BTreeMap<String, String>) {
    let mut files = Vec::new();
    let mut stats = BTreeMap::new();
    for raw_line in diff_stat
        .lines()
        .map(str::trim_end)
        .filter(|line| !line.is_empty())
    {
        if let Some((path, stat)) = raw_line.split_once('|') {
            let clean_path = path.trim();
            let clean_stat = stat.trim();
            if clean_path.is_empty() || clean_stat.is_empty() {
                continue;
            }
            let clean_path = clean_path.to_string();
            if !files.iter().any(|item| item == &clean_path) {
                files.push(clean_path.clone());
            }
            stats.insert(clean_path, clean_stat.to_string());
        }
    }
    (files, stats)
}

#[derive(Clone, Debug, PartialEq, Eq)]
struct DiffStatFragment {
    count: usize,
    visual: String,
}

fn parse_diff_stat_fragment(stat: &str) -> Option<DiffStatFragment> {
    let clean = stat.trim();
    if clean.is_empty() {
        return None;
    }
    let mut parts = clean.split_whitespace();
    let count = parts.next()?.parse::<usize>().ok()?;
    let visual = parts.collect::<Vec<_>>().join(" ");
    Some(DiffStatFragment { count, visual })
}

fn merge_diff_stat_value(existing: Option<&str>, incoming: Option<&str>) -> Option<String> {
    let clean_incoming = incoming.map(str::trim).filter(|value| !value.is_empty())?;
    let clean_existing = existing.map(str::trim).filter(|value| !value.is_empty());

    match (
        clean_existing.and_then(parse_diff_stat_fragment),
        parse_diff_stat_fragment(clean_incoming),
    ) {
        (Some(previous), Some(next)) => {
            let mut visual = previous.visual;
            visual.push_str(&next.visual);
            let visual = visual.trim().to_string();
            if visual.is_empty() {
                Some(previous.count.saturating_add(next.count).to_string())
            } else {
                Some(format!(
                    "{} {}",
                    previous.count.saturating_add(next.count),
                    visual
                ))
            }
        }
        _ => clean_existing
            .filter(|value| *value == clean_incoming)
            .map(str::to_string)
            .or_else(|| Some(clean_incoming.to_string())),
    }
}

fn merge_cumulative_file(
    files: &mut Vec<String>,
    markers: &mut BTreeMap<String, String>,
    stats: &mut BTreeMap<String, String>,
    path: &str,
    marker: Option<&str>,
    stat: Option<&str>,
) {
    let clean_path = path.trim();
    if clean_path.is_empty() {
        return;
    }
    if !files.iter().any(|item| item == clean_path) {
        files.push(clean_path.to_string());
    }
    if let Some(value) = marker.map(str::trim).filter(|value| !value.is_empty()) {
        markers.insert(clean_path.to_string(), value.to_string());
    }
    if let Some(value) = stat.map(str::trim).filter(|value| !value.is_empty()) {
        let merged = merge_diff_stat_value(stats.get(clean_path).map(String::as_str), Some(value))
            .unwrap_or_else(|| value.to_string());
        stats.insert(clean_path.to_string(), merged);
    }
}

fn build_cumulative_diff_stat(files: &[String], stats: &BTreeMap<String, String>) -> String {
    let mut lines = Vec::new();
    for file in files {
        if let Some(stat) = stats.get(file) {
            lines.push(format!("{file} | {stat}"));
        }
    }
    lines.join("\n")
}

pub fn load_git_checkpoint_records(path: &str) -> Vec<GitCheckpointRecord> {
    let Some(contents) = read_text_artifact(path) else {
        return Vec::new();
    };
    let mut records: Vec<GitCheckpointRecord> = BufReader::new(contents.as_bytes())
        .lines()
        .enumerate()
        .filter_map(|(idx, line)| {
            let line = line.ok()?;
            let mut record = serde_json::from_str::<GitCheckpointRecord>(&line).ok()?;
            if record.checkpoint_id.trim().is_empty() {
                record.checkpoint_id = format!("chkpt-{:04}", idx + 1);
            }
            if record.changed_files.is_empty() && !record.worktree_diff_stat.trim().is_empty() {
                record.changed_files = git_changed_files_from_diff_stat(&record.worktree_diff_stat);
            }
            Some(record)
        })
        .filter(|record| record.seq > 0)
        .collect();
    records.sort_by_key(|record| (record.seq, record.timestamp));
    let mut previous_head = String::new();
    for record in &mut records {
        record.delta_diff_stat.clear();
        record.delta_files.clear();
        record.delta_file_markers.clear();
        record.cumulative_diff_stat.clear();
        record.cumulative_files.clear();
        record.cumulative_file_markers.clear();
        if record.committed_files.is_empty() && !record.committed_diff_stat.trim().is_empty() {
            record.committed_files = git_changed_files_from_diff_stat(&record.committed_diff_stat);
        }
        if record.comparison_base_head.trim().is_empty() && !previous_head.trim().is_empty() {
            record.comparison_base_head = previous_head.clone();
        }
        if record.committed_files.is_empty()
            && record.committed_diff_stat.trim().is_empty()
            && !record.repo_root.trim().is_empty()
            && !record.comparison_base_head.trim().is_empty()
            && !record.head.trim().is_empty()
            && record.comparison_base_head != record.head
        {
            if let Some(diff_stat) = git_capture(
                &record.repo_root,
                &[
                    "diff",
                    "--stat",
                    &format!("{}..{}", record.comparison_base_head, record.head),
                ],
            ) {
                record.committed_diff_stat = diff_stat;
                record.committed_files =
                    git_changed_files_from_diff_stat(&record.committed_diff_stat);
            }
        }
        if !record.head.trim().is_empty() {
            previous_head = record.head.clone();
        }
    }
    records
}

pub fn enrich_git_checkpoint_records(
    records: &mut [GitCheckpointRecord],
    repo_root: &str,
    _head_start: &str,
) {
    let clean_repo_root = repo_root.trim();
    let mut cumulative_files = Vec::new();
    let mut cumulative_markers = BTreeMap::new();
    let mut cumulative_stats = BTreeMap::new();
    for record in records {
        record.delta_diff_stat.clear();
        record.delta_files.clear();
        record.delta_file_markers.clear();
        record.cumulative_diff_stat.clear();
        record.cumulative_files.clear();
        record.cumulative_file_markers.clear();
        let mut delta_files = Vec::new();
        let mut delta_markers = BTreeMap::new();
        let mut delta_stats = BTreeMap::new();
        let mut committed_files = record.committed_files.clone();
        let mut committed_markers = BTreeMap::new();
        let (_, committed_stats) = parse_diff_stat_files(&record.committed_diff_stat);
        if !clean_repo_root.is_empty()
            && !record.comparison_base_head.trim().is_empty()
            && !record.head.trim().is_empty()
            && record.comparison_base_head != record.head
        {
            let range = format!("{}..{}", record.comparison_base_head, record.head);
            let name_status =
                git_capture_lines(clean_repo_root, &["diff", "--name-status", &range]);
            let (files, markers) = parse_name_status_lines(&name_status);
            if !files.is_empty() {
                committed_files = files;
            }
            committed_markers = markers;
        }
        if committed_files.is_empty() {
            committed_files = git_changed_files_from_diff_stat(&record.committed_diff_stat);
        }
        for file in &committed_files {
            merge_cumulative_file(
                &mut delta_files,
                &mut delta_markers,
                &mut delta_stats,
                file,
                committed_markers
                    .get(file)
                    .map(String::as_str)
                    .or(Some("•")),
                committed_stats.get(file).map(String::as_str),
            );
        }

        let (status_files, status_markers) = parse_status_porcelain(&record.status_porcelain);
        let (worktree_stat_files, worktree_stats) =
            parse_diff_stat_files(&record.worktree_diff_stat);
        let mut worktree_files = BTreeSet::new();
        worktree_files.extend(status_files.iter().cloned());
        worktree_files.extend(worktree_stat_files.iter().cloned());
        worktree_files.extend(record.changed_files.iter().cloned());
        worktree_files.extend(record.untracked_paths.iter().cloned());
        for file in worktree_files {
            let fallback_marker = if record.untracked_paths.iter().any(|item| item == &file) {
                Some("+")
            } else {
                Some("•")
            };
            merge_cumulative_file(
                &mut delta_files,
                &mut delta_markers,
                &mut delta_stats,
                &file,
                status_markers
                    .get(&file)
                    .map(String::as_str)
                    .or(fallback_marker),
                worktree_stats.get(&file).map(String::as_str),
            );
        }

        record.delta_files = delta_files.clone();
        record.delta_file_markers = delta_markers.clone();
        record.delta_diff_stat = build_cumulative_diff_stat(&delta_files, &delta_stats);

        for file in &delta_files {
            merge_cumulative_file(
                &mut cumulative_files,
                &mut cumulative_markers,
                &mut cumulative_stats,
                file,
                delta_markers.get(file).map(String::as_str),
                delta_stats.get(file).map(String::as_str),
            );
        }

        record.cumulative_files = cumulative_files.clone();
        record.cumulative_file_markers = cumulative_markers.clone();
        record.cumulative_diff_stat =
            build_cumulative_diff_stat(&cumulative_files, &cumulative_stats);
    }
}

pub fn run_from_env(argv: &[String]) -> Result<(), String> {
    let args = CheckpointsArgs::parse_from(
        std::iter::once("checkpoints".to_string()).chain(argv.iter().cloned()),
    );
    if args.out.trim().is_empty() {
        return Err("missing checkpoint output path".to_string());
    }
    let stdin = io::stdin();
    let mut recorder = CheckpointRecorder::new(&args.out, args.interval_ms, args.interval_events)
        .map_err(|err| format!("checkpoint recorder init failed: {err}"))?;
    for line in stdin.lock().lines() {
        let line = line.map_err(|err| format!("checkpoint input read failed: {err}"))?;
        if line.trim().is_empty() {
            continue;
        }
        let event: CheckpointInputEvent = serde_json::from_str(&line)
            .map_err(|err| format!("invalid checkpoint input payload: {err}"))?;
        recorder
            .handle_event(event)
            .map_err(|err| format!("checkpoint write failed: {err}"))?;
    }
    recorder
        .flush_pending_resize_checkpoint(true)
        .map_err(|err| format!("final resize checkpoint write failed: {err}"))?;
    recorder
        .maybe_emit_checkpoint(recorder.last_seen_seq, true)
        .map_err(|err| format!("final checkpoint write failed: {err}"))?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::{
        checkpoint_path_for_transcript, decode_checkpoint_state, enrich_git_checkpoint_records,
        load_checkpoint_records, CheckpointInputEvent, CheckpointRecorder, GitCheckpointRecord,
    };
    use base64::Engine;
    use flate2::write::GzEncoder;
    use flate2::Compression;
    use std::collections::BTreeMap;
    use std::fs;
    use std::io::Write;
    use std::path::PathBuf;
    use std::time::{SystemTime, UNIX_EPOCH};

    #[test]
    fn checkpoint_path_uses_sidecar_suffix() {
        assert_eq!(
            checkpoint_path_for_transcript("/tmp/demo.transcript.jsonl"),
            "/tmp/demo.checkpoints.jsonl"
        );
        assert_eq!(
            checkpoint_path_for_transcript("/tmp/demo.transcript.jsonl.gz"),
            "/tmp/demo.checkpoints.jsonl.gz"
        );
    }

    #[test]
    fn checkpoint_loader_round_trips_state() {
        let suffix = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|duration| duration.as_nanos())
            .unwrap_or(0);
        let path: PathBuf = std::env::temp_dir().join(format!("demo-{suffix}.checkpoints.jsonl"));
        let mut parser = vt100::Parser::new(3, 12, 0);
        parser.process(b"hello");
        let record = serde_json::json!({
            "seq": 9,
            "rows": 3,
            "cols": 12,
            "state_b64": base64::engine::general_purpose::STANDARD.encode(parser.screen().state_formatted()),
        });
        fs::write(&path, format!("{record}\n")).expect("write");
        let records = load_checkpoint_records(path.to_str().unwrap_or_default());
        assert_eq!(records.len(), 1);
        let bytes = decode_checkpoint_state(&records[0]);
        assert!(!bytes.is_empty());
        let _ = fs::remove_file(path);
    }

    #[test]
    fn resize_burst_is_debounced_into_single_checkpoint() {
        let suffix = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|duration| duration.as_nanos())
            .unwrap_or(0);
        let path: PathBuf =
            std::env::temp_dir().join(format!("resize-burst-{suffix}.checkpoints.jsonl"));
        let mut recorder = CheckpointRecorder::new(path.to_str().unwrap_or_default(), 1_000, 1_000)
            .expect("recorder");
        recorder
            .handle_event(CheckpointInputEvent {
                direction: "resize".to_string(),
                seq: 1,
                rows: Some(20),
                cols: Some(80),
                data_b64: String::new(),
            })
            .expect("first resize");
        recorder
            .handle_event(CheckpointInputEvent {
                direction: "resize".to_string(),
                seq: 2,
                rows: Some(24),
                cols: Some(100),
                data_b64: String::new(),
            })
            .expect("second resize");
        recorder
            .flush_pending_resize_checkpoint(true)
            .expect("flush");
        let records = load_checkpoint_records(path.to_str().unwrap_or_default());
        assert_eq!(records.len(), 1);
        assert_eq!(records[0].seq, 2);
        assert_eq!(records[0].rows, 24);
        assert_eq!(records[0].cols, 100);
        let _ = fs::remove_file(path);
    }

    #[test]
    fn checkpoint_loader_reads_gzip_sidecars() {
        let suffix = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|duration| duration.as_nanos())
            .unwrap_or(0);
        let path: PathBuf =
            std::env::temp_dir().join(format!("gzip-demo-{suffix}.checkpoints.jsonl.gz"));
        let mut parser = vt100::Parser::new(3, 12, 0);
        parser.process(b"hello");
        let record = serde_json::json!({
            "seq": 9,
            "rows": 3,
            "cols": 12,
            "state_b64": base64::engine::general_purpose::STANDARD.encode(parser.screen().state_formatted()),
        });
        let file = fs::File::create(&path).expect("create gzip checkpoint");
        let mut encoder = GzEncoder::new(file, Compression::default());
        encoder
            .write_all(format!("{record}\n").as_bytes())
            .expect("write compressed checkpoint");
        encoder.finish().expect("finish gzip checkpoint");

        let records = load_checkpoint_records(path.to_str().unwrap_or_default());
        assert_eq!(records.len(), 1);
        assert_eq!(records[0].seq, 9);
        let _ = fs::remove_file(path);
    }

    #[test]
    fn git_checkpoint_enrichment_accumulates_worktree_files_across_checkpoints() {
        let suffix = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|duration| duration.as_nanos())
            .unwrap_or(0);
        let repo_root = std::env::temp_dir().join(format!("git-checkpoint-accumulate-{suffix}"));
        fs::create_dir_all(&repo_root).expect("create temp repo root");

        let mut records = vec![
            GitCheckpointRecord {
                checkpoint_id: "chkpt-0003".to_string(),
                seq: 3,
                timestamp: 100,
                reason: "first".to_string(),
                repo_root: repo_root.to_string_lossy().into_owned(),
                branch: "main".to_string(),
                head: "base".to_string(),
                comparison_base_head: "base".to_string(),
                status_porcelain: " M agensic/cli/track.py\n".to_string(),
                status_fingerprint: String::new(),
                tracked_patch_sha256: String::new(),
                committed_diff_stat: String::new(),
                committed_files: Vec::new(),
                worktree_diff_stat: "agensic/cli/track.py | 23 ++++++\n".to_string(),
                changed_files: vec!["agensic/cli/track.py".to_string()],
                untracked_paths: Vec::new(),
                fingerprint: String::new(),
                delta_diff_stat: String::new(),
                delta_files: Vec::new(),
                delta_file_markers: BTreeMap::new(),
                cumulative_diff_stat: String::new(),
                cumulative_files: Vec::new(),
                cumulative_file_markers: BTreeMap::new(),
            },
            GitCheckpointRecord {
                checkpoint_id: "chkpt-0004".to_string(),
                seq: 4,
                timestamp: 101,
                reason: "second".to_string(),
                repo_root: repo_root.to_string_lossy().into_owned(),
                branch: "main".to_string(),
                head: "base".to_string(),
                comparison_base_head: "base".to_string(),
                status_porcelain: "?? modifications.md\n".to_string(),
                status_fingerprint: String::new(),
                tracked_patch_sha256: String::new(),
                committed_diff_stat: String::new(),
                committed_files: Vec::new(),
                worktree_diff_stat: "modifications.md | 1 +\n".to_string(),
                changed_files: vec!["modifications.md".to_string()],
                untracked_paths: vec!["modifications.md".to_string()],
                fingerprint: String::new(),
                delta_diff_stat: String::new(),
                delta_files: Vec::new(),
                delta_file_markers: BTreeMap::new(),
                cumulative_diff_stat: String::new(),
                cumulative_files: Vec::new(),
                cumulative_file_markers: BTreeMap::new(),
            },
        ];

        enrich_git_checkpoint_records(&mut records, repo_root.to_str().unwrap_or_default(), "base");

        assert_eq!(
            records[1].cumulative_files,
            vec![
                "agensic/cli/track.py".to_string(),
                "modifications.md".to_string()
            ]
        );
        assert_eq!(
            records[1]
                .cumulative_file_markers
                .get("agensic/cli/track.py")
                .map(String::as_str),
            Some("~")
        );
        assert_eq!(
            records[1]
                .cumulative_file_markers
                .get("modifications.md")
                .map(String::as_str),
            Some("+")
        );
        assert_eq!(records[1].delta_files, vec!["modifications.md".to_string()]);
        assert_eq!(
            records[1]
                .delta_file_markers
                .get("modifications.md")
                .map(String::as_str),
            Some("+")
        );
        assert!(records[1]
            .cumulative_diff_stat
            .contains("agensic/cli/track.py | 23 ++++++"));
        assert!(records[1]
            .cumulative_diff_stat
            .contains("modifications.md | 1 +"));

        let _ = fs::remove_dir_all(repo_root);
    }

    #[test]
    fn git_checkpoint_enrichment_sums_repeated_file_stats_across_checkpoints() {
        let suffix = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|duration| duration.as_nanos())
            .unwrap_or(0);
        let repo_root = std::env::temp_dir().join(format!("git-checkpoint-repeat-{suffix}"));
        fs::create_dir_all(&repo_root).expect("create temp repo root");

        let mut records = vec![
            GitCheckpointRecord {
                checkpoint_id: "chkpt-0001".to_string(),
                seq: 1,
                timestamp: 100,
                reason: "first".to_string(),
                repo_root: repo_root.to_string_lossy().into_owned(),
                branch: "main".to_string(),
                head: "base".to_string(),
                comparison_base_head: "base".to_string(),
                status_porcelain: "?? modifications.md\n".to_string(),
                status_fingerprint: String::new(),
                tracked_patch_sha256: String::new(),
                committed_diff_stat: String::new(),
                committed_files: Vec::new(),
                worktree_diff_stat: "modifications.md | 2 ++\n".to_string(),
                changed_files: vec!["modifications.md".to_string()],
                untracked_paths: vec!["modifications.md".to_string()],
                fingerprint: String::new(),
                delta_diff_stat: String::new(),
                delta_files: Vec::new(),
                delta_file_markers: BTreeMap::new(),
                cumulative_diff_stat: String::new(),
                cumulative_files: Vec::new(),
                cumulative_file_markers: BTreeMap::new(),
            },
            GitCheckpointRecord {
                checkpoint_id: "chkpt-0002".to_string(),
                seq: 2,
                timestamp: 101,
                reason: "second".to_string(),
                repo_root: repo_root.to_string_lossy().into_owned(),
                branch: "main".to_string(),
                head: "base".to_string(),
                comparison_base_head: "base".to_string(),
                status_porcelain: " M modifications.md\n".to_string(),
                status_fingerprint: String::new(),
                tracked_patch_sha256: String::new(),
                committed_diff_stat: String::new(),
                committed_files: Vec::new(),
                worktree_diff_stat: "modifications.md | 3 +++\n".to_string(),
                changed_files: vec!["modifications.md".to_string()],
                untracked_paths: Vec::new(),
                fingerprint: String::new(),
                delta_diff_stat: String::new(),
                delta_files: Vec::new(),
                delta_file_markers: BTreeMap::new(),
                cumulative_diff_stat: String::new(),
                cumulative_files: Vec::new(),
                cumulative_file_markers: BTreeMap::new(),
            },
        ];

        enrich_git_checkpoint_records(&mut records, repo_root.to_str().unwrap_or_default(), "base");

        assert!(records[0]
            .cumulative_diff_stat
            .contains("modifications.md | 2 ++"));
        assert!(records[1]
            .delta_diff_stat
            .contains("modifications.md | 3 +++"));
        assert!(records[1]
            .cumulative_diff_stat
            .contains("modifications.md | 5 +++++"));

        let _ = fs::remove_dir_all(repo_root);
    }

    #[test]
    fn git_checkpoint_enrichment_sums_committed_and_worktree_stats_within_checkpoint() {
        let suffix = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .map(|duration| duration.as_nanos())
            .unwrap_or(0);
        let repo_root = std::env::temp_dir().join(format!("git-checkpoint-mixed-{suffix}"));
        fs::create_dir_all(&repo_root).expect("create temp repo root");

        let mut records = vec![GitCheckpointRecord {
            checkpoint_id: "chkpt-0001".to_string(),
            seq: 1,
            timestamp: 100,
            reason: "mixed".to_string(),
            repo_root: repo_root.to_string_lossy().into_owned(),
            branch: "main".to_string(),
            head: "head-1".to_string(),
            comparison_base_head: "base".to_string(),
            status_porcelain: " M testing.md\n".to_string(),
            status_fingerprint: String::new(),
            tracked_patch_sha256: String::new(),
            committed_diff_stat: "testing.md | 2 ++\n".to_string(),
            committed_files: vec!["testing.md".to_string()],
            worktree_diff_stat: "testing.md | 3 +++\n".to_string(),
            changed_files: vec!["testing.md".to_string()],
            untracked_paths: Vec::new(),
            fingerprint: String::new(),
            delta_diff_stat: String::new(),
            delta_files: Vec::new(),
            delta_file_markers: BTreeMap::new(),
            cumulative_diff_stat: String::new(),
            cumulative_files: Vec::new(),
            cumulative_file_markers: BTreeMap::new(),
        }];

        enrich_git_checkpoint_records(&mut records, repo_root.to_str().unwrap_or_default(), "base");

        assert!(records[0].delta_diff_stat.contains("testing.md | 5 +++++"));
        assert!(records[0]
            .cumulative_diff_stat
            .contains("testing.md | 5 +++++"));

        let _ = fs::remove_dir_all(repo_root);
    }
}
