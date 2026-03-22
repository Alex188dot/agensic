use base64::engine::general_purpose::STANDARD as BASE64_STANDARD;
use base64::Engine;
use clap::Parser;
use flate2::read::GzDecoder;
use serde::{Deserialize, Serialize};
use std::collections::hash_map::DefaultHasher;
use std::fs::{self, File};
use std::hash::{Hash, Hasher};
use std::io::{self, BufRead, BufReader, Read, Write};
use std::path::Path;
use std::time::{Duration, Instant};

const DEFAULT_CHECKPOINT_INTERVAL_MS: u64 = 120;
const DEFAULT_CHECKPOINT_INTERVAL_EVENTS: i64 = 48;
const DEFAULT_RESIZE_SETTLE_MS: u64 = 140;

#[derive(Debug, Parser, Clone)]
#[command(name = "agensic-provenance-tui checkpoints")]
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
    pub status_porcelain: String,
    #[serde(default)]
    pub status_fingerprint: String,
    #[serde(default)]
    pub tracked_patch_sha256: String,
    #[serde(default)]
    pub worktree_diff_stat: String,
    #[serde(default)]
    pub changed_files: Vec<String>,
    #[serde(default)]
    pub untracked_paths: Vec<String>,
    #[serde(default)]
    pub fingerprint: String,
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
    records
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
        checkpoint_path_for_transcript, decode_checkpoint_state, load_checkpoint_records,
        CheckpointInputEvent, CheckpointRecorder,
    };
    use base64::Engine;
    use flate2::write::GzEncoder;
    use flate2::Compression;
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
}
