pub(crate) use crate::sessions_render::copy_button_style;

use crate::checkpoints::{
    checkpoint_path_for_transcript, decode_checkpoint_state, enrich_git_checkpoint_records,
    git_checkpoint_path_for_transcript, load_checkpoint_records, load_git_checkpoint_records,
    CheckpointRecord, GitCheckpointRecord,
};
use crate::sessions_render::{
    collapse_blank_runs, diff_stat_line, flush_terminal_span, pane_block, pane_block_title,
    push_text_block, rendered_text_height, replay_max_scroll, replay_toggle_style,
    strip_inline_progress_noise, summarize_terminal_lines, terminal_cell_style,
    terminal_replay_end_padding,
    terminal_replay_max_scroll_x as render_terminal_replay_max_scroll_x,
    terminal_replay_scroll as render_terminal_replay_scroll,
};
use base64::engine::general_purpose::STANDARD as BASE64_STANDARD;
use base64::Engine;
use chrono::TimeZone;
use clap::Parser;
use crossterm::event::{
    self, DisableMouseCapture, EnableMouseCapture, Event, KeyCode, KeyEvent, KeyEventKind,
    KeyModifiers, MouseButton, MouseEvent, MouseEventKind,
};
use crossterm::execute;
use crossterm::terminal::{
    disable_raw_mode, enable_raw_mode, size as terminal_size, EnterAlternateScreen,
    LeaveAlternateScreen,
};
use flate2::read::GzDecoder;
use ratatui::backend::CrosstermBackend;
use ratatui::layout::{Constraint, Direction, Layout, Rect};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span, Text};
use ratatui::widgets::{Block, Borders, Cell, Clear, Paragraph, Row, Table, TableState, Wrap};
use ratatui::Terminal;
use reqwest::blocking::Client;
use reqwest::Method;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::cell::RefCell;
use std::cmp::{max, min};
use std::collections::{BTreeMap, BTreeSet, HashMap};
use std::fs::{self, File};
use std::io::{self, Read, Write};
use std::path::Path;
use std::process::Command;
use std::sync::mpsc::{self, Receiver, TryRecvError};
use std::sync::Arc;
use std::thread;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use unicode_width::{UnicodeWidthChar, UnicodeWidthStr};
use vt100::Parser as VtParser;

const TIMELINE_PAGE_STEP: usize = 500;
const TEXT_REPLAY_TICK_MS: u64 = 120;
const TERMINAL_REPLAY_TICK_MS: u64 = TEXT_REPLAY_TICK_MS / 3;
const MAX_SESSION_DURATION_SECONDS: i64 = 24 * 60 * 60;
const SESSION_COPY_BUTTON: &str = "[ Copy ]";
const SESSION_COPIED_BUTTON: &str = "[   ✓   ]";
const REPLAY_FULLSCREEN_BUTTON: &str = "[fullscreen]";
const REPLAY_SPLIT_BUTTON: &str = "[split]";
const REPLAY_LOADING_FRAMES: [&str; 4] = ["[-]", "[\\]", "[|]", "[/]"];
const TIMELINE_ORDINAL_WIDTH: u16 = 6;
const TIMELINE_EVENT_TYPE_WIDTH: usize = 18;
const TIMELINE_COLUMN_SPACING: u16 = 1;
const IDLE_POLL_MS: u64 = 1_000;
const REPLAY_LOADING_POLL_MS: u64 = 120;

#[derive(Debug, Parser, Clone)]
#[command(name = "agensic-provenance-tui sessions")]
pub struct SessionsArgs {
    #[arg(long, default_value = "http://127.0.0.1:22000")]
    daemon_url: String,

    #[arg(long, default_value = "")]
    auth_token: String,

    #[arg(long, default_value = "")]
    session_id: String,

    #[arg(long, default_value_t = 200)]
    limit: usize,

    #[arg(long, default_value_t = false)]
    replay: bool,
}

#[allow(dead_code)]
#[derive(Clone, Debug, Default, Deserialize)]
struct SessionSummary {
    session_id: String,
    status: String,
    launch_mode: String,
    agent: String,
    model: String,
    agent_name: String,
    #[serde(default)]
    session_name: String,
    working_directory: String,
    root_command: String,
    transcript_path: String,
    started_at: i64,
    ended_at: i64,
    updated_at: i64,
    violation_code: String,
    exit_code: Option<i64>,
    repo_root: String,
    branch_start: String,
    branch_end: String,
    head_start: String,
    head_end: String,
    #[serde(default)]
    aggregate: Value,
    #[serde(default)]
    changes: Value,
}

#[derive(Debug, Default, Deserialize)]
struct SessionsResponse {
    #[serde(default)]
    sessions: Vec<SessionSummary>,
}

#[derive(Debug, Default, Deserialize)]
struct SessionDetailResponse {
    session: Option<SessionSummary>,
}

#[derive(Debug, Default, Serialize)]
struct SessionRenamePayload {
    session_name: String,
}

#[derive(Debug, Default, Serialize)]
struct TimeTravelPreviewPayload {
    target_seq: i64,
    target_ts: i64,
}

#[derive(Debug, Default, Serialize)]
struct TimeTravelForkPayload {
    target_seq: i64,
    branch_name: String,
}

#[derive(Clone, Debug, Default)]
struct TimeTravelHandoff {
    command: Vec<String>,
    working_directory: String,
    branch_name: String,
    session_label: String,
    replay_metadata_json: String,
}

impl TimeTravelHandoff {
    fn status_message(&self) -> String {
        if self.branch_name.trim().is_empty() {
            format!("Launching {}", self.session_label)
        } else {
            format!("Launching {} on {}", self.session_label, self.branch_name)
        }
    }
}

#[allow(dead_code)]
#[derive(Clone, Debug, Default, Deserialize)]
struct TimeTravelPreviewResponse {
    status: String,
    #[serde(default)]
    reason: String,
    #[serde(default)]
    session_id: String,
    #[serde(default)]
    target_seq: i64,
    #[serde(default)]
    resolved_checkpoint: Value,
    #[serde(default)]
    exact_match: bool,
    #[serde(default)]
    current_repo_state: Value,
    #[serde(default)]
    can_fork: bool,
    #[serde(default)]
    blocking_reason: String,
    #[serde(default)]
    suggested_branch: String,
    #[serde(default)]
    action: String,
    #[serde(default)]
    repo_root: String,
}

#[allow(dead_code)]
#[derive(Clone, Debug, Default, Deserialize)]
struct TimeTravelForkResponse {
    status: String,
    #[serde(default)]
    reason: String,
    #[serde(default)]
    branch_name: String,
    #[serde(default)]
    working_directory: String,
    #[serde(default)]
    launch_payload: Value,
}

#[derive(Debug, Default, Deserialize)]
struct ErrorDetailResponse {
    #[serde(default)]
    detail: String,
}

#[allow(dead_code)]
#[derive(Clone, Debug, Default, Deserialize)]
struct SessionEvent {
    session_id: String,
    seq: i64,
    ts_wall: f64,
    ts_monotonic_ms: i64,
    #[serde(rename = "type")]
    event_type: String,
    #[serde(default)]
    payload: Value,
}

#[derive(Debug, Default, Deserialize)]
struct SessionEventsResponse {
    #[serde(default)]
    events: Vec<SessionEvent>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum FocusPane {
    Timeline,
    Changes,
    Replay,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum ReplayMode {
    Terminal,
    Text,
}

#[derive(Clone, Debug)]
struct TimelineEntry {
    event_index: usize,
    event_start_index: usize,
    event_end_index: usize,
    seq_start: i64,
    seq_end: i64,
    ts_wall: f64,
    event_type: String,
    summary: String,
    copy_command: Option<String>,
}

#[derive(Debug, Serialize)]
struct ConversationExportRow {
    session_id: String,
    #[serde(skip_serializing_if = "String::is_empty")]
    session_name: String,
    row_index: usize,
    role: String,
    source_event_type: String,
    event_index_start: usize,
    event_index_end: usize,
    seq_start: i64,
    seq_end: i64,
    ts_iso: String,
    ts_monotonic_ms: i64,
    text: String,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum TimelineCategory {
    Command,
    Process,
    Terminal,
    Git,
    Marker,
    Violation,
    Session,
    Other,
}

impl TimelineCategory {
    fn from_event_type(event_type: &str) -> Self {
        if event_type.starts_with("terminal.") {
            Self::Terminal
        } else if event_type.starts_with("command.") {
            Self::Command
        } else if event_type.starts_with("process.") {
            Self::Process
        } else if event_type.starts_with("git.") {
            Self::Git
        } else if event_type.starts_with("marker.") {
            Self::Marker
        } else if event_type.starts_with("violation.") {
            Self::Violation
        } else if event_type.starts_with("session.") {
            Self::Session
        } else {
            Self::Other
        }
    }
}

#[derive(Clone, Debug, Default, Deserialize)]
struct TranscriptRecord {
    #[serde(default)]
    direction: String,
    #[serde(default)]
    data_b64: String,
    seq: Option<i64>,
    rows: Option<u16>,
    cols: Option<u16>,
}

#[derive(Clone, Debug, Default)]
struct TranscriptChunk {
    direction: String,
    data: Vec<u8>,
    seq: Option<i64>,
    rows: Option<u16>,
    cols: Option<u16>,
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

fn artifact_exists(path: &str) -> bool {
    artifact_candidate_paths(path)
        .into_iter()
        .any(|candidate| Path::new(&candidate).is_file())
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

fn terminal_replay_notice(transcript_path: &str, text_available: bool) -> String {
    let transcript_missing =
        !transcript_path.trim().is_empty() && !artifact_exists(transcript_path);
    match (transcript_missing, text_available) {
        (true, true) => "Terminal replay unavailable: tracked terminal artifacts expired or were pruned (kept 7 days / 1 GiB total). Showing the cleaned transcript fallback instead.".to_string(),
        (true, false) => "Terminal replay unavailable: tracked terminal artifacts expired or were pruned (kept 7 days / 1 GiB total).".to_string(),
        (false, true) => {
            "Terminal replay unavailable for this session. Showing the cleaned transcript fallback instead."
                .to_string()
        }
        (false, false) => {
            "Terminal replay unavailable for this session. The tracked terminal transcript could not be decoded."
                .to_string()
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
enum TerminalReplayCacheKey {
    RecordedGeometry,
    Viewport(u16, u16),
}

struct DetailState {
    session: SessionSummary,
    events: Vec<SessionEvent>,
    timeline_entries: Vec<TimelineEntry>,
    replay_mode: ReplayMode,
    focus: FocusPane,
    replay_fullscreen: bool,
    timeline_index: usize,
    text_replay_chunks: Vec<String>,
    text_replay_cached_step: Option<usize>,
    text_replay_cached_value: String,
    replay_timeline_indices: Vec<usize>,
    git_checkpoint_records: Vec<GitCheckpointRecord>,
    transcript_chunks: Vec<TranscriptChunk>,
    checkpoint_records: Vec<CheckpointRecord>,
    terminal_replay_frames: Arc<Vec<TerminalReplayFrame>>,
    terminal_replay_cache: HashMap<TerminalReplayCacheKey, Arc<Vec<TerminalReplayFrame>>>,
    git_change_view_cache: RefCell<HashMap<GitRangeChangeViewCacheKey, GitRangeChangeView>>,
    terminal_cache_key: Option<TerminalReplayCacheKey>,
    pending_replay_cache_key: Option<TerminalReplayCacheKey>,
    replay_cache_rx: Option<Receiver<(TerminalReplayCacheKey, Arc<Vec<TerminalReplayFrame>>)>>,
    replay_notice: Option<String>,
    replay_loading: bool,
    replay_step: usize,
    replay_text: String,
    replay_visible: bool,
    changes_scroll: u16,
    replay_scroll: u16,
    replay_scroll_x: u16,
    replay_follow_end: bool,
    autoplay: bool,
    last_tick: Instant,
}

impl DetailState {
    fn new(session: SessionSummary, events: Vec<SessionEvent>, autoplay: bool) -> Self {
        let mut git_checkpoint_records = load_git_checkpoint_records(
            &git_checkpoint_path_for_transcript(&session.transcript_path),
        );
        enrich_git_checkpoint_records(
            &mut git_checkpoint_records,
            &session.repo_root,
            &session.head_start,
        );
        let events = augment_events_with_git_checkpoints(
            events,
            &git_checkpoint_records,
            &session.session_id,
        );
        let (timeline_entries, text_replay_chunks, replay_timeline_indices) =
            build_display_model(&events);
        let transcript_chunks = load_transcript_chunks(&session.transcript_path);
        let checkpoint_records =
            load_checkpoint_records(&checkpoint_path_for_transcript(&session.transcript_path));
        let text_fallback_available = !text_replay_chunks.is_empty();
        let replay_mode = if transcript_chunks.is_empty() {
            ReplayMode::Text
        } else {
            ReplayMode::Terminal
        };
        let replay_notice = if replay_mode == ReplayMode::Text {
            Some(terminal_replay_notice(
                &session.transcript_path,
                text_fallback_available,
            ))
        } else {
            None
        };
        let initial_timeline_index = if autoplay {
            0
        } else {
            timeline_entries.len().saturating_sub(1)
        };
        let mut out = Self {
            session,
            events,
            timeline_entries,
            replay_mode,
            focus: if autoplay {
                FocusPane::Replay
            } else {
                FocusPane::Timeline
            },
            replay_fullscreen: false,
            timeline_index: initial_timeline_index,
            text_replay_chunks,
            text_replay_cached_step: None,
            text_replay_cached_value: String::new(),
            replay_timeline_indices,
            git_checkpoint_records,
            transcript_chunks,
            checkpoint_records,
            terminal_replay_frames: Arc::new(Vec::new()),
            terminal_replay_cache: HashMap::new(),
            git_change_view_cache: RefCell::new(HashMap::new()),
            terminal_cache_key: None,
            pending_replay_cache_key: None,
            replay_cache_rx: None,
            replay_notice,
            replay_loading: false,
            replay_step: 0,
            replay_text: String::new(),
            replay_visible: false,
            changes_scroll: 0,
            replay_scroll: 0,
            replay_scroll_x: 0,
            replay_follow_end: true,
            autoplay,
            last_tick: Instant::now(),
        };
        out.ensure_terminal_replay_cache();
        if out.autoplay {
            out.set_timeline_index(0);
        } else {
            out.set_timeline_index(initial_timeline_index);
        }
        out
    }

    fn active_replay_len(&self) -> usize {
        match self.replay_mode {
            ReplayMode::Terminal if !self.terminal_replay_frames.is_empty() => {
                self.terminal_replay_frames.len()
            }
            _ => self.text_replay_chunks.len(),
        }
    }

    fn current_terminal_cache_request(&self) -> Option<(TerminalReplayCacheKey, u16, u16)> {
        if self.transcript_chunks.is_empty() && self.checkpoint_records.is_empty() {
            return None;
        }
        let layout = session_detail_layout(self)?;
        let viewport_rows = layout.replay.height.saturating_sub(2).max(1);
        let viewport_cols = layout.replay.width.saturating_sub(2).max(1);
        let cache_key = if !self.checkpoint_records.is_empty()
            || transcript_has_recorded_geometry(&self.transcript_chunks)
        {
            TerminalReplayCacheKey::RecordedGeometry
        } else {
            TerminalReplayCacheKey::Viewport(viewport_rows, viewport_cols)
        };
        let (rows, cols) = self
            .checkpoint_records
            .first()
            .map(|record| (record.rows, record.cols))
            .or_else(|| first_recorded_terminal_size(&self.transcript_chunks))
            .unwrap_or((viewport_rows, viewport_cols));
        Some((cache_key, rows, cols))
    }

    fn spawn_terminal_replay_cache_build(
        &mut self,
        cache_key: TerminalReplayCacheKey,
        rows: u16,
        cols: u16,
    ) {
        let transcript_chunks = self.transcript_chunks.clone();
        let checkpoint_records = self.checkpoint_records.clone();
        let (tx, rx) = mpsc::channel();
        thread::spawn(move || {
            let frames = if checkpoint_records.is_empty() {
                build_terminal_replay_frames(&transcript_chunks, rows, cols)
            } else {
                build_terminal_replay_frames_from_checkpoints(&checkpoint_records)
            };
            let _ = tx.send((cache_key, Arc::new(frames)));
        });
        self.pending_replay_cache_key = Some(cache_key);
        self.replay_cache_rx = Some(rx);
        self.replay_loading = true;
    }

    fn poll_terminal_replay_cache(&mut self) -> bool {
        let Some(rx) = self.replay_cache_rx.as_ref() else {
            return false;
        };
        match rx.try_recv() {
            Ok((cache_key, frames)) => {
                self.terminal_replay_cache.insert(cache_key, frames);
                self.pending_replay_cache_key = None;
                self.replay_cache_rx = None;
                self.replay_loading = false;
                true
            }
            Err(TryRecvError::Empty) => false,
            Err(TryRecvError::Disconnected) => {
                self.pending_replay_cache_key = None;
                self.replay_cache_rx = None;
                self.replay_loading = false;
                true
            }
        }
    }

    fn ensure_terminal_replay_cache(&mut self) -> bool {
        let mut changed = self.poll_terminal_replay_cache();
        let Some((cache_key, rows, cols)) = self.current_terminal_cache_request() else {
            return changed;
        };
        if let Some(cached) = self.terminal_replay_cache.get(&cache_key) {
            if !Arc::ptr_eq(&self.terminal_replay_frames, cached) {
                self.terminal_replay_frames = Arc::clone(cached);
                changed = true;
            }
            if self.terminal_cache_key != Some(cache_key) {
                self.terminal_cache_key = Some(cache_key);
                changed = true;
            }
            if self.replay_loading {
                self.replay_loading = false;
                changed = true;
            }
            if self.replay_mode == ReplayMode::Terminal && self.terminal_replay_frames.is_empty() {
                self.replay_mode = ReplayMode::Text;
                changed = true;
                if self.replay_notice.is_none() {
                    self.replay_notice = Some(terminal_replay_notice(
                        &self.session.transcript_path,
                        !self.text_replay_chunks.is_empty(),
                    ));
                    changed = true;
                }
            }
            return changed;
        }
        if self.pending_replay_cache_key != Some(cache_key) {
            self.spawn_terminal_replay_cache_build(cache_key, rows, cols);
            changed = true;
        }
        if self.terminal_cache_key.take().is_some() {
            changed = true;
        }
        changed
    }

    fn replay_loading_for_active_view(&self) -> bool {
        self.replay_mode == ReplayMode::Terminal
            && self.pending_replay_cache_key.is_some()
            && self.terminal_cache_key != self.pending_replay_cache_key
    }

    fn clear_terminal_replay_caches(&mut self) {
        self.terminal_replay_frames = Arc::new(Vec::new());
        self.terminal_replay_cache.clear();
        self.terminal_cache_key = None;
        self.pending_replay_cache_key = None;
        self.replay_cache_rx = None;
        self.replay_loading = false;
    }

    fn rebuild_replay_text(&mut self, step: usize) {
        let frame_count = self.active_replay_len();
        self.replay_step = min(step, frame_count.saturating_sub(1));
        if frame_count == 0 {
            self.replay_visible = false;
            self.replay_text.clear();
            return;
        }
        self.replay_visible = true;
        self.replay_text = match self.replay_mode {
            ReplayMode::Terminal if !self.terminal_replay_frames.is_empty() => self
                .terminal_replay_frames[self.replay_step]
                .plain_text
                .clone(),
            ReplayMode::Text => self.text_replay_text_for_step(self.replay_step),
            ReplayMode::Terminal => self.text_replay_text_for_step(self.replay_step),
        };
        if self.replay_follow_end {
            self.replay_scroll = u16::MAX;
        }
        if self.replay_mode == ReplayMode::Text {
            self.replay_scroll_x = 0;
        }
    }

    fn clear_replay(&mut self) {
        self.replay_visible = false;
        self.replay_text.clear();
        self.replay_step = 0;
        if self.replay_follow_end {
            self.replay_scroll = u16::MAX;
        } else {
            self.replay_scroll = 0;
        }
        self.replay_scroll_x = 0;
    }

    fn text_replay_text_for_step(&mut self, step: usize) -> String {
        if self.text_replay_chunks.is_empty() {
            self.text_replay_cached_step = None;
            self.text_replay_cached_value.clear();
            return String::new();
        }
        let step = step.min(self.text_replay_chunks.len().saturating_sub(1));
        if self.text_replay_cached_step == Some(step) {
            return self.text_replay_cached_value.clone();
        }
        if let Some(cached_step) = self.text_replay_cached_step {
            if step == cached_step + 1 {
                self.text_replay_cached_value
                    .push_str(&self.text_replay_chunks[step]);
                self.text_replay_cached_step = Some(step);
                return self.text_replay_cached_value.clone();
            }
        }
        self.text_replay_cached_value = self.text_replay_chunks[..=step].join("");
        self.text_replay_cached_step = Some(step);
        self.text_replay_cached_value.clone()
    }

    fn text_replay_step_for_timeline(&self, timeline_index: usize) -> Option<usize> {
        self.replay_timeline_indices
            .iter()
            .rposition(|&mapped_index| mapped_index <= timeline_index)
    }

    fn terminal_replay_step_for_timeline(&self, timeline_index: usize) -> Option<usize> {
        let frame_count = self.terminal_replay_frames.len();
        if frame_count == 0 {
            return None;
        }
        let target_seq = self
            .timeline_entries
            .get(timeline_index)
            .map(|entry| entry.seq_end)
            .unwrap_or_default();
        if self
            .terminal_replay_frames
            .iter()
            .any(|frame| frame.source_seq_end.is_some())
        {
            return self
                .terminal_replay_frames
                .iter()
                .rposition(|frame| frame.source_seq_end.is_some_and(|seq| seq <= target_seq));
        }
        let start_index = self.replay_timeline_indices.first().copied().unwrap_or(0);
        let end_index = self
            .replay_timeline_indices
            .last()
            .copied()
            .unwrap_or_else(|| self.timeline_entries.len().saturating_sub(1));
        if timeline_index < start_index {
            return None;
        }
        if end_index <= start_index {
            return Some(frame_count.saturating_sub(1));
        }
        let progress = timeline_index.saturating_sub(start_index);
        let span = end_index.saturating_sub(start_index);
        let scaled = progress
            .saturating_mul(frame_count.saturating_sub(1))
            .saturating_add(span / 2)
            / span.max(1);
        Some(scaled.min(frame_count.saturating_sub(1)))
    }

    fn sync_replay_to_timeline(&mut self) {
        if self.active_replay_len() == 0 {
            self.clear_replay();
            return;
        }
        let next_step = match self.replay_mode {
            ReplayMode::Text => self.text_replay_step_for_timeline(self.timeline_index),
            ReplayMode::Terminal => self.terminal_replay_step_for_timeline(self.timeline_index),
        };
        if let Some(step) = next_step {
            self.rebuild_replay_text(step);
        } else {
            self.clear_replay();
        }
    }

    fn advance_autoplay(&mut self) -> bool {
        let mut changed = false;
        if !self.autoplay || self.timeline_entries.is_empty() {
            return changed;
        }
        let tick_ms = match self.replay_mode {
            ReplayMode::Terminal => TERMINAL_REPLAY_TICK_MS,
            ReplayMode::Text => TEXT_REPLAY_TICK_MS,
        };
        if self.last_tick.elapsed() < Duration::from_millis(tick_ms) {
            return changed;
        }
        self.last_tick = Instant::now();
        if self.timeline_index + 1 < self.timeline_entries.len() {
            self.move_selection(1);
            changed = true;
        } else {
            self.autoplay = false;
            changed = true;
        }
        changed
    }

    fn set_timeline_index(&mut self, index: usize) {
        if self.timeline_entries.is_empty() {
            self.timeline_index = 0;
            self.sync_replay_to_timeline();
            return;
        }
        self.timeline_index = index.min(self.timeline_entries.len().saturating_sub(1));
        self.sync_replay_to_timeline();
    }

    fn move_selection(&mut self, delta: isize) {
        if self.timeline_entries.is_empty() {
            self.timeline_index = 0;
            self.sync_replay_to_timeline();
            return;
        }
        let total = self.timeline_entries.len() as isize;
        let next_visible = (self.timeline_index as isize + delta).rem_euclid(total) as usize;
        self.set_timeline_index(next_visible);
    }

    fn page_selection(&mut self, delta: isize) {
        if self.timeline_entries.is_empty() {
            self.timeline_index = 0;
            self.sync_replay_to_timeline();
            return;
        }
        let next_visible = (self.timeline_index as isize
            + delta.saturating_mul(TIMELINE_PAGE_STEP as isize))
        .clamp(0, self.timeline_entries.len().saturating_sub(1) as isize)
            as usize;
        self.set_timeline_index(next_visible);
    }

    fn selected_timeline_entry(&self) -> Option<&TimelineEntry> {
        self.timeline_entries.get(self.timeline_index)
    }

    fn selected_event(&self) -> Option<&SessionEvent> {
        self.selected_timeline_entry()
            .and_then(|entry| self.events.get(entry.event_index))
    }

    fn selected_git_checkpoint(&self) -> Option<&GitCheckpointRecord> {
        let target_seq = self
            .selected_timeline_entry()
            .map(|entry| entry.seq_end.max(entry.seq_start))
            .unwrap_or_default();
        self.git_checkpoint_records
            .iter()
            .rposition(|record| record.seq <= target_seq)
            .and_then(|idx| self.git_checkpoint_records.get(idx))
    }

    fn cycle_focus(&mut self) {
        self.focus = match self.focus {
            FocusPane::Timeline => FocusPane::Changes,
            FocusPane::Changes => FocusPane::Replay,
            FocusPane::Replay => FocusPane::Timeline,
        };
    }

    fn toggle_replay_fullscreen(&mut self) {
        self.replay_fullscreen = !self.replay_fullscreen;
        self.focus = FocusPane::Replay;
        self.ensure_terminal_replay_cache();
    }

    fn scroll_changes_by(&mut self, delta: i32, area: Rect) {
        let max_scroll = changes_max_scroll(self, area);
        if delta < 0 {
            self.changes_scroll = self
                .changes_scroll
                .saturating_sub(delta.unsigned_abs() as u16);
        } else {
            self.changes_scroll = self.changes_scroll.saturating_add(delta as u16);
        }
        self.changes_scroll = self.changes_scroll.min(max_scroll);
    }

    fn scroll_replay_horizontal(&mut self, delta: i32) {
        if delta < 0 {
            self.replay_scroll_x = self
                .replay_scroll_x
                .saturating_sub(delta.unsigned_abs() as u16);
        } else {
            self.replay_scroll_x = self.replay_scroll_x.saturating_add(delta as u16);
        }
    }
}

struct App {
    client: Client,
    args: SessionsArgs,
    all_sessions: Vec<SessionSummary>,
    sessions: Vec<SessionSummary>,
    selected: usize,
    filter_menu: bool,
    filter_cursor: usize,
    filters: SessionFilters,
    detail: Option<DetailState>,
    status: String,
    flash_status: Option<crate::FlashStatus>,
    deep_link: bool,
    needs_terminal_clear: bool,
    event_modal_open: bool,
    event_modal_scroll: u16,
    hovered_timeline_copy_row: Option<usize>,
    hovered_event_modal_copy: bool,
    hovered_header_copy: bool,
    hovered_replay_toggle: bool,
    copy_feedback: Option<CopyFeedback>,
    rename_modal: Option<RenameModalState>,
    delete_modal: Option<DeleteModalState>,
    time_travel_modal: Option<TimeTravelModalState>,
    pending_time_travel_handoff: Option<TimeTravelHandoff>,
}

#[derive(Clone, Debug, Default)]
struct TerminalReplayFrame {
    plain_text: String,
    lines: Vec<Line<'static>>,
    source_seq_end: Option<i64>,
    cursor_row: u16,
    last_content_row: u16,
    rows: u16,
    cols: u16,
}

#[derive(Clone, Debug, Default)]
struct EventModalContent {
    lines: Vec<Line<'static>>,
    command_line_index: Option<u16>,
    command: Option<String>,
}

#[derive(Clone, Copy, Debug)]
struct TimelineViewport {
    start: usize,
    end: usize,
}

#[derive(Clone, Copy, Debug)]
struct TimelineTableLayout {
    ordinal_width: u16,
    kind_width: u16,
    preview_width: u16,
    action_width: u16,
    spacing: u16,
}

#[derive(Clone, Debug)]
struct CopyFeedback {
    target: CopyFeedbackTarget,
    expires_at: Instant,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum CopyFeedbackTarget {
    TimelineRow(usize),
    EventModalCommand(i64),
    SessionId,
}

#[derive(Clone, Debug, Default)]
struct RenameModalState {
    session_id: String,
    input: String,
}

#[derive(Clone, Debug, Default)]
struct DeleteModalState {
    session_id: String,
    session_name: String,
}

#[derive(Clone, Debug, Default)]
struct TimeTravelModalState {
    session_id: String,
    target_seq: i64,
    event_summary: String,
    preview: Option<TimeTravelPreviewResponse>,
    error: String,
}

#[derive(Clone, Debug, Default)]
struct SessionFilters {
    status: String,
    agent: String,
    model: String,
    repo: String,
    branch: String,
}

impl CopyFeedback {
    fn active_target(&self) -> Option<CopyFeedbackTarget> {
        (Instant::now() <= self.expires_at).then_some(self.target)
    }
}

impl App {
    fn next_poll_timeout(&self) -> Duration {
        let now = Instant::now();
        let mut timeout = Duration::from_millis(IDLE_POLL_MS);
        if let Some(detail) = self.detail.as_ref() {
            if detail.autoplay {
                let tick_ms = match detail.replay_mode {
                    ReplayMode::Terminal => TERMINAL_REPLAY_TICK_MS,
                    ReplayMode::Text => TEXT_REPLAY_TICK_MS,
                };
                let next_tick = detail.last_tick + Duration::from_millis(tick_ms);
                timeout = timeout.min(
                    next_tick
                        .checked_duration_since(now)
                        .unwrap_or(Duration::from_millis(0)),
                );
            }
            if detail.replay_loading {
                timeout = timeout.min(Duration::from_millis(REPLAY_LOADING_POLL_MS));
            }
        }
        if let Some(remaining) = self
            .flash_status
            .as_ref()
            .and_then(crate::FlashStatus::remaining_duration)
        {
            timeout = timeout.min(remaining);
        }
        if let Some(remaining) = self
            .copy_feedback
            .as_ref()
            .and_then(|feedback| feedback.expires_at.checked_duration_since(now))
        {
            timeout = timeout.min(remaining);
        }
        timeout
    }

    fn has_timed_redraws(&self) -> bool {
        self.detail
            .as_ref()
            .map(|detail| detail.replay_loading)
            .unwrap_or(false)
            || self
                .flash_status
                .as_ref()
                .is_some_and(|status| status.active_message().is_some())
            || self.active_copy_feedback().is_some()
    }

    fn format_http_error(response: reqwest::blocking::Response, context: &str) -> String {
        let status = response.status();
        let detail = response
            .json::<ErrorDetailResponse>()
            .ok()
            .map(|body| body.detail.trim().to_string())
            .filter(|body| !body.is_empty());
        match detail {
            Some(detail) => format!("{context}: {status} ({detail})"),
            None => format!("{context}: {status}"),
        }
    }

    fn new(client: Client, args: SessionsArgs) -> Result<Self, String> {
        let mut app = Self {
            client,
            args: args.clone(),
            all_sessions: Vec::new(),
            sessions: Vec::new(),
            selected: 0,
            filter_menu: false,
            filter_cursor: 0,
            filters: SessionFilters::default(),
            detail: None,
            status: "Ready".to_string(),
            flash_status: None,
            deep_link: !args.session_id.trim().is_empty(),
            needs_terminal_clear: true,
            event_modal_open: false,
            event_modal_scroll: 0,
            hovered_timeline_copy_row: None,
            hovered_event_modal_copy: false,
            hovered_header_copy: false,
            hovered_replay_toggle: false,
            copy_feedback: None,
            rename_modal: None,
            delete_modal: None,
            time_travel_modal: None,
            pending_time_travel_handoff: None,
        };
        app.refresh_sessions()?;
        if !args.session_id.trim().is_empty() {
            app.open_session(args.session_id.trim(), args.replay)?;
        }
        Ok(app)
    }

    fn request_with_method(&self, method: Method, path: &str) -> reqwest::blocking::RequestBuilder {
        let url = format!(
            "{}/{}",
            self.args.daemon_url.trim_end_matches('/'),
            path.trim_start_matches('/')
        );
        let builder = self.client.request(method, url);
        if self.args.auth_token.trim().is_empty() {
            builder
        } else {
            builder.bearer_auth(self.args.auth_token.trim())
        }
    }

    fn request(&self, path: &str) -> reqwest::blocking::RequestBuilder {
        self.request_with_method(Method::GET, path)
    }

    fn refresh_sessions(&mut self) -> Result<(), String> {
        let response = self
            .request(&format!(
                "/sessions?limit={}",
                max(1, min(500, self.args.limit))
            ))
            .send()
            .map_err(|err| format!("sessions request failed: {}", err))?;
        if response.status().as_u16() != 200 {
            return Err(format!("sessions request failed: {}", response.status()));
        }
        let payload: SessionsResponse = response
            .json()
            .map_err(|err| format!("invalid sessions payload: {}", err))?;
        self.all_sessions = payload.sessions;
        self.apply_session_filters();
        self.status = format!(
            "Loaded {} sessions (showing {})",
            self.all_sessions.len(),
            self.sessions.len()
        );
        Ok(())
    }

    fn filter_fields() -> [&'static str; 6] {
        ["status", "agent", "model", "repo", "branch", "[Reset All]"]
    }

    fn session_actor(session: &SessionSummary) -> String {
        if session.agent_name.trim().is_empty() {
            session.agent.clone()
        } else {
            session.agent_name.clone()
        }
    }

    fn session_repo(session: &SessionSummary) -> String {
        repo_display_name(&session.repo_root)
    }

    fn session_branch(session: &SessionSummary) -> String {
        if session.branch_end.trim().is_empty() {
            session.branch_start.clone()
        } else {
            session.branch_end.clone()
        }
    }

    fn field_values(&self, field: &str) -> Vec<String> {
        match field {
            "status" => unique_values(
                self.all_sessions
                    .iter()
                    .map(|session| session.status.clone()),
            ),
            "agent" => unique_values(self.all_sessions.iter().map(Self::session_actor)),
            "model" => unique_values(
                self.all_sessions
                    .iter()
                    .map(|session| session.model.clone()),
            ),
            "repo" => unique_values(self.all_sessions.iter().map(Self::session_repo)),
            "branch" => unique_values(self.all_sessions.iter().map(Self::session_branch)),
            "[Reset All]" => vec!["<Press Left/Right/Enter to Reset>".to_string()],
            _ => vec!["".to_string()],
        }
    }

    fn field_current(&self, field: &str) -> String {
        match field {
            "status" => self.filters.status.clone(),
            "agent" => self.filters.agent.clone(),
            "model" => self.filters.model.clone(),
            "repo" => self.filters.repo.clone(),
            "branch" => self.filters.branch.clone(),
            "[Reset All]" => "<Press Left/Right/Enter to Reset>".to_string(),
            _ => String::new(),
        }
    }

    fn session_passes_filters(&self, session: &SessionSummary) -> bool {
        if !self.filters.status.is_empty() && session.status != self.filters.status {
            return false;
        }
        if !self.filters.agent.is_empty() && Self::session_actor(session) != self.filters.agent {
            return false;
        }
        if !self.filters.model.is_empty() && session.model != self.filters.model {
            return false;
        }
        if !self.filters.repo.is_empty() && Self::session_repo(session) != self.filters.repo {
            return false;
        }
        if !self.filters.branch.is_empty() && Self::session_branch(session) != self.filters.branch {
            return false;
        }
        true
    }

    fn apply_session_filters(&mut self) {
        self.sessions = self
            .all_sessions
            .iter()
            .filter(|session| self.session_passes_filters(session))
            .cloned()
            .collect();
        if self.selected >= self.sessions.len() && !self.sessions.is_empty() {
            self.selected = self.sessions.len() - 1;
        }
        if self.sessions.is_empty() {
            self.selected = 0;
        }
    }

    fn set_field_current(&mut self, field: &str, value: String) {
        match field {
            "status" => self.filters.status = value,
            "agent" => self.filters.agent = value,
            "model" => self.filters.model = value,
            "repo" => self.filters.repo = value,
            "branch" => self.filters.branch = value,
            "[Reset All]" => self.filters = SessionFilters::default(),
            _ => {}
        }
        self.apply_session_filters();
    }

    fn cycle_filter_value(&mut self, step: isize) {
        let field = Self::filter_fields()[self.filter_cursor];
        let values = self.field_values(field);
        if values.is_empty() {
            return;
        }
        let current = self.field_current(field);
        let pos = values
            .iter()
            .position(|value| value == &current)
            .unwrap_or(0) as isize;
        let len = values.len() as isize;
        let next = (pos + step).rem_euclid(len) as usize;
        self.set_field_current(field, values[next].clone());
    }

    fn open_selected(&mut self) -> Result<(), String> {
        if let Some(session) = self.sessions.get(self.selected) {
            return self.open_session(&session.session_id.clone(), false);
        }
        Ok(())
    }

    fn open_session(&mut self, session_id: &str, replay: bool) -> Result<(), String> {
        let session_response = self
            .request(&format!("/sessions/{}", session_id))
            .send()
            .map_err(|err| format!("session detail request failed: {}", err))?;
        if session_response.status().as_u16() != 200 {
            return Err(format!(
                "session detail request failed: {}",
                session_response.status()
            ));
        }
        let session_payload: SessionDetailResponse = session_response
            .json()
            .map_err(|err| format!("invalid session detail payload: {}", err))?;
        let session = session_payload
            .session
            .ok_or_else(|| "session detail payload missing session".to_string())?;

        let events_response = self
            .request(&format!("/sessions/{}/events", session_id))
            .send()
            .map_err(|err| format!("session events request failed: {}", err))?;
        if events_response.status().as_u16() != 200 {
            return Err(format!(
                "session events request failed: {}",
                events_response.status()
            ));
        }
        let events_payload: SessionEventsResponse = events_response
            .json()
            .map_err(|err| format!("invalid session events payload: {}", err))?;

        let detail = DetailState::new(session, events_payload.events, replay);
        self.status = if detail.replay_notice.is_some() {
            if detail.text_replay_chunks.is_empty() {
                "Terminal replay unavailable for this session.".to_string()
            } else {
                "Terminal replay unavailable for this session; using the transcript fallback."
                    .to_string()
            }
        } else {
            format!("Opened session {}", session_id)
        };
        self.detail = Some(detail);
        self.event_modal_open = false;
        self.event_modal_scroll = 0;
        self.hovered_timeline_copy_row = None;
        self.hovered_event_modal_copy = false;
        self.hovered_header_copy = false;
        self.hovered_replay_toggle = false;
        self.copy_feedback = None;
        self.rename_modal = None;
        self.delete_modal = None;
        self.time_travel_modal = None;
        self.needs_terminal_clear = true;
        Ok(())
    }

    fn status_text(&self) -> &str {
        self.flash_status
            .as_ref()
            .and_then(crate::FlashStatus::active_message)
            .unwrap_or(self.status.as_str())
    }

    fn set_flash(&mut self, message: impl Into<String>) {
        self.flash_status = Some(crate::FlashStatus::new(message));
    }

    fn active_copy_feedback(&self) -> Option<CopyFeedbackTarget> {
        self.copy_feedback
            .as_ref()
            .and_then(CopyFeedback::active_target)
    }

    fn set_copy_feedback(&mut self, target: CopyFeedbackTarget) {
        self.copy_feedback = Some(CopyFeedback {
            target,
            expires_at: Instant::now() + crate::COPY_FLASH_DURATION,
        });
    }

    fn timeline_copy_hovered(&self, row_index: usize) -> bool {
        self.hovered_timeline_copy_row == Some(row_index)
    }

    fn timeline_copy_copied(&self, row_index: usize) -> bool {
        self.active_copy_feedback() == Some(CopyFeedbackTarget::TimelineRow(row_index))
    }

    fn event_modal_copy_copied(&self, event_seq: i64) -> bool {
        self.active_copy_feedback() == Some(CopyFeedbackTarget::EventModalCommand(event_seq))
    }

    fn header_copy_copied(&self) -> bool {
        self.active_copy_feedback() == Some(CopyFeedbackTarget::SessionId)
    }

    fn export_conversation_jsonl(&mut self) -> Result<(String, usize), String> {
        let detail = self
            .detail
            .as_ref()
            .ok_or_else(|| "No open session".to_string())?;
        let out = default_conversation_export_path(&detail.session.session_id, "jsonl");
        let count = export_conversation_jsonl(detail, &out)?;
        Ok((out, count))
    }

    fn export_timeline_csv(&mut self) -> Result<String, String> {
        let detail = self
            .detail
            .as_ref()
            .ok_or_else(|| "No open session".to_string())?;
        let out = default_timeline_export_path(&detail.session.session_id, "csv");
        export_timeline_rows(detail, &out)?;
        Ok(out)
    }

    fn selected_session_id(&self) -> Option<String> {
        self.sessions
            .get(self.selected)
            .map(|session| session.session_id.clone())
    }

    fn select_session_id(&mut self, session_id: &str) {
        if let Some(index) = self
            .sessions
            .iter()
            .position(|session| session.session_id == session_id)
        {
            self.selected = index;
        }
    }

    fn open_rename_modal(&mut self, session_id: String, current_name: String) {
        self.rename_modal = Some(RenameModalState {
            session_id,
            input: current_name,
        });
        self.delete_modal = None;
        self.time_travel_modal = None;
        self.event_modal_open = false;
        self.event_modal_scroll = 0;
    }

    fn start_rename_selected(&mut self) {
        if let Some(session) = self.sessions.get(self.selected) {
            self.open_rename_modal(session.session_id.clone(), session.session_name.clone());
        }
    }

    fn open_delete_modal(&mut self, session_id: String, session_name: String) {
        self.delete_modal = Some(DeleteModalState {
            session_id,
            session_name,
        });
        self.rename_modal = None;
        self.event_modal_open = false;
        self.event_modal_scroll = 0;
        self.time_travel_modal = None;
    }

    fn start_delete_selected(&mut self) {
        if let Some(session) = self.sessions.get(self.selected) {
            self.open_delete_modal(session.session_id.clone(), session.session_name.clone());
        }
    }

    fn start_delete_detail(&mut self) {
        if let Some(detail) = self.detail.as_ref() {
            self.open_delete_modal(
                detail.session.session_id.clone(),
                detail.session.session_name.clone(),
            );
        }
    }

    fn open_time_travel_modal(&mut self) {
        let Some(detail) = self.detail.as_ref() else {
            return;
        };
        let Some(entry) = detail.selected_timeline_entry() else {
            self.set_flash("No timeline event selected");
            return;
        };
        let target_seq = entry.seq_end.max(entry.seq_start);
        let preview = self
            .request_with_method(
                Method::POST,
                &format!(
                    "/sessions/{}/time-travel/preview",
                    detail.session.session_id
                ),
            )
            .json(&TimeTravelPreviewPayload {
                target_seq,
                target_ts: entry.ts_wall as i64,
            })
            .send();
        let mut modal = TimeTravelModalState {
            session_id: detail.session.session_id.clone(),
            target_seq,
            event_summary: entry.summary.clone(),
            preview: None,
            error: String::new(),
        };
        match preview {
            Ok(response) if response.status().as_u16() == 200 => {
                match response.json::<TimeTravelPreviewResponse>() {
                    Ok(body) => modal.preview = Some(body),
                    Err(err) => modal.error = format!("invalid preview payload: {}", err),
                }
            }
            Ok(response) => modal.error = Self::format_http_error(response, "preview failed"),
            Err(err) => modal.error = format!("preview request failed: {}", err),
        }
        self.rename_modal = None;
        self.delete_modal = None;
        self.event_modal_open = false;
        self.event_modal_scroll = 0;
        self.time_travel_modal = Some(modal);
    }

    fn submit_time_travel(&mut self) -> Result<TimeTravelHandoff, String> {
        let modal = self
            .time_travel_modal
            .clone()
            .ok_or_else(|| "Time Travel modal is not open".to_string())?;
        if !modal.error.trim().is_empty() {
            return Err(modal.error.clone());
        }
        let preview = modal
            .preview
            .clone()
            .ok_or_else(|| "Time Travel preview unavailable".to_string())?;
        if !preview.can_fork {
            return Err(if preview.blocking_reason.trim().is_empty() {
                "Time Travel is blocked for this repo state".to_string()
            } else {
                format!("Time Travel blocked: {}", preview.blocking_reason)
            });
        }
        let fork = self
            .request_with_method(
                Method::POST,
                &format!("/sessions/{}/time-travel/fork", modal.session_id),
            )
            .json(&TimeTravelForkPayload {
                target_seq: modal.target_seq,
                branch_name: String::new(),
            })
            .send()
            .map_err(|err| format!("fork request failed: {}", err))?;
        if fork.status().as_u16() != 200 {
            return Err(format!("fork failed: {}", fork.status()));
        }
        let fork_payload: TimeTravelForkResponse = fork
            .json()
            .map_err(|err| format!("invalid fork payload: {}", err))?;
        let launch_payload = fork_payload.launch_payload.clone();
        let working_directory = launch_payload
            .get("working_directory")
            .and_then(Value::as_str)
            .unwrap_or(&fork_payload.working_directory)
            .to_string();
        let command = launch_payload
            .get("launch_command")
            .and_then(Value::as_array)
            .map(|items| {
                items
                    .iter()
                    .filter_map(Value::as_str)
                    .map(|item| item.to_string())
                    .collect::<Vec<_>>()
            })
            .unwrap_or_default();
        if command.is_empty() {
            return Err("time travel launch command missing".to_string());
        }
        let session_label = launch_payload
            .get("resolved_agent")
            .and_then(Value::as_str)
            .filter(|value| !value.trim().is_empty())
            .or_else(|| launch_payload.get("agent").and_then(Value::as_str))
            .unwrap_or("agent")
            .to_string();
        let replay_metadata_json = serde_json::to_string(&json!({
            "source_session_id": launch_payload
                .get("source_session_id")
                .and_then(Value::as_str)
                .unwrap_or_default(),
            "target_seq": launch_payload
                .get("source_target_seq")
                .and_then(Value::as_i64)
                .unwrap_or_default(),
            "resolved_checkpoint_seq": launch_payload
                .get("resolved_checkpoint_seq")
                .and_then(Value::as_i64)
                .unwrap_or_default(),
            "fork_branch": if fork_payload.branch_name.trim().is_empty() {
                launch_payload
                    .get("branch_name")
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .to_string()
            } else {
                fork_payload.branch_name.clone()
            },
        }))
        .unwrap_or_default();
        self.time_travel_modal = None;
        Ok(TimeTravelHandoff {
            command,
            working_directory,
            branch_name: fork_payload.branch_name,
            session_label,
            replay_metadata_json,
        })
    }

    fn submit_rename(&mut self) -> Result<(), String> {
        let modal = self
            .rename_modal
            .clone()
            .ok_or_else(|| "Rename modal is not open".to_string())?;
        let selected_before = self.selected_session_id().unwrap_or_default();
        let response = self
            .request_with_method(Method::PATCH, &format!("/sessions/{}", modal.session_id))
            .json(&SessionRenamePayload {
                session_name: modal.input.trim().to_string(),
            })
            .send()
            .map_err(|err| format!("session rename request failed: {}", err))?;
        if response.status().as_u16() == 405 {
            return Err(
                "Session rename is unavailable because the running Agensic daemon is outdated. Restart Agensic and try again."
                    .to_string(),
            );
        }
        if response.status().as_u16() != 200 {
            return Err(format!(
                "session rename request failed: {}",
                response.status()
            ));
        }
        let payload: SessionDetailResponse = response
            .json()
            .map_err(|err| format!("invalid session rename payload: {}", err))?;
        let updated = payload
            .session
            .ok_or_else(|| "session rename payload missing session".to_string())?;
        self.refresh_sessions()?;
        if !selected_before.trim().is_empty() {
            self.select_session_id(&selected_before);
        }
        self.select_session_id(&updated.session_id);
        if let Some(detail) = self.detail.as_mut() {
            if detail.session.session_id == updated.session_id {
                detail.session.session_name = updated.session_name.clone();
            }
        }
        self.rename_modal = None;
        self.status = format!("Renamed session {}", updated.session_id);
        Ok(())
    }

    fn submit_delete(&mut self) -> Result<(), String> {
        let modal = self
            .delete_modal
            .clone()
            .ok_or_else(|| "Delete modal is not open".to_string())?;
        let selected_before = self.selected_session_id().unwrap_or_default();
        let deleted_from_detail = self
            .detail
            .as_ref()
            .map(|detail| detail.session.session_id == modal.session_id)
            .unwrap_or(false);
        let response = self
            .request_with_method(Method::DELETE, &format!("/sessions/{}", modal.session_id))
            .send()
            .map_err(|err| format!("session delete request failed: {}", err))?;
        if response.status().as_u16() == 405 {
            return Err(
                "Session deletion is unavailable because the running Agensic daemon is outdated. Restart Agensic and try again."
                    .to_string(),
            );
        }
        if response.status().as_u16() != 200 {
            return Err(format!(
                "session delete request failed: {}",
                response.status()
            ));
        }
        self.refresh_sessions()?;
        if !selected_before.trim().is_empty() && selected_before != modal.session_id {
            self.select_session_id(&selected_before);
        }
        if deleted_from_detail {
            if let Some(detail) = self.detail.as_mut() {
                detail.clear_terminal_replay_caches();
            }
            self.detail = None;
            self.needs_terminal_clear = true;
        }
        self.delete_modal = None;
        self.status = format!("Deleted session {}", modal.session_id);
        Ok(())
    }
}

pub fn run_from_env(argv: &[String]) -> Result<(), String> {
    let args = SessionsArgs::parse_from(
        std::iter::once("sessions".to_string()).chain(argv.iter().cloned()),
    );
    let client = Client::builder()
        .timeout(Duration::from_secs(8))
        .build()
        .map_err(|err| format!("failed to build HTTP client: {}", err))?;
    run(client, args)
}

fn run(client: Client, args: SessionsArgs) -> Result<(), String> {
    enable_raw_mode().map_err(|e| format!("enable raw mode failed: {}", e))?;
    let mut stdout = io::stdout();
    execute!(stdout, EnterAlternateScreen, EnableMouseCapture)
        .map_err(|e| format!("enter alt screen failed: {}", e))?;
    let backend = CrosstermBackend::new(stdout);
    let mut terminal =
        Terminal::new(backend).map_err(|e| format!("terminal init failed: {}", e))?;
    let mut app = App::new(client, args)?;
    let mut needs_draw = true;

    loop {
        let timeout = app.next_poll_timeout();
        let timed_redraws_active = app.has_timed_redraws();
        let poll_timed_out = !event::poll(timeout).map_err(|e| format!("poll failed: {}", e))?;
        let mut state_changed = false;
        if app.needs_terminal_clear {
            terminal
                .clear()
                .map_err(|e| format!("terminal clear failed: {}", e))?;
            app.needs_terminal_clear = false;
            state_changed = true;
        }
        if let Some(detail) = app.detail.as_mut() {
            state_changed |= detail.ensure_terminal_replay_cache();
            state_changed |= detail.advance_autoplay();
        }
        if !poll_timed_out {
            match event::read().map_err(|e| format!("read failed: {}", e))? {
                Event::Key(key) if key.kind == KeyEventKind::Press => {
                    if handle_key(&mut app, key)? {
                        break;
                    }
                    state_changed = true;
                }
                Event::Mouse(mouse) => {
                    handle_mouse(&mut app, mouse);
                    state_changed = true;
                }
                _ => {}
            }
        } else if timed_redraws_active {
            state_changed = true;
        }
        if needs_draw || state_changed {
            terminal
                .draw(|frame| draw_ui(frame, &app))
                .map_err(|e| format!("draw failed: {}", e))?;
            needs_draw = false;
        }
    }

    let pending_handoff = app.pending_time_travel_handoff.clone();

    disable_raw_mode().map_err(|e| format!("disable raw mode failed: {}", e))?;
    execute!(
        terminal.backend_mut(),
        DisableMouseCapture,
        LeaveAlternateScreen
    )
    .map_err(|e| format!("leave alt screen failed: {}", e))?;
    terminal
        .show_cursor()
        .map_err(|e| format!("show cursor failed: {}", e))?;
    io::stdout()
        .flush()
        .map_err(|e| format!("stdout flush failed: {}", e))?;
    crate::flush_stdin_input_buffer();
    if let Some(handoff) = pending_handoff {
        let mut command = Command::new(
            handoff
                .command
                .first()
                .ok_or_else(|| "handoff command missing executable".to_string())?,
        );
        if handoff.command.len() > 1 {
            command.args(&handoff.command[1..]);
        }
        if !handoff.working_directory.trim().is_empty() {
            command.current_dir(handoff.working_directory.trim());
        }
        if !handoff.replay_metadata_json.trim().is_empty() {
            command.env(
                "AGENSIC_TIME_TRAVEL_REPLAY_METADATA",
                handoff.replay_metadata_json.trim(),
            );
        }
        let status = command
            .status()
            .map_err(|e| format!("time travel handoff failed: {}", e))?;
        if !status.success() {
            return Err(format!("time travel handoff exited with {}", status));
        }
    }
    Ok(())
}

fn handle_key(app: &mut App, key: KeyEvent) -> Result<bool, String> {
    if key.modifiers.contains(KeyModifiers::CONTROL) && matches!(key.code, KeyCode::Char('c')) {
        if let Some(detail) = app.detail.as_mut() {
            detail.clear_terminal_replay_caches();
        }
        app.detail = None;
        return Ok(true);
    }
    if app.rename_modal.is_some() {
        match key.code {
            KeyCode::Esc => {
                app.rename_modal = None;
                app.status = "Rename cancelled".to_string();
            }
            KeyCode::Enter => {
                if let Err(err) = app.submit_rename() {
                    app.set_flash(err);
                }
            }
            KeyCode::Backspace | KeyCode::Delete => {
                if let Some(modal) = app.rename_modal.as_mut() {
                    modal.input.pop();
                }
            }
            KeyCode::Char(c) => {
                if let Some(modal) = app.rename_modal.as_mut() {
                    modal.input.push(c);
                }
            }
            _ => {}
        }
        return Ok(false);
    }
    if app.delete_modal.is_some() {
        match key.code {
            KeyCode::Esc => {
                app.delete_modal = None;
                app.status = "Delete cancelled".to_string();
            }
            KeyCode::Enter => {
                if let Err(err) = app.submit_delete() {
                    app.set_flash(err);
                }
            }
            _ => {}
        }
        return Ok(false);
    }
    if app.time_travel_modal.is_some() {
        match key.code {
            KeyCode::Esc => {
                app.time_travel_modal = None;
                app.status = "Time Travel cancelled".to_string();
            }
            KeyCode::Enter => match app.submit_time_travel() {
                Ok(handoff) => {
                    app.status = handoff.status_message();
                    app.pending_time_travel_handoff = Some(handoff);
                    return Ok(true);
                }
                Err(err) => app.set_flash(err),
            },
            _ => {}
        }
        return Ok(false);
    }
    if app.detail.is_some() {
        let mut flash_message: Option<String> = None;
        let mut copy_target: Option<CopyFeedbackTarget> = None;
        let mut export_conversation = false;
        let mut export_timeline = false;
        let mut close_detail = false;
        {
            let detail = app.detail.as_mut().expect("detail checked above");
            if app.event_modal_open {
                match key.code {
                    KeyCode::Esc | KeyCode::Enter => {
                        app.event_modal_open = false;
                        app.event_modal_scroll = 0;
                    }
                    KeyCode::Char('c') => {
                        if let Some((command, event_seq)) =
                            detail.selected_event().and_then(|event| {
                                event_command(event).map(|command| (command, event.seq))
                            })
                        {
                            match crate::copy_to_clipboard(&command) {
                                Ok(()) => {
                                    flash_message = Some("✓ Copied command".to_string());
                                    copy_target =
                                        Some(CopyFeedbackTarget::EventModalCommand(event_seq));
                                }
                                Err(err) => flash_message = Some(err),
                            }
                        } else {
                            flash_message = Some("No command to copy".to_string());
                        }
                    }
                    KeyCode::Up | KeyCode::Char('k') => {
                        app.event_modal_scroll = app.event_modal_scroll.saturating_sub(1);
                    }
                    KeyCode::Down | KeyCode::Char('j') => {
                        app.event_modal_scroll = app.event_modal_scroll.saturating_add(1);
                    }
                    KeyCode::PageUp => {
                        app.event_modal_scroll = app.event_modal_scroll.saturating_sub(10);
                    }
                    KeyCode::PageDown => {
                        app.event_modal_scroll = app.event_modal_scroll.saturating_add(10);
                    }
                    KeyCode::Home => app.event_modal_scroll = 0,
                    _ => {}
                }
            } else {
                match key.code {
                    KeyCode::Esc => {
                        if app.deep_link {
                            detail.clear_terminal_replay_caches();
                            return Ok(true);
                        }
                        detail.clear_terminal_replay_caches();
                        close_detail = true;
                    }
                    KeyCode::Char('s') => detail.cycle_focus(),
                    KeyCode::Char('f') => {
                        detail.toggle_replay_fullscreen();
                        app.status = if detail.replay_fullscreen {
                            "Replay fullscreen enabled".to_string()
                        } else {
                            "Replay split view restored".to_string()
                        };
                    }
                    KeyCode::Char(' ') => {
                        if detail.autoplay {
                            detail.autoplay = false;
                        } else if !detail.timeline_entries.is_empty() {
                            if detail.timeline_index + 1 >= detail.timeline_entries.len() {
                                detail.set_timeline_index(0);
                            }
                            detail.autoplay = true;
                            detail.focus = FocusPane::Replay;
                            detail.replay_follow_end = detail.replay_mode == ReplayMode::Text;
                            detail.replay_scroll = if detail.replay_follow_end {
                                u16::MAX
                            } else {
                                0
                            };
                            detail.last_tick = Instant::now();
                        }
                    }
                    KeyCode::Enter => {
                        if detail.selected_event().is_some() {
                            app.event_modal_open = true;
                            app.event_modal_scroll = 0;
                        }
                    }
                    KeyCode::Char('c') => {
                        if let Some((row_index, command)) = detail
                            .timeline_entries
                            .get(detail.timeline_index)
                            .and_then(|entry| {
                                entry
                                    .copy_command
                                    .clone()
                                    .map(|command| (detail.timeline_index, command))
                            })
                        {
                            match crate::copy_to_clipboard(&command) {
                                Ok(()) => {
                                    flash_message = Some("✓ Copied command".to_string());
                                    copy_target = Some(CopyFeedbackTarget::TimelineRow(row_index));
                                }
                                Err(err) => flash_message = Some(err),
                            }
                        } else {
                            flash_message = Some("No command to copy".to_string());
                        }
                    }
                    KeyCode::Char('D') => app.start_delete_detail(),
                    KeyCode::Char('T') => app.open_time_travel_modal(),
                    KeyCode::Char('e') => export_conversation = true,
                    KeyCode::Char('E') => export_timeline = true,
                    KeyCode::Left if detail.focus == FocusPane::Replay => {
                        detail.scroll_replay_horizontal(-4)
                    }
                    KeyCode::Right if detail.focus == FocusPane::Replay => {
                        detail.scroll_replay_horizontal(4)
                    }
                    KeyCode::Up | KeyCode::Char('k') => {
                        if detail.focus == FocusPane::Changes {
                            if let Some(changes_area) =
                                session_detail_layout(detail).map(|layout| layout.changes)
                            {
                                detail.scroll_changes_by(-1, changes_area);
                            }
                        } else {
                            detail.move_selection(-1);
                        }
                    }
                    KeyCode::Down | KeyCode::Char('j') => {
                        if detail.focus == FocusPane::Changes {
                            if let Some(changes_area) =
                                session_detail_layout(detail).map(|layout| layout.changes)
                            {
                                detail.scroll_changes_by(1, changes_area);
                            }
                        } else {
                            detail.move_selection(1);
                        }
                    }
                    KeyCode::PageUp if detail.focus == FocusPane::Changes => {
                        if let Some(changes_area) =
                            session_detail_layout(detail).map(|layout| layout.changes)
                        {
                            detail.scroll_changes_by(-10, changes_area);
                        }
                    }
                    KeyCode::PageDown if detail.focus == FocusPane::Changes => {
                        if let Some(changes_area) =
                            session_detail_layout(detail).map(|layout| layout.changes)
                        {
                            detail.scroll_changes_by(10, changes_area);
                        }
                    }
                    KeyCode::Home if detail.focus == FocusPane::Changes => {
                        detail.changes_scroll = 0
                    }
                    KeyCode::End if detail.focus == FocusPane::Changes => {
                        if let Some(changes_area) =
                            session_detail_layout(detail).map(|layout| layout.changes)
                        {
                            detail.changes_scroll = changes_max_scroll(detail, changes_area);
                        }
                    }
                    KeyCode::BackTab if detail.focus == FocusPane::Timeline => {
                        detail.page_selection(-1)
                    }
                    KeyCode::Tab if detail.focus == FocusPane::Timeline => detail.page_selection(1),
                    _ => {}
                }
            }
        }
        if close_detail {
            app.detail = None;
            app.status = "Back to sessions".to_string();
            app.needs_terminal_clear = true;
        }
        if export_conversation {
            match app.export_conversation_jsonl() {
                Ok((out, count)) => {
                    flash_message = Some(format!("Exported {count} conversation rows to {}", out))
                }
                Err(err) => flash_message = Some(err),
            }
        }
        if export_timeline {
            match app.export_timeline_csv() {
                Ok(out) => flash_message = Some(format!("Exported timeline CSV to {}", out)),
                Err(err) => flash_message = Some(err),
            }
        }
        if let Some(message) = flash_message {
            app.set_flash(message);
        }
        if let Some(target) = copy_target {
            app.set_copy_feedback(target);
        }
        return Ok(false);
    }

    if app.filter_menu {
        match key.code {
            KeyCode::Esc => {
                app.filter_menu = false;
            }
            KeyCode::Enter => {
                if App::filter_fields()[app.filter_cursor] == "[Reset All]" {
                    app.filters = SessionFilters::default();
                    app.apply_session_filters();
                }
                app.filter_menu = false;
            }
            KeyCode::Down => {
                let max = App::filter_fields().len().saturating_sub(1);
                app.filter_cursor = (app.filter_cursor + 1).min(max);
            }
            KeyCode::Up => {
                if app.filter_cursor > 0 {
                    app.filter_cursor -= 1;
                }
            }
            KeyCode::Left => app.cycle_filter_value(-1),
            KeyCode::Right => app.cycle_filter_value(1),
            _ => {}
        }
        return Ok(false);
    }

    match key.code {
        KeyCode::Char('c') if key.modifiers.contains(KeyModifiers::CONTROL) => return Ok(true),
        KeyCode::Esc => return Ok(true),
        KeyCode::Up => {
            if app.selected > 0 {
                app.selected -= 1;
            }
        }
        KeyCode::Down => {
            if app.selected + 1 < app.sessions.len() {
                app.selected += 1;
            }
        }
        KeyCode::Enter => app.open_selected()?,
        KeyCode::Char('f') => app.filter_menu = true,
        KeyCode::Char('R') => app.start_rename_selected(),
        KeyCode::Char('D') => app.start_delete_selected(),
        KeyCode::Char('r') => app.refresh_sessions()?,
        _ => {}
    }
    Ok(false)
}

fn draw_ui(frame: &mut ratatui::Frame<'_>, app: &App) {
    if let Some(detail) = app.detail.as_ref() {
        draw_detail(frame, app, detail);
    } else {
        draw_browser(frame, app);
    }
    if let Some(modal) = app.rename_modal.as_ref() {
        draw_rename_modal(frame, modal);
    }
    if let Some(modal) = app.delete_modal.as_ref() {
        draw_delete_modal(frame, modal);
    }
    if let Some(modal) = app.time_travel_modal.as_ref() {
        draw_time_travel_modal(frame, modal);
    }
}

fn unique_values<I>(iter: I) -> Vec<String>
where
    I: Iterator<Item = String>,
{
    let mut set: BTreeSet<String> = BTreeSet::new();
    for value in iter {
        let clean = value.trim().to_string();
        if clean.is_empty() {
            continue;
        }
        set.insert(clean);
    }
    let mut out = vec!["".to_string()];
    out.extend(set);
    out
}

fn draw_browser(frame: &mut ratatui::Frame<'_>, app: &App) {
    let area = frame.area();
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Min(5),
            Constraint::Length(2),
            Constraint::Length(1),
        ])
        .split(area);

    let rows: Vec<Row> = app
        .sessions
        .iter()
        .map(|session| {
            Row::new(vec![
                Cell::from(session.session_id.clone()),
                Cell::from(if session.session_name.trim().is_empty() {
                    "-".to_string()
                } else {
                    truncate(&session.session_name, 18)
                }),
                Cell::from(format_ts(session.started_at)),
                Cell::from(if session.agent_name.trim().is_empty() {
                    session.agent.clone()
                } else {
                    session.agent_name.clone()
                }),
                Cell::from(session.model.clone()),
                Cell::from(truncate(&repo_display_name(&session.repo_root), 28)),
                Cell::from(if session.branch_end.trim().is_empty() {
                    session.branch_start.clone()
                } else {
                    session.branch_end.clone()
                }),
                Cell::from(format_duration(session.started_at, session.ended_at)),
                Cell::from(format_outcome(
                    session.exit_code,
                    &session.status,
                    &session.violation_code,
                )),
            ])
        })
        .collect();

    let table = Table::new(
        rows,
        [
            Constraint::Length(18),
            Constraint::Length(20),
            Constraint::Length(19),
            Constraint::Length(18),
            Constraint::Length(16),
            Constraint::Length(30),
            Constraint::Length(18),
            Constraint::Length(12),
            Constraint::Min(14),
        ],
    )
    .header(
        Row::new(vec![
            "session", "name", "started", "agent", "model", "repo", "branch", "duration", "outcome",
        ])
        .style(
            Style::default()
                .fg(Color::Cyan)
                .add_modifier(Modifier::BOLD),
        ),
    )
    .block(
        Block::default()
            .borders(Borders::ALL)
            .title(Line::from(Span::styled(
                "Agensic Sessions",
                crate::agensic_title_style(),
            ))),
    )
    .row_highlight_style(
        Style::default()
            .fg(Color::Black)
            .bg(Color::LightGreen)
            .add_modifier(Modifier::BOLD),
    )
    .highlight_symbol(">> ");
    let mut state = TableState::default();
    if !app.sessions.is_empty() {
        state.select(Some(app.selected));
    }
    frame.render_stateful_widget(table, chunks[0], &mut state);
    frame.render_widget(
        Paragraph::new(format!(
            "↑↓ move  Enter open  f filters  R rename  D delete  r refresh  Esc quit    {}",
            app.status_text()
        ))
        .style(Style::default().fg(Color::White)),
        chunks[1],
    );
    frame.render_widget(
        Paragraph::new(format!(
            "sessions: {}/{}",
            app.sessions.len(),
            app.all_sessions.len()
        ))
        .style(Style::default().fg(Color::DarkGray)),
        chunks[2],
    );

    if app.filter_menu {
        let popup = crate::centered_rect(58, 42, area);
        let fields = App::filter_fields();
        let mut lines: Vec<Line> = Vec::new();
        lines.push(Line::from(
            "Filter panel (Left/Right change, Up/Down move, Enter/Esc close)",
        ));
        lines.push(Line::from(""));
        for (idx, field) in fields.iter().enumerate() {
            let value = app.field_current(field);
            let shown = if value.is_empty() {
                "*"
            } else {
                value.as_str()
            };
            let style = if idx == app.filter_cursor {
                Style::default().fg(Color::Black).bg(Color::Cyan)
            } else {
                Style::default()
            };
            lines.push(Line::from(vec![Span::styled(
                format!("{}: {}", field, shown),
                style,
            )]));
        }
        let content = Paragraph::new(lines)
            .block(Block::default().borders(Borders::ALL).title("Filters"))
            .wrap(Wrap { trim: true });
        frame.render_widget(Clear, popup);
        frame.render_widget(content, popup);
    }
}

fn draw_detail(frame: &mut ratatui::Frame<'_>, app: &App, detail: &DetailState) {
    let area = frame.area();
    let Some(layout) = session_detail_layout_in_area(detail, area) else {
        return;
    };
    frame.render_widget(build_header(app, detail), layout.header);
    if !detail.replay_fullscreen {
        let timeline_view = timeline_viewport(detail, layout.timeline.height);
        let mut timeline_state = TableState::default();
        if !detail.timeline_entries.is_empty() {
            let selected = detail
                .timeline_index
                .min(detail.timeline_entries.len().saturating_sub(1));
            timeline_state.select(Some(selected.saturating_sub(timeline_view.start)));
        }
        frame.render_stateful_widget(
            build_timeline(app, detail, layout.timeline),
            layout.timeline,
            &mut timeline_state,
        );
        frame.render_widget(build_changes(detail, layout.changes), layout.changes);
    }
    frame.render_widget(build_replay(app, detail, layout.replay), layout.replay);
    let mut footer_lines = vec![
        Line::from(Span::styled(
            detail_footer_hints(),
            Style::default().fg(Color::Yellow),
        )),
        Line::from(app.status_text().to_string()),
    ];
    if detail.replay_loading {
        footer_lines.push(Line::from(Span::styled(
            format!("Loading replay cache {} ", loading_spinner_frame()),
            Style::default().fg(Color::LightCyan),
        )));
    }
    frame.render_widget(
        Paragraph::new(footer_lines).style(Style::default().fg(Color::White)),
        layout.footer,
    );

    if app.deep_link && detail.session.session_id.is_empty() {
        frame.render_widget(
            Paragraph::new("Session unavailable").block(Block::default().borders(Borders::ALL)),
            centered_rect(50, 20, area),
        );
    }

    if app.event_modal_open {
        if let (Some(event), Some(entry)) =
            (detail.selected_event(), detail.selected_timeline_entry())
        {
            draw_event_modal(frame, app, event, entry, detail, app.event_modal_scroll);
        }
    }
}

fn loading_spinner_frame() -> &'static str {
    let millis = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_millis() as usize)
        .unwrap_or(0);
    REPLAY_LOADING_FRAMES[(millis / 120) % REPLAY_LOADING_FRAMES.len()]
}

fn build_header(app: &App, detail: &DetailState) -> Paragraph<'static> {
    let metadata_key_style = Style::default().fg(Color::Yellow);
    let actor = if detail.session.agent_name.trim().is_empty() {
        detail.session.agent.as_str()
    } else {
        detail.session.agent_name.as_str()
    };
    let header_copy_button = copy_button_label(app.header_copy_copied());
    let mut lines = vec![
        Line::from(vec![
            Span::styled(
                actor.to_string(),
                Style::default()
                    .fg(Color::LightGreen)
                    .add_modifier(Modifier::BOLD),
            ),
            Span::raw("  "),
            Span::raw(sanitize_inline_text(&detail.session.model)),
            Span::raw("  "),
            Span::styled(
                format_header_outcome(detail.session.exit_code, &detail.session.status),
                Style::default().fg(Color::Yellow),
            ),
        ]),
        Line::from(vec![
            Span::styled("session ", metadata_key_style),
            Span::raw(detail.session.session_id.clone()),
            Span::raw("  ".to_string()),
            Span::styled(
                header_copy_button,
                copy_button_style(app.hovered_header_copy, app.header_copy_copied(), false),
            ),
            Span::raw("    ".to_string()),
            Span::styled("name ", metadata_key_style),
            Span::raw(if detail.session.session_name.trim().is_empty() {
                "-".to_string()
            } else {
                sanitize_inline_text(&detail.session.session_name)
            }),
            Span::raw("    ".to_string()),
            Span::styled("duration ", metadata_key_style),
            Span::raw(format_duration(
                detail.session.started_at,
                detail.session.ended_at,
            )),
            Span::raw("    ".to_string()),
            Span::styled("started ", metadata_key_style),
            Span::raw(format_ts(detail.session.started_at)),
        ]),
    ];
    if !detail.session.repo_root.trim().is_empty() {
        let repo_name = repo_display_name(&detail.session.repo_root);
        lines.push(Line::from(vec![
            Span::styled("repo ", metadata_key_style),
            Span::raw(repo_name),
            Span::raw("    ".to_string()),
            Span::styled("path ", metadata_key_style),
            Span::raw(sanitize_inline_text(&detail.session.repo_root)),
        ]));
    }
    if !detail.session.branch_start.trim().is_empty()
        || !detail.session.branch_end.trim().is_empty()
        || !detail.session.head_start.trim().is_empty()
        || !detail.session.head_end.trim().is_empty()
    {
        let branch_start = sanitize_inline_text(&detail.session.branch_start);
        let branch_end = sanitize_inline_text(&detail.session.branch_end);
        let head_start = sanitize_inline_text(&detail.session.head_start);
        let head_end = sanitize_inline_text(&detail.session.head_end);
        lines.push(Line::from(vec![
            Span::styled("branch ", metadata_key_style),
            Span::raw(fallback_text(&branch_start).to_string()),
            Span::raw(" -> ".to_string()),
            Span::raw(fallback_text(&branch_end).to_string()),
            Span::raw("    ".to_string()),
            Span::styled("head ", metadata_key_style),
            Span::raw(truncate(&head_start, 12)),
            Span::raw(" -> ".to_string()),
            Span::raw(truncate(&head_end, 12)),
        ]));
    }
    Paragraph::new(lines)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .title(Line::from(Span::styled(
                    "Agensic Session Detail (Experimental)",
                    crate::agensic_title_style(),
                ))),
        )
        .wrap(Wrap { trim: true })
}

fn build_timeline(app: &App, detail: &DetailState, area: Rect) -> Table<'static> {
    let total = detail.timeline_entries.len();
    let selected = detail.timeline_index.min(total.saturating_sub(1));
    let viewport = timeline_viewport(detail, area.height);
    let layout = timeline_table_layout(area.width);
    let preview_width = layout.preview_width as usize;
    let rows: Vec<Row<'static>> = (viewport.start..viewport.end)
        .map(|row_index| {
            let entry = &detail.timeline_entries[row_index];
            let copied = app.timeline_copy_copied(row_index);
            let hovered = app.timeline_copy_hovered(row_index);
            let selected_row = row_index == selected;
            let kind_style = timeline_kind_style(&entry.event_type);
            let button = if entry.copy_command.is_some() {
                copy_button_label(copied)
            } else {
                ""
            };
            Row::new(vec![
                Cell::from(format_timeline_ordinal(row_index + 1)),
                Cell::from(Span::styled(
                    truncate_display_width(
                        &sanitize_inline_text(&entry.event_type),
                        layout.kind_width as usize,
                    ),
                    kind_style.add_modifier(Modifier::BOLD),
                )),
                Cell::from(truncate_display_width(&entry.summary, preview_width)),
                Cell::from(Span::styled(
                    button,
                    if entry.copy_command.is_some() {
                        copy_button_style(hovered, copied, selected_row)
                    } else {
                        Style::default()
                    },
                )),
            ])
        })
        .collect();
    let header_style = Style::default()
        .fg(Color::Cyan)
        .add_modifier(Modifier::BOLD);
    let rows = if rows.is_empty() {
        vec![Row::new(vec![
            Cell::from("-"),
            Cell::from("-"),
            Cell::from("(no recorded events)"),
            Cell::from(""),
        ])]
    } else {
        rows
    };
    Table::new(
        rows,
        [
            Constraint::Length(layout.ordinal_width),
            Constraint::Length(layout.kind_width),
            Constraint::Length(layout.preview_width),
            Constraint::Length(layout.action_width),
        ],
    )
    .column_spacing(layout.spacing)
    .header(Row::new(vec!["n.", "kind", "preview", "copy"]).style(header_style))
    .block(pane_block("Timeline", detail.focus == FocusPane::Timeline))
    .row_highlight_style(
        Style::default()
            .fg(Color::Black)
            .bg(Color::LightGreen)
            .add_modifier(Modifier::BOLD),
    )
}

fn timeline_kind_style(event_type: &str) -> Style {
    let color = match event_type {
        "command.recorded" => Color::Rgb(214, 188, 52),
        "process.spawned" => Color::Rgb(94, 244, 126),
        "process.exited" => Color::Rgb(64, 214, 112),
        "terminal.input" => Color::Rgb(56, 198, 255),
        "terminal.output" => Color::Rgb(84, 232, 255),
        "terminal.resize" => Color::Rgb(120, 170, 255),
        "git.snapshot.start" => Color::Rgb(232, 110, 255),
        "git.snapshot.end" => Color::Rgb(198, 124, 255),
        "git.snapshot.chkpt" => Color::Rgb(216, 146, 255),
        "git.commit.created" => Color::Rgb(255, 126, 216),
        "git.commit.sess_sync" => Color::Rgb(232, 96, 186),
        "git.push.attempted" => Color::Rgb(224, 90, 188),
        "marker.session.started" => Color::Rgb(255, 106, 214),
        "marker.session.finished" => Color::Rgb(234, 90, 174),
        "violation.noted" => Color::Rgb(255, 116, 116),
        _ => timeline_category_color(TimelineCategory::from_event_type(event_type)),
    };
    Style::default().fg(color)
}

fn timeline_category_color(category: TimelineCategory) -> Color {
    match category {
        TimelineCategory::Command => Color::Rgb(196, 174, 78),
        TimelineCategory::Process => Color::Rgb(80, 226, 118),
        TimelineCategory::Terminal => Color::Rgb(88, 214, 255),
        TimelineCategory::Git => Color::Rgb(210, 112, 245),
        TimelineCategory::Marker => Color::Rgb(244, 102, 194),
        TimelineCategory::Violation => Color::Rgb(255, 126, 126),
        TimelineCategory::Session => Color::Rgb(118, 164, 255),
        TimelineCategory::Other => Color::White,
    }
}

fn timeline_table_layout(area_width: u16) -> TimelineTableLayout {
    let inner_width = area_width.saturating_sub(2).max(1);
    let action_width = copy_icon_width() as u16;
    let spacing = TIMELINE_COLUMN_SPACING;
    let total_spacing = spacing.saturating_mul(3);
    let max_kind_width = TIMELINE_EVENT_TYPE_WIDTH as u16;
    let available =
        inner_width.saturating_sub(TIMELINE_ORDINAL_WIDTH + action_width + total_spacing);
    let (kind_width, preview_width) = if available <= 1 {
        (1, 1)
    } else if available <= 10 {
        (available.saturating_sub(1), 1)
    } else {
        let kind_width = (available / 4).clamp(8, max_kind_width);
        let preview_width = available.saturating_sub(kind_width).max(1);
        (kind_width, preview_width)
    };
    TimelineTableLayout {
        ordinal_width: TIMELINE_ORDINAL_WIDTH,
        kind_width,
        preview_width,
        action_width,
        spacing,
    }
}

fn format_timeline_ordinal(value: usize) -> String {
    truncate_display_width(
        &crate::format_compact_count(value),
        TIMELINE_ORDINAL_WIDTH as usize,
    )
}

fn build_changes_lines(detail: &DetailState) -> Vec<Line<'static>> {
    let push_metric = detail
        .session
        .aggregate
        .get("push_attempts")
        .or_else(|| detail.session.aggregate.get("push_attempt_count"));
    let commit_count = detail
        .session
        .aggregate
        .get("commits_created")
        .and_then(Value::as_u64)
        .map(|value| value as usize)
        .or_else(|| {
            detail
                .session
                .aggregate
                .get("commit_count")
                .and_then(Value::as_u64)
                .map(|value| value as usize)
        })
        .or_else(|| {
            detail
                .session
                .changes
                .get("commits_created")
                .and_then(Value::as_array)
                .map(|items| items.len())
        });
    let commit_metric = detail.session.changes.get("commits_created");
    let mut lines = vec![Line::from(format!(
        "commands {}    subprocesses {}    pushes {}",
        metric(detail.session.aggregate.get("command_count")),
        metric(detail.session.aggregate.get("subprocess_count")),
        metric(push_metric),
    ))];
    lines.push(Line::from(format!(
        "commits {}    violations {}",
        commit_count
            .map(|value| value.to_string())
            .unwrap_or_else(|| metric(commit_metric)),
        if detail.session.violation_code.trim().is_empty() {
            "-".to_string()
        } else {
            sanitize_inline_text(&detail.session.violation_code)
        },
    )));

    let session_files: Vec<String> = detail
        .session
        .changes
        .get("files_changed")
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .filter_map(Value::as_str)
                .map(sanitize_inline_text)
                .filter(|item| !item.is_empty())
                .take(12)
                .collect()
        })
        .unwrap_or_default();
    let committed_diff = detail
        .session
        .changes
        .get("committed_diff_stat")
        .and_then(Value::as_str)
        .map(sanitize_multiline_text)
        .filter(|value| value != "-")
        .unwrap_or_default();
    let worktree_diff = detail
        .session
        .changes
        .get("worktree_diff_stat")
        .and_then(Value::as_str)
        .map(sanitize_multiline_text)
        .filter(|value| value != "-")
        .unwrap_or_default();
    let committed_stats = parse_diff_stat(&committed_diff);
    let worktree_stats = parse_diff_stat(&worktree_diff);
    let session_preferred_stats =
        if !committed_stats.files.is_empty() || committed_stats.summary.is_some() {
            committed_stats
        } else {
            worktree_stats
        };
    let selected_event = detail.selected_event();
    let selected_entry = detail.selected_timeline_entry();
    let selected_target_seq = selected_entry
        .map(|entry| entry.seq_end.max(entry.seq_start))
        .unwrap_or_default();
    let selected_checkpoint = detail.selected_git_checkpoint();
    let default_checkpoint_label = selected_checkpoint
        .map(|record| sanitize_inline_text(&record.checkpoint_id))
        .filter(|value| !value.is_empty())
        .unwrap_or_else(|| "-".to_string());
    let (checkpoint_label, selected_head) = selected_event
        .map(|event| match event.event_type.as_str() {
            "git.snapshot.end" => (
                "snapshot end".to_string(),
                event
                    .payload
                    .get("head")
                    .and_then(Value::as_str)
                    .map(str::to_string),
            ),
            "git.commit.created" | "git.commit.sess_sync" => (
                sanitize_inline_text(
                    event
                        .payload
                        .get("sha")
                        .and_then(Value::as_str)
                        .unwrap_or("commit"),
                ),
                event
                    .payload
                    .get("sha")
                    .and_then(Value::as_str)
                    .map(str::to_string),
            ),
            "git.snapshot.chkpt" => (default_checkpoint_label.clone(), None),
            _ => (default_checkpoint_label.clone(), None),
        })
        .unwrap_or_else(|| (default_checkpoint_label.clone(), None));
    let prefer_different_head = selected_event
        .map(|event| {
            matches!(
                event.event_type.as_str(),
                "git.commit.created" | "git.commit.sess_sync"
            )
        })
        .unwrap_or(false);
    let synthetic_git_view = selected_head.as_deref().and_then(|target_head| {
        let base_record = preferred_base_checkpoint_for_synthetic_view(
            detail,
            selected_target_seq,
            target_head,
            prefer_different_head,
        )?;
        let record_head = base_record.head.trim();
        if record_head.is_empty()
            || target_head.trim().is_empty()
            || record_head == target_head.trim()
        {
            return None;
        }
        Some((
            build_git_range_change_view_cached(detail, record_head, target_head),
            build_git_range_change_view_cached(detail, &detail.session.head_start, target_head),
        ))
    });
    let (checkpoint_delta_stats, checkpoint_delta_files, checkpoint_delta_markers) =
        if let Some((delta_view, _)) = synthetic_git_view.as_ref() {
            (
                delta_view.stats.clone(),
                delta_view.files.clone(),
                delta_view.markers.clone(),
            )
        } else if let Some(record) = selected_checkpoint {
            let checkpoint_delta_stats =
                parse_diff_stat(&sanitize_multiline_text(&record.delta_diff_stat));
            let checkpoint_worktree_stats =
                parse_diff_stat(&sanitize_multiline_text(&record.worktree_diff_stat));
            let preferred_stats = if !checkpoint_delta_stats.files.is_empty()
                || checkpoint_delta_stats.summary.is_some()
            {
                checkpoint_delta_stats
            } else if !checkpoint_worktree_stats.files.is_empty()
                || checkpoint_worktree_stats.summary.is_some()
            {
                checkpoint_worktree_stats
            } else {
                ParsedDiffStat::default()
            };
            let preferred_files = if !record.delta_files.is_empty() {
                &record.delta_files
            } else {
                &record.changed_files
            };
            let files = preferred_files
                .iter()
                .map(|item| sanitize_inline_text(item))
                .filter(|item| !item.is_empty())
                .collect();
            (preferred_stats, files, record.delta_file_markers.clone())
        } else {
            (
                session_preferred_stats.clone(),
                session_files.clone(),
                BTreeMap::new(),
            )
        };
    let (checkpoint_cumulative_stats, checkpoint_cumulative_files, checkpoint_cumulative_markers) =
        if let Some((_, cumulative_view)) = synthetic_git_view.as_ref() {
            (
                cumulative_view.stats.clone(),
                cumulative_view.files.clone(),
                cumulative_view.markers.clone(),
            )
        } else if let Some(record) = selected_checkpoint {
            let checkpoint_cumulative_stats =
                parse_diff_stat(&sanitize_multiline_text(&record.cumulative_diff_stat));
            let checkpoint_worktree_stats =
                parse_diff_stat(&sanitize_multiline_text(&record.worktree_diff_stat));
            let preferred_stats = if !checkpoint_cumulative_stats.files.is_empty()
                || checkpoint_cumulative_stats.summary.is_some()
            {
                checkpoint_cumulative_stats
            } else if !checkpoint_worktree_stats.files.is_empty()
                || checkpoint_worktree_stats.summary.is_some()
            {
                checkpoint_worktree_stats
            } else {
                ParsedDiffStat::default()
            };
            let preferred_files = if !record.cumulative_files.is_empty() {
                &record.cumulative_files
            } else {
                &record.changed_files
            };
            let files = preferred_files
                .iter()
                .map(|item| sanitize_inline_text(item))
                .filter(|item| !item.is_empty())
                .collect();
            (
                preferred_stats,
                files,
                record.cumulative_file_markers.clone(),
            )
        } else {
            (
                session_preferred_stats.clone(),
                session_files.clone(),
                BTreeMap::new(),
            )
        };

    lines.push(Line::from(""));
    lines.push(Line::from(Span::styled(
        "Session Summary",
        Style::default()
            .fg(Color::Cyan)
            .add_modifier(Modifier::BOLD),
    )));
    if let Some(summary) = session_preferred_stats.summary.as_ref() {
        lines.push(diff_stat_line(summary));
    } else if session_files.is_empty() && session_preferred_stats.files.is_empty() {
        lines.push(Line::from("No repo changes recorded in this session."));
    } else {
        lines.push(Line::from(format!(
            "{} file(s) changed in this session.",
            session_files.len().max(session_preferred_stats.files.len())
        )));
    }
    lines.push(Line::from(""));
    lines.push(Line::from(Span::styled(
        format!("Changes made in this checkpoint ({checkpoint_label})"),
        Style::default()
            .fg(Color::Cyan)
            .add_modifier(Modifier::BOLD),
    )));
    render_changes_block(
        &mut lines,
        &checkpoint_delta_stats,
        &checkpoint_delta_files,
        &checkpoint_delta_markers,
        "No repo changes recorded in this checkpoint.",
    );
    lines.push(Line::from(""));
    lines.push(Line::from(Span::styled(
        format!("Changes made since session start ({checkpoint_label})"),
        Style::default()
            .fg(Color::Cyan)
            .add_modifier(Modifier::BOLD),
    )));
    render_changes_block(
        &mut lines,
        &checkpoint_cumulative_stats,
        &checkpoint_cumulative_files,
        &checkpoint_cumulative_markers,
        "No repo changes recorded since session start.",
    );

    lines
}

fn render_changes_block(
    lines: &mut Vec<Line<'static>>,
    stats: &ParsedDiffStat,
    files: &[String],
    markers: &BTreeMap<String, String>,
    empty_message: &str,
) {
    if files.is_empty() && stats.files.is_empty() {
        lines.push(Line::from(empty_message.to_string()));
        return;
    }
    let mut rendered_files = BTreeSet::new();
    for file in files {
        rendered_files.insert(file.clone());
        let marker = markers
            .get(file)
            .cloned()
            .unwrap_or_else(|| "•".to_string());
        if let Some(stat) = stats.files.get(file) {
            lines.push(diff_stat_line(&format!("{marker} {} | {}", file, stat)));
        } else {
            lines.push(Line::from(format!("{marker} {}", file)));
        }
    }
    for (file, stat) in &stats.files {
        if rendered_files.insert(file.clone()) {
            let marker = markers
                .get(file)
                .cloned()
                .unwrap_or_else(|| "•".to_string());
            lines.push(diff_stat_line(&format!("{marker} {} | {}", file, stat)));
        }
    }
}

#[derive(Clone, Debug, Default)]
struct GitRangeChangeView {
    stats: ParsedDiffStat,
    files: Vec<String>,
    markers: BTreeMap<String, String>,
}

#[derive(Clone, Debug, PartialEq, Eq, Hash)]
struct GitRangeChangeViewCacheKey {
    repo_root: String,
    start_head: String,
    end_head: String,
}

fn build_git_range_change_view_cached(
    detail: &DetailState,
    start_head: &str,
    end_head: &str,
) -> GitRangeChangeView {
    let key = GitRangeChangeViewCacheKey {
        repo_root: detail.session.repo_root.trim().to_string(),
        start_head: start_head.trim().to_string(),
        end_head: end_head.trim().to_string(),
    };
    if key.repo_root.is_empty()
        || key.start_head.is_empty()
        || key.end_head.is_empty()
        || key.start_head == key.end_head
    {
        return GitRangeChangeView::default();
    }
    if let Some(cached) = detail.git_change_view_cache.borrow().get(&key).cloned() {
        return cached;
    }
    let view = build_git_range_change_view(&key.repo_root, &key.start_head, &key.end_head);
    detail
        .git_change_view_cache
        .borrow_mut()
        .insert(key, view.clone());
    view
}

fn preferred_base_checkpoint_for_synthetic_view<'a>(
    detail: &'a DetailState,
    target_seq: i64,
    target_head: &str,
    prefer_different_head: bool,
) -> Option<&'a GitCheckpointRecord> {
    let clean_target_head = target_head.trim();
    let mut same_head_candidate = None;
    for record in detail.git_checkpoint_records.iter().rev() {
        if record.seq >= target_seq {
            continue;
        }
        let record_head = record.head.trim();
        if record_head.is_empty() {
            continue;
        }
        if !prefer_different_head
            || clean_target_head.is_empty()
            || record_head != clean_target_head
        {
            return Some(record);
        }
        if same_head_candidate.is_none() {
            same_head_candidate = Some(record);
        }
    }
    same_head_candidate
}

fn build_git_range_change_view(
    repo_root: &str,
    start_head: &str,
    end_head: &str,
) -> GitRangeChangeView {
    let clean_repo_root = repo_root.trim();
    let clean_start = start_head.trim();
    let clean_end = end_head.trim();
    if clean_repo_root.is_empty()
        || clean_start.is_empty()
        || clean_end.is_empty()
        || clean_start == clean_end
    {
        return GitRangeChangeView::default();
    }
    let range = format!("{clean_start}..{clean_end}");
    let diff_stat =
        git_capture_text(clean_repo_root, &["diff", "--stat", &range]).unwrap_or_default();
    let stats = parse_diff_stat(&sanitize_multiline_text(&diff_stat));
    let name_status =
        git_capture_text(clean_repo_root, &["diff", "--name-status", &range]).unwrap_or_default();
    let (files, markers) = parse_name_status_text(&name_status);
    GitRangeChangeView {
        stats,
        files,
        markers,
    }
}

fn git_capture_text(repo_root: &str, args: &[&str]) -> Option<String> {
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

fn parse_name_status_text(text: &str) -> (Vec<String>, BTreeMap<String, String>) {
    let mut files = Vec::new();
    let mut markers = BTreeMap::new();
    for raw_line in text
        .lines()
        .map(str::trim_end)
        .filter(|line| !line.is_empty())
    {
        let parts: Vec<&str> = raw_line.split('\t').collect();
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
        markers.insert(clean_path, marker_for_git_status(status).to_string());
    }
    (files, markers)
}

fn marker_for_git_status(status: &str) -> &'static str {
    match status.chars().next().unwrap_or_default() {
        'A' | 'C' => "+",
        'D' => "-",
        'R' => ">",
        'M' => "~",
        _ => "•",
    }
}

fn changes_plain_text(lines: &[Line<'_>]) -> String {
    lines
        .iter()
        .map(ToString::to_string)
        .collect::<Vec<_>>()
        .join("\n")
}

fn changes_max_scroll(detail: &DetailState, area: Rect) -> u16 {
    let lines = build_changes_lines(detail);
    let content_lines = rendered_text_height(
        &changes_plain_text(&lines),
        area.width.saturating_sub(2).max(1) as usize,
    );
    let visible_lines = area.height.saturating_sub(2).max(1) as usize;
    content_lines
        .saturating_sub(visible_lines)
        .min(u16::MAX as usize) as u16
}

fn build_changes(detail: &DetailState, area: Rect) -> Paragraph<'static> {
    Paragraph::new(build_changes_lines(detail))
        .block(pane_block("Changes", detail.focus == FocusPane::Changes))
        .scroll((
            detail.changes_scroll.min(changes_max_scroll(detail, area)),
            0,
        ))
        .wrap(Wrap { trim: true })
}

fn build_replay(app: &App, detail: &DetailState, area: Rect) -> Paragraph<'static> {
    let focused = detail.focus == FocusPane::Replay;
    let status_label = replay_status_label(detail.autoplay);
    let mode_label = match detail.replay_mode {
        ReplayMode::Terminal => "terminal replay",
        ReplayMode::Text => "fallback transcript",
    };
    let active_frame = detail
        .terminal_replay_frames
        .get(detail.replay_step)
        .filter(|_| detail.replay_mode == ReplayMode::Terminal && detail.replay_visible);
    let frame_size_label = active_frame
        .map(|frame| format!(" {}x{}", frame.cols, frame.rows))
        .unwrap_or_default();
    let horizontal_scroll_hint = active_frame
        .filter(|frame| terminal_replay_max_scroll_x(frame, area) > 0)
        .map(|_| " ←/→ horizontal scroll")
        .unwrap_or("");
    let toggle_label = if detail.replay_fullscreen {
        REPLAY_SPLIT_BUTTON
    } else {
        REPLAY_FULLSCREEN_BUTTON
    };
    let title = Line::from(vec![
        Span::raw(format!(
            "Replay ({}{}) [{}]{} ",
            mode_label, frame_size_label, status_label, horizontal_scroll_hint
        )),
        Span::raw("  "),
        Span::styled(toggle_label, replay_toggle_style(app.hovered_replay_toggle)),
        Span::raw("  "),
    ]);
    let (text, scroll_y, scroll_x) = match detail.replay_mode {
        ReplayMode::Terminal => {
            let visible_rows = area.height.saturating_sub(2).max(1) as usize;
            let lines = if detail.replay_loading_for_active_view() {
                vec![Line::from(Span::styled(
                    format!("Loading replay cache {} ", loading_spinner_frame()),
                    Style::default().fg(Color::LightCyan),
                ))]
            } else if detail.replay_visible {
                detail
                    .terminal_replay_frames
                    .get(detail.replay_step)
                    .map(|frame| {
                        let padding_rows = terminal_replay_end_padding(
                            detail.replay_step,
                            detail.terminal_replay_frames.len(),
                        );
                        let scroll = terminal_replay_scroll(frame, area, padding_rows) as usize;
                        let total_rows = frame.lines.len().saturating_add(padding_rows as usize);
                        let start = scroll.min(total_rows);
                        let end = start.saturating_add(visible_rows).min(total_rows);
                        let visible_frame_end = end.min(frame.lines.len());
                        let mut lines =
                            frame.lines[start.min(frame.lines.len())..visible_frame_end].to_vec();
                        let blank_rows = end.saturating_sub(frame.lines.len()).min(visible_rows);
                        if blank_rows > 0 {
                            lines
                                .extend(std::iter::repeat_with(|| Line::from("")).take(blank_rows));
                        }
                        lines
                    })
                    .filter(|lines| !lines.is_empty())
                    .unwrap_or_else(|| vec![Line::from("")])
            } else if let Some(notice) = detail.replay_notice.as_ref() {
                vec![Line::from(notice.clone())]
            } else {
                vec![Line::from("")]
            };
            let scroll_x = active_frame
                .map(|frame| {
                    detail
                        .replay_scroll_x
                        .min(terminal_replay_max_scroll_x(frame, area))
                })
                .unwrap_or(0);
            (Text::from(lines), 0, scroll_x)
        }
        ReplayMode::Text => {
            let collapsed = if detail.replay_visible {
                collapse_blank_runs(&detail.replay_text, 2)
            } else {
                String::new()
            };
            let mut lines = Vec::new();
            if let Some(notice) = detail.replay_notice.as_ref() {
                lines.push(Line::from(Span::styled(
                    notice.clone(),
                    Style::default()
                        .fg(Color::LightYellow)
                        .add_modifier(Modifier::BOLD),
                )));
                if !collapsed.trim().is_empty() {
                    lines.push(Line::from(""));
                }
            }
            if collapsed.trim().is_empty() {
                if lines.is_empty() {
                    lines.push(Line::from(""));
                }
            } else {
                lines.extend(collapsed.lines().map(|line| Line::from(line.to_string())));
            }
            let plain = lines
                .iter()
                .map(ToString::to_string)
                .collect::<Vec<_>>()
                .join("\n");
            let max_scroll = replay_max_scroll(&plain, area);
            let scroll = if detail.replay_follow_end {
                max_scroll
            } else {
                detail.replay_scroll.min(max_scroll)
            };
            (Text::from(lines), scroll, 0)
        }
    };
    let paragraph = Paragraph::new(text)
        .block(pane_block_title(title, focused))
        .scroll((scroll_y, scroll_x));
    if detail.replay_mode == ReplayMode::Text {
        paragraph.wrap(Wrap { trim: false })
    } else {
        paragraph
    }
}

fn draw_event_modal(
    frame: &mut ratatui::Frame<'_>,
    app: &App,
    event: &SessionEvent,
    entry: &TimelineEntry,
    detail: &DetailState,
    scroll: u16,
) {
    let popup = centered_rect(74, 72, frame.area());
    let content = build_event_modal_content(
        event,
        entry,
        detail,
        popup.width.saturating_sub(2) as usize,
        app.hovered_event_modal_copy,
        app.event_modal_copy_copied(event.seq),
    );
    let panel = Paragraph::new(content.lines)
        .block(
            Block::default()
                .borders(Borders::ALL)
                .title(Line::from(vec![
                    Span::styled(
                        "Event details ",
                        Style::default()
                            .fg(Color::LightGreen)
                            .add_modifier(Modifier::BOLD),
                    ),
                    Span::styled(
                        "(Enter/Esc close, ↑↓ scroll)",
                        Style::default().fg(Color::DarkGray),
                    ),
                ])),
        )
        .scroll((scroll, 0))
        .wrap(Wrap { trim: true });
    frame.render_widget(Clear, popup);
    frame.render_widget(panel, popup);
}

fn draw_rename_modal(frame: &mut ratatui::Frame<'_>, modal: &RenameModalState) {
    let popup = centered_rect(56, 26, frame.area());
    frame.render_widget(Clear, popup);
    let content = vec![
        Line::from("Rename session"),
        Line::from(""),
        Line::from(format!("session: {}", modal.session_id)),
        Line::from(format!("name: {}", modal.input)),
        Line::from(""),
        Line::from(Span::styled(
            "Enter: save  Esc: cancel  Backspace/Delete: edit",
            Style::default().fg(Color::Yellow),
        )),
    ];
    frame.render_widget(
        Paragraph::new(content)
            .block(
                Block::default()
                    .borders(Borders::ALL)
                    .title(Line::from(Span::styled(
                        "Rename Session",
                        crate::agensic_title_style(),
                    ))),
            )
            .wrap(Wrap { trim: true }),
        popup,
    );
}

fn draw_delete_modal(frame: &mut ratatui::Frame<'_>, modal: &DeleteModalState) {
    let popup = centered_rect(56, 28, frame.area());
    frame.render_widget(Clear, popup);
    let session_name = if modal.session_name.trim().is_empty() {
        "-".to_string()
    } else {
        modal.session_name.clone()
    };
    let content = vec![
        Line::from("Delete session"),
        Line::from(""),
        Line::from(format!("session: {}", modal.session_id)),
        Line::from(format!("name: {}", session_name)),
        Line::from(""),
        Line::from("This permanently removes the tracked session and its artifacts."),
        Line::from(""),
        Line::from(Span::styled(
            "Enter: delete  Esc: cancel",
            Style::default().fg(Color::Yellow),
        )),
    ];
    frame.render_widget(
        Paragraph::new(content)
            .block(
                Block::default()
                    .borders(Borders::ALL)
                    .title(Line::from(Span::styled(
                        "Delete Session",
                        crate::agensic_title_style(),
                    ))),
            )
            .wrap(Wrap { trim: true }),
        popup,
    );
}

fn draw_time_travel_modal(frame: &mut ratatui::Frame<'_>, modal: &TimeTravelModalState) {
    let popup = centered_rect(72, 48, frame.area());
    frame.render_widget(Clear, popup);
    let mut content = vec![
        Line::from("Time Travel"),
        Line::from(""),
        Line::from(format!("session: {}", modal.session_id)),
        Line::from(format!("target seq: {}", modal.target_seq)),
        Line::from(format!(
            "selected event: {}",
            sanitize_inline_text(&modal.event_summary)
        )),
    ];
    if !modal.error.trim().is_empty() {
        content.push(Line::from(""));
        content.push(Line::from(Span::styled(
            sanitize_inline_text(&modal.error),
            Style::default().fg(Color::LightRed),
        )));
    } else if let Some(preview) = modal.preview.as_ref() {
        let resolved_seq = preview
            .resolved_checkpoint
            .get("seq")
            .and_then(Value::as_i64)
            .unwrap_or(0);
        let branch = preview
            .resolved_checkpoint
            .get("branch")
            .and_then(Value::as_str)
            .unwrap_or("-");
        let head = preview
            .resolved_checkpoint
            .get("head")
            .and_then(Value::as_str)
            .unwrap_or("-");
        let current_branch = preview
            .current_repo_state
            .get("branch")
            .and_then(Value::as_str)
            .unwrap_or("-");
        let current_head = preview
            .current_repo_state
            .get("head")
            .and_then(Value::as_str)
            .unwrap_or("-");
        let diff_stat = preview
            .resolved_checkpoint
            .get("worktree_diff_stat")
            .and_then(Value::as_str)
            .unwrap_or("-");
        let untracked_count = preview
            .resolved_checkpoint
            .get("untracked_paths")
            .and_then(Value::as_array)
            .map(|items| items.len())
            .unwrap_or(0);
        content.push(Line::from(""));
        content.push(Line::from(format!(
            "resolved checkpoint: {} ({})",
            resolved_seq,
            if preview.exact_match {
                "exact"
            } else {
                "nearest prior"
            }
        )));
        content.push(Line::from(format!(
            "repo: {}",
            sanitize_inline_text(&preview.repo_root)
        )));
        content.push(Line::from(format!(
            "recorded branch/head: {} / {}",
            branch,
            truncate(head, 14)
        )));
        content.push(Line::from(format!(
            "live branch/head: {} / {}",
            current_branch,
            truncate(current_head, 14)
        )));
        content.push(Line::from(format!(
            "suggested fork branch: {}",
            sanitize_inline_text(&preview.suggested_branch)
        )));
        content.push(Line::from(format!(
            "tracked diff: {}",
            sanitize_inline_text(diff_stat)
        )));
        content.push(Line::from(format!("untracked files: {}", untracked_count)));
        content.push(Line::from(format!(
            "action: fork branch and restore checkpoint{}",
            if preview.can_fork { "" } else { " (blocked)" }
        )));
        if !preview.blocking_reason.trim().is_empty() {
            content.push(Line::from(Span::styled(
                format!(
                    "blocking reason: {}",
                    sanitize_inline_text(&preview.blocking_reason)
                ),
                Style::default().fg(Color::LightRed),
            )));
        }
    }
    content.push(Line::from(""));
    content.push(Line::from(Span::styled(
        "Enter: fork + launch replay  Esc: cancel",
        Style::default().fg(Color::Yellow),
    )));
    frame.render_widget(
        Paragraph::new(content)
            .block(
                Block::default()
                    .borders(Borders::ALL)
                    .title(Line::from(Span::styled(
                        "Time Travel",
                        crate::agensic_title_style(),
                    ))),
            )
            .wrap(Wrap { trim: true }),
        popup,
    );
}

fn centered_rect(percent_x: u16, percent_y: u16, r: Rect) -> Rect {
    let popup_layout = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Percentage((100 - percent_y) / 2),
            Constraint::Percentage(percent_y),
            Constraint::Percentage((100 - percent_y) / 2),
        ])
        .split(r);
    Layout::default()
        .direction(Direction::Horizontal)
        .constraints([
            Constraint::Percentage((100 - percent_x) / 2),
            Constraint::Percentage(percent_x),
            Constraint::Percentage((100 - percent_x) / 2),
        ])
        .split(popup_layout[1])[1]
}

fn format_ts(ts: i64) -> String {
    if ts <= 0 {
        return "-".to_string();
    }
    chrono::Local
        .timestamp_opt(ts, 0)
        .single()
        .map(|dt| dt.format("%Y-%m-%d %H:%M:%S").to_string())
        .unwrap_or_else(|| ts.to_string())
}

fn format_duration(started_at: i64, ended_at: i64) -> String {
    if started_at <= 0 {
        return "-".to_string();
    }
    let end = if ended_at > 0 { ended_at } else { started_at };
    let secs = max(0, end - started_at);
    if secs >= MAX_SESSION_DURATION_SECONDS {
        return "24h+".to_string();
    }
    if secs >= 3600 {
        return format_duration_hours_minutes(secs);
    }
    if secs >= 60 {
        return format_duration_minutes_seconds(secs);
    }
    format!("{}s", secs)
}

fn format_duration_minutes_seconds(total_seconds: i64) -> String {
    let minutes = total_seconds / 60;
    let seconds = total_seconds % 60;
    format!("{minutes}m {seconds}s")
}

fn format_duration_hours_minutes(total_seconds: i64) -> String {
    let total_minutes = total_seconds / 60;
    let hours = total_minutes / 60;
    let minutes = total_minutes % 60;
    format!("{hours}h {minutes}m")
}

fn format_outcome(exit_code: Option<i64>, status: &str, violation_code: &str) -> String {
    let mut out = format!(
        "{} exit={}",
        if status.trim().is_empty() {
            "-"
        } else {
            status
        },
        exit_code
            .map(|value| value.to_string())
            .unwrap_or_else(|| "-".to_string())
    );
    if let Some(violation) = outcome_violation_label(violation_code) {
        out.push_str(&format!(" violation={}", violation));
    }
    out
}

fn format_header_outcome(exit_code: Option<i64>, status: &str) -> String {
    format!(
        "{} exit={}",
        if status.trim().is_empty() {
            "-"
        } else {
            status
        },
        exit_code
            .map(|value| value.to_string())
            .unwrap_or_else(|| "-".to_string())
    )
}

fn fallback_text(value: &str) -> &str {
    if value.trim().is_empty() {
        "-"
    } else {
        value
    }
}

fn repo_display_name(repo_root: &str) -> String {
    let trimmed = repo_root.trim().trim_end_matches(['/', '\\']);
    if trimmed.is_empty() {
        return "-".to_string();
    }
    Path::new(trimmed)
        .file_name()
        .and_then(|name| name.to_str())
        .filter(|name| !name.trim().is_empty())
        .map(sanitize_inline_text)
        .unwrap_or_else(|| sanitize_inline_text(trimmed))
}

fn outcome_violation_label(violation_code: &str) -> Option<String> {
    let sanitized = sanitize_inline_text(violation_code);
    if sanitized.is_empty() || sanitized == "session_boundary_escape" {
        None
    } else {
        Some(sanitized)
    }
}

fn truncate(value: &str, max_chars: usize) -> String {
    let chars: Vec<char> = value.chars().collect();
    if chars.len() <= max_chars {
        return value.to_string();
    }
    chars
        .into_iter()
        .take(max_chars.saturating_sub(1))
        .collect::<String>()
        + "…"
}

pub(crate) fn display_width(value: &str) -> usize {
    UnicodeWidthStr::width(value)
}

pub(crate) fn copy_icon_width() -> usize {
    display_width(SESSION_COPY_BUTTON)
        .max(display_width(SESSION_COPIED_BUTTON))
        .max(1)
}

pub(crate) fn truncate_display_width(value: &str, max_width: usize) -> String {
    if max_width == 0 {
        return String::new();
    }
    if display_width(value) <= max_width {
        return value.to_string();
    }
    if max_width == 1 {
        return "…".to_string();
    }

    let mut out = String::new();
    let mut width = 0usize;
    for ch in value.chars() {
        let ch_width = UnicodeWidthChar::width(ch).unwrap_or(0);
        if width + ch_width + 1 > max_width {
            break;
        }
        out.push(ch);
        width += ch_width;
    }
    out.push('…');
    out
}

pub(crate) fn copy_button_label(copied: bool) -> &'static str {
    if copied {
        SESSION_COPIED_BUTTON
    } else {
        SESSION_COPY_BUTTON
    }
}

fn inline_copy_line(label: &str, hovered: bool, copied: bool) -> Line<'static> {
    let button = copy_button_label(copied);
    Line::from(vec![
        Span::styled(
            label.to_string(),
            Style::default()
                .fg(Color::Cyan)
                .add_modifier(Modifier::BOLD),
        ),
        Span::raw(" "),
        Span::styled(button, copy_button_style(hovered, copied, false)),
    ])
}

fn event_summary(event: &SessionEvent) -> String {
    match event.event_type.as_str() {
        "git.snapshot.chkpt" => {
            let checkpoint_id = event
                .payload
                .get("checkpoint_id")
                .and_then(Value::as_str)
                .map(sanitize_inline_text)
                .filter(|value| !value.is_empty())
                .unwrap_or_else(|| "chkpt".to_string());
            let reason = checkpoint_reason_label(
                event
                    .payload
                    .get("reason")
                    .and_then(Value::as_str)
                    .unwrap_or_default(),
            );
            truncate(&format!("{checkpoint_id} {reason}"), 52)
        }
        "process.spawned" | "process.exited" | "command.recorded" | "git.push.attempted" => event
            .payload
            .get("command")
            .and_then(Value::as_str)
            .map(|value| truncate(&sanitize_inline_text(value), 52))
            .unwrap_or_else(|| "-".to_string()),
        "violation.noted" => event
            .payload
            .get("code")
            .and_then(Value::as_str)
            .map(sanitize_inline_text)
            .unwrap_or_else(|| "-".to_string()),
        "git.commit.created" | "git.commit.sess_sync" => format!(
            "{} {}",
            sanitize_inline_text(
                event
                    .payload
                    .get("sha")
                    .and_then(Value::as_str)
                    .unwrap_or("-")
            ),
            sanitize_inline_text(
                event
                    .payload
                    .get("summary")
                    .and_then(Value::as_str)
                    .unwrap_or("-"),
            ),
        ),
        "terminal.stdin" | "terminal.stdout" => event
            .payload
            .get("data")
            .and_then(Value::as_str)
            .map(|value| truncate(&sanitize_inline_text(value), 52))
            .unwrap_or_else(|| "-".to_string()),
        _ => event
            .payload
            .as_object()
            .map(|obj| truncate(&sanitize_inline_text(&format!("{:?}", obj)), 52))
            .unwrap_or_else(|| "-".to_string()),
    }
}

fn build_event_modal_content(
    event: &SessionEvent,
    entry: &TimelineEntry,
    detail: &DetailState,
    _content_width: usize,
    hovered_copy: bool,
    copied: bool,
) -> EventModalContent {
    let mut lines = vec![
        Line::from(format!("session: {}", detail.session.session_id)),
        Line::from(format!("timeline_row: {}", detail.timeline_index + 1)),
        Line::from(format!(
            "event_index: {} (source {}..{}, count {})",
            entry.event_index,
            entry.event_start_index,
            entry.event_end_index,
            entry
                .event_end_index
                .saturating_sub(entry.event_start_index)
                .saturating_add(1)
        )),
        Line::from(format!(
            "seq: {} (range {}..{})",
            event.seq, entry.seq_start, entry.seq_end
        )),
        Line::from(format!("type: {}", sanitize_inline_text(&event.event_type))),
        Line::from(format!("timestamp: {}", format_wall_ts(event.ts_wall))),
        Line::from(format!("monotonic_ms: {}", event.ts_monotonic_ms)),
    ];

    if let Some(pid) = event.payload.get("pid").and_then(Value::as_i64) {
        lines.push(Line::from(format!("pid: {}", pid)));
    }
    if let Some(ppid) = event.payload.get("ppid").and_then(Value::as_i64) {
        lines.push(Line::from(format!("ppid: {}", ppid)));
    }
    if let Some(exit_code) = event.payload.get("exit_code").and_then(Value::as_i64) {
        lines.push(Line::from(format!("exit_code: {}", exit_code)));
    }
    if let Some(label) = event.payload.get("label").and_then(Value::as_str) {
        lines.push(Line::from(format!(
            "label: {}",
            sanitize_inline_text(label)
        )));
    }
    if let Some(cwd) = event
        .payload
        .get("working_directory")
        .and_then(Value::as_str)
    {
        lines.push(Line::from(format!("cwd: {}", sanitize_multiline_text(cwd))));
    } else if !detail.session.working_directory.trim().is_empty() {
        lines.push(Line::from(format!(
            "cwd: {}",
            sanitize_multiline_text(&detail.session.working_directory)
        )));
    }

    let command = event_command(event);
    let mut command_line_index = None;
    if let Some(command) = command.as_ref() {
        lines.push(Line::from(""));
        command_line_index = Some(lines.len() as u16);
        lines.push(inline_copy_line("command", hovered_copy, copied));
        push_text_block(&mut lines, &sanitize_multiline_text(command));
    }

    if let Some(data) = event.payload.get("data").and_then(Value::as_str) {
        lines.push(Line::from(""));
        lines.push(Line::from(Span::styled(
            "data",
            Style::default()
                .fg(Color::Cyan)
                .add_modifier(Modifier::BOLD),
        )));
        push_text_block(&mut lines, &sanitize_multiline_text(data));
    }

    let payload_text =
        serde_json::to_string_pretty(&event.payload).unwrap_or_else(|_| "{}".to_string());
    lines.push(Line::from(""));
    lines.push(Line::from(Span::styled(
        "payload",
        Style::default()
            .fg(Color::Cyan)
            .add_modifier(Modifier::BOLD),
    )));
    push_text_block(&mut lines, &sanitize_multiline_text(&payload_text));
    EventModalContent {
        lines,
        command_line_index,
        command,
    }
}

fn detail_header_height(detail: &DetailState) -> u16 {
    let mut line_count = 2u16;
    if !detail.session.repo_root.trim().is_empty() {
        line_count += 1;
    }
    if !detail.session.branch_start.trim().is_empty()
        || !detail.session.branch_end.trim().is_empty()
        || !detail.session.head_start.trim().is_empty()
        || !detail.session.head_end.trim().is_empty()
    {
        line_count += 1;
    }
    line_count + 2
}

fn detail_footer_height(detail: &DetailState) -> u16 {
    if detail.replay_loading {
        3
    } else {
        2
    }
}

#[cfg(target_os = "linux")]
fn detail_footer_hints() -> &'static str {
    "Space Play  ↑↓ Move/Scroll  ←→ Replay X  Tab Jump  c Copy  D Delete  T Time Travel  e/E Export  f Fullscreen  s Pane  Enter Details  Esc Back"
}

#[cfg(not(target_os = "linux"))]
fn detail_footer_hints() -> &'static str {
    "Space: Play/Pause   ↑↓: Move  ←/→: Horiz. Scroll  Tab/Shift+Tab: Jump 500  c: Copy  D: Delete  T: Time Travel  e: Export Conv(jsonl)  E: Export TL(csv)  f: Fullscreen  s: Switch pane  Enter: Details  Esc: Back"
}

fn changes_panel_constraints(detail: &DetailState) -> [Constraint; 2] {
    if detail_has_meaningful_changes(detail) {
        [Constraint::Percentage(40), Constraint::Percentage(60)]
    } else {
        [Constraint::Length(7), Constraint::Min(10)]
    }
}

fn detail_has_meaningful_changes(detail: &DetailState) -> bool {
    has_nonempty_array(detail.session.changes.get("files_changed"))
        || !detail.git_checkpoint_records.is_empty()
        || has_nonempty_array(detail.session.changes.get("commits_created"))
        || detail
            .session
            .changes
            .get("committed_diff_stat")
            .and_then(Value::as_str)
            .map(|value| sanitize_multiline_text(value) != "-")
            .unwrap_or(false)
        || detail
            .session
            .changes
            .get("worktree_diff_stat")
            .and_then(Value::as_str)
            .map(|value| sanitize_multiline_text(value) != "-")
            .unwrap_or(false)
}

fn has_nonempty_array(value: Option<&Value>) -> bool {
    value
        .and_then(Value::as_array)
        .map(|items| !items.is_empty())
        .unwrap_or(false)
}

fn augment_events_with_git_checkpoints(
    events: Vec<SessionEvent>,
    git_checkpoint_records: &[GitCheckpointRecord],
    session_id: &str,
) -> Vec<SessionEvent> {
    if git_checkpoint_records.is_empty()
        || events
            .iter()
            .any(|event| event.event_type == "git.snapshot.chkpt")
    {
        return events;
    }
    let mut inserts: BTreeMap<usize, Vec<SessionEvent>> = BTreeMap::new();
    let mut prepend = Vec::new();
    for record in git_checkpoint_records {
        let payload = json!({
            "checkpoint_id": record.checkpoint_id,
            "reason": record.reason,
            "branch": record.branch,
            "head": record.head,
            "worktree_diff_stat": record.worktree_diff_stat,
            "changed_files": record.changed_files,
            "untracked_paths": record.untracked_paths,
        });
        let ts_wall = if record.timestamp > 0 {
            record.timestamp as f64
        } else {
            events
                .iter()
                .rfind(|event| event.seq <= record.seq)
                .map(|event| event.ts_wall)
                .unwrap_or_default()
        };
        let synthetic = SessionEvent {
            session_id: session_id.to_string(),
            seq: record.seq,
            ts_wall,
            ts_monotonic_ms: 0,
            event_type: "git.snapshot.chkpt".to_string(),
            payload,
        };
        if let Some(event_idx) = events.iter().rposition(|event| event.seq <= record.seq) {
            inserts.entry(event_idx).or_default().push(synthetic);
        } else {
            prepend.push(synthetic);
        }
    }

    let mut out = Vec::with_capacity(events.len().saturating_add(git_checkpoint_records.len()));
    out.extend(prepend);
    for (idx, event) in events.into_iter().enumerate() {
        out.push(event);
        if let Some(pending) = inserts.remove(&idx) {
            out.extend(pending);
        }
    }
    out
}

fn checkpoint_reason_label(reason: &str) -> String {
    let clean = reason.trim();
    if clean.is_empty() {
        return "checkpoint".to_string();
    }
    if clean == "session_start" {
        return "session start".to_string();
    }
    if clean == "session_end" {
        return "session end".to_string();
    }
    if let Some(pid) = clean.strip_prefix("process_exit:") {
        return format!("process exit {pid}");
    }
    if let Some(sha) = clean.strip_prefix("commit_created:") {
        return format!("commit {sha}");
    }
    clean.replace('_', " ")
}

fn build_display_model(events: &[SessionEvent]) -> (Vec<TimelineEntry>, Vec<String>, Vec<usize>) {
    let mut timeline_entries = Vec::new();
    let mut replay_chunks = Vec::new();
    let mut replay_timeline_indices = Vec::new();
    let mut index = 0usize;

    while index < events.len() {
        let event = &events[index];
        match event.event_type.as_str() {
            "terminal.stdout" => {
                let start = index;
                while index < events.len() && events[index].event_type == "terminal.stdout" {
                    index += 1;
                }
                if let Some(block) = build_terminal_display_block(&events[start..index], start) {
                    let timeline_index = timeline_entries.len();
                    timeline_entries.push(TimelineEntry {
                        event_index: block.event_index,
                        event_start_index: start,
                        event_end_index: index.saturating_sub(1),
                        seq_start: events[start].seq,
                        seq_end: events[index.saturating_sub(1)].seq,
                        ts_wall: events[start].ts_wall,
                        event_type: "terminal.output".to_string(),
                        summary: block.summary,
                        copy_command: None,
                    });
                    replay_chunks.push(block.replay_text);
                    replay_timeline_indices.push(timeline_index);
                }
            }
            "terminal.stdin" => {
                let start = index;
                while index < events.len() && events[index].event_type == "terminal.stdin" {
                    index += 1;
                }
                if let Some(block) = build_terminal_display_block(&events[start..index], start) {
                    timeline_entries.push(TimelineEntry {
                        event_index: block.event_index,
                        event_start_index: start,
                        event_end_index: index.saturating_sub(1),
                        seq_start: events[start].seq,
                        seq_end: events[index.saturating_sub(1)].seq,
                        ts_wall: events[start].ts_wall,
                        event_type: "terminal.input".to_string(),
                        summary: block.summary,
                        copy_command: None,
                    });
                }
            }
            _ => {
                timeline_entries.push(TimelineEntry {
                    event_index: index,
                    event_start_index: index,
                    event_end_index: index,
                    seq_start: event.seq,
                    seq_end: event.seq,
                    ts_wall: event.ts_wall,
                    event_type: event.event_type.clone(),
                    summary: event_summary(event),
                    copy_command: event_command(event),
                });
                index += 1;
            }
        }
    }

    if timeline_entries.is_empty() {
        for (idx, event) in events.iter().enumerate() {
            timeline_entries.push(TimelineEntry {
                event_index: idx,
                event_start_index: idx,
                event_end_index: idx,
                seq_start: event.seq,
                seq_end: event.seq,
                ts_wall: event.ts_wall,
                event_type: event.event_type.clone(),
                summary: event_summary(event),
                copy_command: event_command(event),
            });
        }
    }

    (timeline_entries, replay_chunks, replay_timeline_indices)
}

fn event_command(event: &SessionEvent) -> Option<String> {
    event
        .payload
        .get("command")
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(ToOwned::to_owned)
}

fn load_transcript_chunks(path: &str) -> Vec<TranscriptChunk> {
    let Some(contents) = read_text_artifact(path) else {
        return Vec::new();
    };
    contents
        .lines()
        .filter_map(|line| serde_json::from_str::<TranscriptRecord>(line).ok())
        .filter_map(|record| {
            let data = if record.data_b64.is_empty() {
                Vec::new()
            } else {
                BASE64_STANDARD.decode(record.data_b64.as_bytes()).ok()?
            };
            Some(TranscriptChunk {
                direction: record.direction.trim().to_string(),
                data,
                seq: record.seq,
                rows: record.rows,
                cols: record.cols,
            })
        })
        .collect()
}

fn transcript_has_recorded_geometry(transcript_chunks: &[TranscriptChunk]) -> bool {
    transcript_chunks
        .iter()
        .any(|chunk| transcript_chunk_size(chunk).is_some())
}

fn first_recorded_terminal_size(transcript_chunks: &[TranscriptChunk]) -> Option<(u16, u16)> {
    transcript_chunks.iter().find_map(transcript_chunk_size)
}

fn transcript_chunk_size(chunk: &TranscriptChunk) -> Option<(u16, u16)> {
    let rows = chunk.rows.filter(|value| *value > 0)?;
    let cols = chunk.cols.filter(|value| *value > 0)?;
    Some((rows, cols))
}

fn build_terminal_replay_frames(
    transcript_chunks: &[TranscriptChunk],
    fallback_rows: u16,
    fallback_cols: u16,
) -> Vec<TerminalReplayFrame> {
    let initial_size =
        first_recorded_terminal_size(transcript_chunks).unwrap_or((fallback_rows, fallback_cols));
    let mut parser = VtParser::new(initial_size.0.max(1), initial_size.1.max(1), 10_000);
    let mut frames = Vec::new();
    let mut last_frame = Vec::new();

    for chunk in transcript_chunks {
        match chunk.direction.as_str() {
            "resize" => {
                if let Some((rows, cols)) = transcript_chunk_size(chunk) {
                    parser.set_size(rows.max(1), cols.max(1));
                    maybe_push_terminal_frame(&mut frames, &mut last_frame, &parser, chunk.seq);
                }
            }
            "pty" => {
                if chunk.data.is_empty() {
                    continue;
                }
                parser.process(&chunk.data);
                maybe_push_terminal_frame(&mut frames, &mut last_frame, &parser, chunk.seq);
            }
            _ => {}
        }
    }

    frames
}

fn build_terminal_replay_frames_from_checkpoints(
    checkpoint_records: &[CheckpointRecord],
) -> Vec<TerminalReplayFrame> {
    let mut frames = Vec::new();
    let mut last_frame = Vec::new();
    for record in checkpoint_records {
        let state = decode_checkpoint_state(record);
        if state.is_empty() {
            continue;
        }
        let mut parser = VtParser::new(record.rows.max(1), record.cols.max(1), 0);
        parser.process(&state);
        maybe_push_terminal_frame(&mut frames, &mut last_frame, &parser, Some(record.seq));
    }
    frames
}

fn maybe_push_terminal_frame(
    frames: &mut Vec<TerminalReplayFrame>,
    last_frame: &mut Vec<u8>,
    parser: &VtParser,
    source_seq_end: Option<i64>,
) {
    let screen = parser.screen();
    let formatted = screen.contents_formatted();
    let (rows, cols) = screen.size();
    let frame = render_terminal_frame(screen, rows.max(1), cols.max(1), source_seq_end);
    if formatted != *last_frame {
        if frames.is_empty() && frame.plain_text.trim().is_empty() {
            return;
        }
        *last_frame = formatted;
        frames.push(frame);
    } else if let (Some(seq), Some(last)) = (source_seq_end, frames.last_mut()) {
        last.source_seq_end = Some(seq);
    }
}

fn render_terminal_frame(
    screen: &vt100::Screen,
    rows: u16,
    cols: u16,
    source_seq_end: Option<i64>,
) -> TerminalReplayFrame {
    let plain_text = screen.rows(0, cols.max(1)).collect::<Vec<_>>().join("\n");
    let (cursor_row, _) = screen.cursor_position();
    let mut lines = Vec::with_capacity(rows as usize);
    let mut last_content_row = 0u16;
    for row in 0..rows.max(1) {
        let mut spans: Vec<Span<'static>> = Vec::new();
        let mut current_style: Option<Style> = None;
        let mut buffer = String::new();
        let mut col = 0u16;
        let mut row_has_content = false;
        while col < cols.max(1) {
            let cell = screen.cell(row, col);
            if cell.is_some_and(vt100::Cell::is_wide_continuation) {
                col += 1;
                continue;
            }
            let width = if cell.is_some_and(vt100::Cell::is_wide) {
                2
            } else {
                1
            };
            let style = cell.map(terminal_cell_style).unwrap_or_default();
            let text = match cell {
                Some(cell) if cell.has_contents() => {
                    row_has_content = true;
                    cell.contents()
                }
                _ => " ".to_string(),
            };
            if current_style == Some(style) {
                buffer.push_str(&text);
            } else {
                flush_terminal_span(&mut spans, &mut buffer, current_style);
                current_style = Some(style);
                buffer.push_str(&text);
            }
            col += width;
        }
        flush_terminal_span(&mut spans, &mut buffer, current_style);
        lines.push(Line::from(spans));
        if row_has_content {
            last_content_row = row;
        }
    }
    TerminalReplayFrame {
        plain_text,
        lines,
        source_seq_end,
        cursor_row,
        last_content_row,
        rows,
        cols,
    }
}

struct TerminalDisplayBlock {
    event_index: usize,
    summary: String,
    replay_text: String,
}

fn build_terminal_display_block(
    events: &[SessionEvent],
    start_index: usize,
) -> Option<TerminalDisplayBlock> {
    let aggressive = terminal_group_is_noisy(events);
    let lines = collect_terminal_lines(events, aggressive);
    if lines.is_empty() {
        return None;
    }
    let replay_text = format!("{}\n\n", lines.join("\n"));
    let summary = summarize_terminal_lines(&lines);
    Some(TerminalDisplayBlock {
        event_index: start_index + events.len().saturating_sub(1),
        summary,
        replay_text,
    })
}

fn terminal_group_is_noisy(events: &[SessionEvent]) -> bool {
    if events.len() >= 6 {
        return true;
    }
    events
        .iter()
        .filter_map(|event| event.payload.get("data").and_then(Value::as_str))
        .map(str::len)
        .sum::<usize>()
        >= 512
}

fn collect_terminal_lines(events: &[SessionEvent], aggressive: bool) -> Vec<String> {
    let mut lines = Vec::new();
    let mut current = String::new();

    for event in events {
        let Some(raw) = event.payload.get("data").and_then(Value::as_str) else {
            continue;
        };
        let cleaned = sanitize_terminal_output(raw);
        for ch in cleaned.chars() {
            if ch == '\n' {
                push_terminal_line(&mut lines, &mut current, aggressive);
                current.clear();
            } else {
                current.push(ch);
            }
        }
    }

    push_terminal_line(&mut lines, &mut current, aggressive);
    lines
}

fn push_terminal_line(lines: &mut Vec<String>, raw_line: &mut String, aggressive: bool) {
    let Some(candidate) = normalize_terminal_line(raw_line, aggressive) else {
        return;
    };
    merge_terminal_line(lines, candidate);
}

fn normalize_terminal_line(raw_line: &str, aggressive: bool) -> Option<String> {
    let stripped = strip_inline_progress_noise(raw_line);
    let collapsed = stripped.split_whitespace().collect::<Vec<_>>().join(" ");
    if collapsed.is_empty() || should_drop_terminal_line(&collapsed, aggressive) {
        return None;
    }
    Some(collapsed)
}

fn should_drop_terminal_line(line: &str, aggressive: bool) -> bool {
    let lowered = line.to_lowercase();
    if lowered.is_empty() {
        return true;
    }

    if line.chars().count() <= 2 {
        return true;
    }

    if lowered.contains("esc to interrupt") {
        return true;
    }

    if looks_like_terminal_chrome(&lowered) {
        return true;
    }

    aggressive
        && (looks_like_activity_status(&lowered)
            || looks_like_fragment_token(line)
            || line.chars().count() <= 1)
}

fn looks_like_terminal_chrome(lowered: &str) -> bool {
    [
        "openai codex (v",
        "claudecode",
        "see full release notes:",
        "/model to change",
        "model:",
        "directory:",
        "shortcuts",
        "context left",
        "tip: use /rename",
        "pressctrl-c again to exit",
        "best experience, launch it in a project directory instead",
    ]
    .iter()
    .any(|pattern| lowered.contains(pattern))
        || lowered.contains("100% left")
}

fn looks_like_activity_status(lowered: &str) -> bool {
    let tokens: Vec<&str> = lowered
        .split_whitespace()
        .map(|token| token.trim_matches(|ch: char| !ch.is_alphanumeric()))
        .filter(|token| !token.is_empty())
        .collect();
    if tokens.is_empty() || tokens.len() > 2 {
        return false;
    }
    let head = tokens[0];
    let repeat_counter = tokens
        .get(1)
        .map(|token| token.starts_with('x') && token[1..].chars().all(|ch| ch.is_ascii_digit()))
        .unwrap_or(true);
    repeat_counter
        && matches!(
            head,
            "working"
                | "thinking"
                | "loading"
                | "brewing"
                | "boogieing"
                | "quantizing"
                | "quantumizing"
                | "analyzing"
                | "reasoning"
        )
}

fn looks_like_fragment_token(text: &str) -> bool {
    let trimmed = text.trim();
    if trimmed.is_empty() {
        return true;
    }
    if trimmed.chars().any(char::is_whitespace) {
        return false;
    }
    let compact = compact_terminal_text(trimmed);
    if compact.is_empty() || compact.len() > 12 {
        return false;
    }
    let starts_clean = trimmed
        .chars()
        .next()
        .map(|ch| ch.is_ascii_uppercase() || ch.is_ascii_digit())
        .unwrap_or(false);
    let alphabetic_chars: Vec<char> = trimmed
        .chars()
        .filter(|ch| ch.is_ascii_alphabetic())
        .collect();
    let all_caps =
        !alphabetic_chars.is_empty() && alphabetic_chars.iter().all(|ch| ch.is_ascii_uppercase());
    let weird_punctuation =
        trimmed.contains('…') || trimmed.chars().any(|ch| matches!(ch, '*' | '~' | '•'));
    weird_punctuation
        || (!starts_clean && compact.len() <= 4)
        || (!starts_clean && trimmed.chars().count() <= 8)
        || (trimmed.chars().count() <= 4 && trimmed.chars().any(|ch| ch.is_ascii_lowercase()))
        || (compact.len() <= 2 && !all_caps)
}

fn compact_terminal_text(value: &str) -> String {
    value
        .chars()
        .filter(|ch| ch.is_alphanumeric())
        .flat_map(char::to_lowercase)
        .collect()
}

fn merge_terminal_line(lines: &mut Vec<String>, candidate: String) {
    const DEDUPE_WINDOW: usize = 8;

    for offset in 0..lines.len().min(DEDUPE_WINDOW) {
        let idx = lines.len() - 1 - offset;
        if lines[idx] == candidate {
            return;
        }
        if line_replaces_existing(&lines[idx], &candidate) {
            lines[idx] = candidate;
            return;
        }
        if line_replaces_existing(&candidate, &lines[idx]) {
            return;
        }
    }

    lines.push(candidate);
}

fn line_replaces_existing(existing: &str, candidate: &str) -> bool {
    let existing_compact = compact_terminal_text(existing);
    let candidate_compact = compact_terminal_text(candidate);
    if existing_compact.is_empty() || candidate_compact.is_empty() {
        return false;
    }
    if existing_compact == candidate_compact {
        return candidate.len() > existing.len();
    }
    if candidate_compact.len() > existing_compact.len()
        && candidate_compact.contains(&existing_compact)
        && existing_compact.len() >= 5
    {
        return true;
    }
    let common_prefix = common_prefix_len(&existing_compact, &candidate_compact);
    let shorter = existing_compact.len().min(candidate_compact.len());
    shorter <= 24
        && common_prefix + 2 >= shorter
        && candidate_compact.len() > existing_compact.len()
}

fn common_prefix_len(left: &str, right: &str) -> usize {
    left.chars()
        .zip(right.chars())
        .take_while(|(a, b)| a == b)
        .count()
}


fn timeline_viewport(detail: &DetailState, height: u16) -> TimelineViewport {
    let total = detail.timeline_entries.len();
    let selected = detail.timeline_index.min(total.saturating_sub(1));
    let visible_rows = height.saturating_sub(2).max(1) as usize;
    let start = selected.saturating_sub(visible_rows / 2);
    let max_start = total.saturating_sub(visible_rows);
    let start = start.min(max_start);
    TimelineViewport {
        start,
        end: min(total, start + visible_rows),
    }
}

#[derive(Clone, Debug, Default)]
struct ParsedDiffStat {
    files: BTreeMap<String, String>,
    summary: Option<String>,
}

fn parse_diff_stat(text: &str) -> ParsedDiffStat {
    let mut out = ParsedDiffStat::default();
    for raw_line in text
        .lines()
        .map(str::trim_end)
        .filter(|line| !line.is_empty())
    {
        if raw_line.contains('|') {
            if let Some((path, stat)) = raw_line.split_once('|') {
                let path = sanitize_inline_text(path.trim());
                let stat = sanitize_inline_text(stat.trim());
                if !path.is_empty() && !stat.is_empty() {
                    out.files.insert(path, stat);
                }
            }
            continue;
        }
        if raw_line.contains("file changed")
            || raw_line.contains("files changed")
            || raw_line.contains("insertion")
            || raw_line.contains("deletion")
        {
            out.summary = Some(sanitize_inline_text(raw_line.trim()));
        }
    }
    out
}

fn metric(value: Option<&Value>) -> String {
    match value {
        Some(Value::Number(number)) => number.to_string(),
        Some(Value::String(text)) if !text.trim().is_empty() => sanitize_inline_text(text),
        _ => "-".to_string(),
    }
}

fn terminal_replay_scroll(frame: &TerminalReplayFrame, area: Rect, padding_rows: u16) -> u16 {
    render_terminal_replay_scroll(
        frame.lines.len(),
        frame.rows,
        frame.cursor_row,
        frame.last_content_row,
        area,
        padding_rows,
    )
}

fn terminal_replay_max_scroll_x(frame: &TerminalReplayFrame, area: Rect) -> u16 {
    render_terminal_replay_max_scroll_x(frame.cols, area)
}

fn format_wall_ts(ts_wall: f64) -> String {
    if ts_wall <= 0.0 {
        return "-".to_string();
    }
    let secs = ts_wall.floor() as i64;
    chrono::Local
        .timestamp_opt(secs, 0)
        .single()
        .map(|dt| dt.format("%Y-%m-%d %H:%M:%S").to_string())
        .unwrap_or_else(|| format!("{:.3}", ts_wall))
}

fn now_epoch_seconds() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|duration| duration.as_secs().min(i64::MAX as u64) as i64)
        .unwrap_or(0)
}

fn default_export_dir() -> String {
    let home = std::env::var("HOME").unwrap_or_else(|_| ".".to_string());
    let downloads = format!("{}/Downloads", home);
    if Path::new(&downloads).is_dir() {
        downloads
    } else {
        home
    }
}

fn export_session_slug(session_id: &str) -> String {
    let slug: String = session_id
        .chars()
        .filter(|ch| ch.is_ascii_alphanumeric())
        .take(12)
        .collect();
    if slug.is_empty() {
        "session".to_string()
    } else {
        slug
    }
}

fn default_conversation_export_path(session_id: &str, ext: &str) -> String {
    format!(
        "{}/conversation_export_{}_{}.{}",
        default_export_dir(),
        export_session_slug(session_id),
        now_epoch_seconds(),
        ext
    )
}

fn default_timeline_export_path(session_id: &str, ext: &str) -> String {
    format!(
        "{}/timeline_export_{}_{}.{}",
        default_export_dir(),
        export_session_slug(session_id),
        now_epoch_seconds(),
        ext
    )
}

fn export_conversation_jsonl(detail: &DetailState, out_path: &str) -> Result<usize, String> {
    if out_path.trim().is_empty() {
        return Err("missing output path".to_string());
    }
    let output = Path::new(out_path);
    if let Some(parent) = output.parent() {
        if !parent.as_os_str().is_empty() {
            fs::create_dir_all(parent).map_err(|e| format!("create parent failed: {}", e))?;
        }
    }

    let rows = build_conversation_export_rows(detail);
    let mut file = File::create(output).map_err(|e| format!("create jsonl failed: {}", e))?;
    for row in &rows {
        serde_json::to_writer(&mut file, row)
            .map_err(|e| format!("serialize jsonl row failed: {}", e))?;
        file.write_all(b"\n")
            .map_err(|e| format!("write jsonl newline failed: {}", e))?;
    }
    file.flush()
        .map_err(|e| format!("flush jsonl failed: {}", e))?;
    Ok(rows.len())
}

fn export_timeline_rows(detail: &DetailState, out_path: &str) -> Result<(), String> {
    if out_path.trim().is_empty() {
        return Err("missing output path".to_string());
    }
    let output = Path::new(out_path);
    if let Some(parent) = output.parent() {
        if !parent.as_os_str().is_empty() {
            fs::create_dir_all(parent).map_err(|e| format!("create parent failed: {}", e))?;
        }
    }
    let mut writer =
        csv::Writer::from_path(output).map_err(|e| format!("create csv failed: {}", e))?;
    writer
        .write_record([
            "session_id",
            "row_index",
            "row_label",
            "event_index",
            "event_index_start",
            "event_index_end",
            "seq",
            "seq_start",
            "seq_end",
            "ts_iso",
            "event_type",
            "summary",
            "command",
            "grouped_event_count",
        ])
        .map_err(|e| format!("write csv header failed: {}", e))?;

    for (row_index, entry) in detail.timeline_entries.iter().enumerate() {
        writer
            .write_record([
                detail.session.session_id.clone(),
                (row_index + 1).to_string(),
                format_timeline_ordinal(row_index + 1),
                entry.event_index.to_string(),
                entry.event_start_index.to_string(),
                entry.event_end_index.to_string(),
                entry.seq_end.to_string(),
                entry.seq_start.to_string(),
                entry.seq_end.to_string(),
                format_wall_ts(entry.ts_wall),
                sanitize_inline_text(&entry.event_type),
                entry.summary.clone(),
                entry.copy_command.clone().unwrap_or_default(),
                entry
                    .event_end_index
                    .saturating_sub(entry.event_start_index)
                    .saturating_add(1)
                    .to_string(),
            ])
            .map_err(|e| format!("write csv row failed: {}", e))?;
    }

    writer
        .flush()
        .map_err(|e| format!("flush csv failed: {}", e))?;
    Ok(())
}

fn build_conversation_export_rows(detail: &DetailState) -> Vec<ConversationExportRow> {
    let mut rows = Vec::new();
    let mut index = 0usize;
    let session_id = detail.session.session_id.clone();
    let session_name = detail.session.session_name.trim().to_string();

    while index < detail.events.len() {
        let event = &detail.events[index];
        let (role, normalized_type) = match event.event_type.as_str() {
            "terminal.stdin" => ("user", "terminal.input"),
            "terminal.stdout" => ("agent", "terminal.output"),
            _ => {
                index += 1;
                continue;
            }
        };
        let start = index;
        while index < detail.events.len() && detail.events[index].event_type == event.event_type {
            index += 1;
        }
        let group = &detail.events[start..index];
        let Some(block) = build_terminal_display_block(group, start) else {
            continue;
        };
        rows.push(ConversationExportRow {
            session_id: session_id.clone(),
            session_name: session_name.clone(),
            row_index: rows.len() + 1,
            role: role.to_string(),
            source_event_type: normalized_type.to_string(),
            event_index_start: start,
            event_index_end: index.saturating_sub(1),
            seq_start: group.first().map(|item| item.seq).unwrap_or_default(),
            seq_end: group.last().map(|item| item.seq).unwrap_or_default(),
            ts_iso: format_wall_ts(group.first().map(|item| item.ts_wall).unwrap_or_default()),
            ts_monotonic_ms: group
                .first()
                .map(|item| item.ts_monotonic_ms)
                .unwrap_or_default(),
            text: block.replay_text.trim().to_string(),
        });
    }

    if !rows.is_empty() {
        return rows;
    }

    for (event_index, event) in detail.events.iter().enumerate() {
        if event.event_type != "command.recorded" {
            continue;
        }
        let Some(command) = event_command(event) else {
            continue;
        };
        rows.push(ConversationExportRow {
            session_id: session_id.clone(),
            session_name: session_name.clone(),
            row_index: rows.len() + 1,
            role: "user".to_string(),
            source_event_type: event.event_type.clone(),
            event_index_start: event_index,
            event_index_end: event_index,
            seq_start: event.seq,
            seq_end: event.seq,
            ts_iso: format_wall_ts(event.ts_wall),
            ts_monotonic_ms: event.ts_monotonic_ms,
            text: command,
        });
    }

    rows
}

fn sanitize_inline_text(value: &str) -> String {
    sanitize_terminal_output(value)
        .replace('\n', " ")
        .split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
}

fn sanitize_multiline_text(value: &str) -> String {
    let text = sanitize_terminal_output(value);
    if text.trim().is_empty() {
        "-".to_string()
    } else {
        text
    }
}

fn sanitize_terminal_output(value: &str) -> String {
    #[derive(Clone, Copy, Debug, PartialEq, Eq)]
    enum EscapeState {
        None,
        Escape,
        Csi,
        Osc,
        StringTerminator,
    }

    let mut output = String::with_capacity(value.len());
    let mut state = EscapeState::None;
    let mut previous_was_carriage_return = false;

    for ch in value.chars() {
        match state {
            EscapeState::None => match ch {
                '\u{1b}' => state = EscapeState::Escape,
                '\r' => {
                    output.push('\n');
                    previous_was_carriage_return = true;
                }
                '\u{8}' => {
                    output.pop();
                    previous_was_carriage_return = false;
                }
                '\n' => {
                    if !previous_was_carriage_return {
                        output.push('\n');
                    }
                    previous_was_carriage_return = false;
                }
                '\t' => {
                    output.push('\t');
                    previous_was_carriage_return = false;
                }
                control if control.is_control() => {
                    previous_was_carriage_return = false;
                }
                _ => {
                    output.push(ch);
                    previous_was_carriage_return = false;
                }
            },
            EscapeState::Escape => match ch {
                '[' => state = EscapeState::Csi,
                ']' | 'P' | 'X' | '^' | '_' => state = EscapeState::Osc,
                '\\' => state = EscapeState::None,
                _ => state = EscapeState::None,
            },
            EscapeState::Csi => {
                if ('@'..='~').contains(&ch) {
                    state = EscapeState::None;
                }
            }
            EscapeState::Osc => match ch {
                '\u{7}' => state = EscapeState::None,
                '\u{1b}' => state = EscapeState::StringTerminator,
                _ => {}
            },
            EscapeState::StringTerminator => {
                state = EscapeState::Osc;
                if ch == '\\' {
                    state = EscapeState::None;
                }
            }
        }
    }

    output
}

fn handle_mouse(app: &mut App, mouse: MouseEvent) {
    if app.rename_modal.is_some() {
        return;
    }
    if app.detail.is_some() {
        let mut flash_message: Option<String> = None;
        let mut copy_target: Option<CopyFeedbackTarget> = None;
        let mut handled = false;
        {
            let detail = app.detail.as_mut().expect("detail checked above");
            app.hovered_header_copy = session_header_copy_hit(mouse, detail);
            app.hovered_replay_toggle =
                !app.event_modal_open && replay_toggle_hit(mouse, detail).is_some();
            if app.event_modal_open {
                app.hovered_event_modal_copy = detail
                    .selected_event()
                    .and_then(|event| {
                        event_modal_copy_command(mouse, event, detail, app.event_modal_scroll)
                    })
                    .is_some();
                app.hovered_timeline_copy_row = None;
            } else {
                app.hovered_event_modal_copy = false;
                app.hovered_timeline_copy_row = timeline_mouse_hit(mouse, detail)
                    .and_then(|(row_index, clicked_copy)| clicked_copy.then_some(row_index));
            }

            if let MouseEventKind::Down(MouseButton::Left) = mouse.kind {
                if app.hovered_header_copy {
                    match crate::copy_to_clipboard(&detail.session.session_id) {
                        Ok(()) => {
                            flash_message = Some("✓ Copied session ID".to_string());
                            copy_target = Some(CopyFeedbackTarget::SessionId);
                        }
                        Err(err) => flash_message = Some(err),
                    }
                    handled = true;
                }
                if !handled && app.hovered_replay_toggle {
                    detail.toggle_replay_fullscreen();
                    flash_message = Some(if detail.replay_fullscreen {
                        "Replay fullscreen enabled".to_string()
                    } else {
                        "Replay split view restored".to_string()
                    });
                    handled = true;
                }
            }

            if !handled && matches!(mouse.kind, MouseEventKind::Down(MouseButton::Left)) {
                if app.event_modal_open {
                    if let Some((command, event_seq)) = detail.selected_event().and_then(|event| {
                        event_modal_copy_command(mouse, event, detail, app.event_modal_scroll)
                            .map(|command| (command, event.seq))
                    }) {
                        match crate::copy_to_clipboard(&command) {
                            Ok(()) => {
                                flash_message = Some("✓ Copied command".to_string());
                                copy_target =
                                    Some(CopyFeedbackTarget::EventModalCommand(event_seq));
                            }
                            Err(err) => flash_message = Some(err),
                        }
                    }
                    handled = true;
                } else if let Some((row_index, clicked_copy)) = timeline_mouse_hit(mouse, detail) {
                    if clicked_copy {
                        if let Some(command) =
                            detail.timeline_entries[row_index].copy_command.clone()
                        {
                            match crate::copy_to_clipboard(&command) {
                                Ok(()) => {
                                    flash_message = Some("✓ Copied command".to_string());
                                    copy_target = Some(CopyFeedbackTarget::TimelineRow(row_index));
                                }
                                Err(err) => flash_message = Some(err),
                            }
                        }
                    } else {
                        detail.set_timeline_index(row_index);
                    }
                    handled = true;
                }
            }

            if !handled {
                match mouse.kind {
                    MouseEventKind::ScrollDown => {
                        if app.event_modal_open {
                            app.event_modal_scroll = app.event_modal_scroll.saturating_add(1);
                        } else if changes_mouse_hit(mouse, detail) {
                            if let Some(changes_area) =
                                session_detail_layout(detail).map(|layout| layout.changes)
                            {
                                detail.scroll_changes_by(1, changes_area);
                            }
                        } else {
                            detail.move_selection(1);
                        }
                    }
                    MouseEventKind::ScrollUp => {
                        if app.event_modal_open {
                            app.event_modal_scroll = app.event_modal_scroll.saturating_sub(1);
                        } else if changes_mouse_hit(mouse, detail) {
                            if let Some(changes_area) =
                                session_detail_layout(detail).map(|layout| layout.changes)
                            {
                                detail.scroll_changes_by(-1, changes_area);
                            }
                        } else {
                            detail.move_selection(-1);
                        }
                    }
                    _ => {}
                }
            }
        }
        if let Some(message) = flash_message {
            app.set_flash(message);
        }
        if let Some(target) = copy_target {
            app.set_copy_feedback(target);
        }
        return;
    }

    match mouse.kind {
        MouseEventKind::ScrollDown => {
            if app.selected + 1 < app.sessions.len() {
                app.selected += 1;
            }
        }
        MouseEventKind::ScrollUp => {
            if app.selected > 0 {
                app.selected -= 1;
            }
        }
        _ => {}
    }
}

fn session_header_copy_hit(mouse: MouseEvent, detail: &DetailState) -> bool {
    let Ok((width, height)) = terminal_size() else {
        return false;
    };
    let area = Rect::new(0, 0, width, height);
    let header_height = detail_header_height(detail);
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(header_height),
            Constraint::Min(12),
            Constraint::Length(detail_footer_height(detail)),
        ])
        .split(area);
    let header = chunks[0];
    if !rect_contains(header, mouse.column, mouse.row) {
        return false;
    }
    let line_y = header.y.saturating_add(2);
    let prefix = format!("session {}  ", detail.session.session_id);
    let button_x = header
        .x
        .saturating_add(1)
        .saturating_add(display_width(&prefix) as u16);
    mouse.row == line_y
        && mouse.column >= button_x
        && mouse.column < button_x.saturating_add(copy_icon_width() as u16)
}

fn timeline_mouse_hit(mouse: MouseEvent, detail: &DetailState) -> Option<(usize, bool)> {
    let layout = session_detail_layout(detail)?;
    if !rect_contains(layout.timeline, mouse.column, mouse.row) {
        return None;
    }
    let inner_x = layout.timeline.x.saturating_add(1);
    let inner_y = layout.timeline.y.saturating_add(2);
    if mouse.column < inner_x || mouse.row < inner_y {
        return None;
    }
    let viewport = timeline_viewport(detail, layout.timeline.height);
    let row_offset = mouse.row.saturating_sub(inner_y) as usize;
    let row_index = viewport.start + row_offset;
    if row_index >= viewport.end {
        return None;
    }
    let table_layout = timeline_table_layout(layout.timeline.width);
    let copy_x = inner_x
        .saturating_add(table_layout.ordinal_width)
        .saturating_add(table_layout.spacing)
        .saturating_add(table_layout.kind_width)
        .saturating_add(table_layout.spacing)
        .saturating_add(table_layout.preview_width)
        .saturating_add(table_layout.spacing);
    let clicked_copy = detail.timeline_entries[row_index].copy_command.is_some()
        && mouse.column >= copy_x
        && mouse.column < copy_x.saturating_add(table_layout.action_width);
    Some((row_index, clicked_copy))
}

fn event_modal_copy_command(
    mouse: MouseEvent,
    event: &SessionEvent,
    detail: &DetailState,
    scroll: u16,
) -> Option<String> {
    let popup = current_event_modal_rect()?;
    if !rect_contains(popup, mouse.column, mouse.row) {
        return None;
    }
    let content = build_event_modal_content(
        event,
        detail.selected_timeline_entry()?,
        detail,
        popup.width.saturating_sub(2) as usize,
        false,
        false,
    );
    let command_line = content.command_line_index?;
    let visible_row = popup
        .y
        .saturating_add(1)
        .saturating_add(command_line)
        .saturating_sub(scroll);
    let icon_x = popup
        .x
        .saturating_add(1)
        .saturating_add(display_width("command ") as u16);
    (mouse.row == visible_row
        && mouse.column >= icon_x
        && mouse.column < icon_x.saturating_add(copy_icon_width() as u16))
    .then_some(content.command)
    .flatten()
}

fn current_event_modal_rect() -> Option<Rect> {
    let (width, height) = terminal_size().ok()?;
    Some(centered_rect(74, 72, Rect::new(0, 0, width, height)))
}

struct SessionDetailLayout {
    header: Rect,
    footer: Rect,
    timeline: Rect,
    changes: Rect,
    replay: Rect,
}

fn session_detail_layout_in_area(detail: &DetailState, area: Rect) -> Option<SessionDetailLayout> {
    if area.width == 0 || area.height == 0 {
        return None;
    }
    let header_height = detail_header_height(detail);
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(header_height),
            Constraint::Min(12),
            Constraint::Length(detail_footer_height(detail)),
        ])
        .split(area);
    if detail.replay_fullscreen {
        return Some(SessionDetailLayout {
            header: chunks[0],
            footer: chunks[2],
            timeline: Rect::default(),
            changes: Rect::default(),
            replay: chunks[1],
        });
    }
    let body = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(47), Constraint::Percentage(53)])
        .split(chunks[1]);
    let right = Layout::default()
        .direction(Direction::Vertical)
        .constraints(changes_panel_constraints(detail))
        .split(body[1]);
    Some(SessionDetailLayout {
        header: chunks[0],
        footer: chunks[2],
        timeline: body[0],
        changes: right[0],
        replay: right[1],
    })
}

fn session_detail_layout(detail: &DetailState) -> Option<SessionDetailLayout> {
    let (width, height) = terminal_size().ok()?;
    session_detail_layout_in_area(detail, Rect::new(0, 0, width, height))
}

fn replay_toggle_hit(mouse: MouseEvent, detail: &DetailState) -> Option<bool> {
    let layout = session_detail_layout(detail)?;
    if !rect_contains(layout.replay, mouse.column, mouse.row) {
        return None;
    }
    let button = if detail.replay_fullscreen {
        REPLAY_SPLIT_BUTTON
    } else {
        REPLAY_FULLSCREEN_BUTTON
    };
    let active_frame = detail
        .terminal_replay_frames
        .get(detail.replay_step)
        .filter(|_| detail.replay_mode == ReplayMode::Terminal && detail.replay_visible);
    let frame_size_label = active_frame
        .map(|frame| format!(" {}x{}", frame.cols, frame.rows))
        .unwrap_or_default();
    let horizontal_scroll_hint = active_frame
        .filter(|frame| terminal_replay_max_scroll_x(frame, layout.replay) > 0)
        .map(|_| " ←/→ horizontal scroll")
        .unwrap_or("");
    let mode_label = match detail.replay_mode {
        ReplayMode::Terminal => "terminal replay",
        ReplayMode::Text => "fallback transcript",
    };
    let status_label = replay_status_label(detail.autoplay);
    let prefix = format!(
        "Replay ({}{}) [{}]{}  ",
        mode_label, frame_size_label, status_label, horizontal_scroll_hint
    );
    let button_x = layout
        .replay
        .x
        .saturating_add(1)
        .saturating_add(display_width(&prefix) as u16);
    let title_y = layout.replay.y;
    (mouse.row == title_y
        && mouse.column >= button_x
        && mouse.column < button_x.saturating_add(display_width(button) as u16))
    .then_some(true)
}

fn changes_mouse_hit(mouse: MouseEvent, detail: &DetailState) -> bool {
    session_detail_layout(detail)
        .map(|layout| rect_contains(layout.changes, mouse.column, mouse.row))
        .unwrap_or(false)
}

fn replay_status_label(autoplay: bool) -> &'static str {
    if autoplay {
        "playing ▶"
    } else {
        "paused ⏸"
    }
}

fn rect_contains(rect: Rect, column: u16, row: u16) -> bool {
    column >= rect.x
        && column < rect.x.saturating_add(rect.width)
        && row >= rect.y
        && row < rect.y.saturating_add(rect.height)
}

#[cfg(test)]
mod tests {
    use super::{
        build_changes_lines, build_conversation_export_rows, build_display_model,
        build_terminal_replay_frames, changes_max_scroll, collect_terminal_lines,
        export_conversation_jsonl, export_timeline_rows, format_duration, format_header_outcome,
        format_outcome, format_timeline_ordinal, handle_key, load_transcript_chunks,
        repo_display_name, sanitize_inline_text, sanitize_terminal_output,
        session_detail_layout_in_area, timeline_category_color, timeline_kind_style, App,
        DeleteModalState, DetailState, FocusPane, GitCheckpointRecord, ReplayMode, SessionEvent,
        SessionFilters, SessionSummary, SessionsArgs, TerminalReplayFrame, TimeTravelModalState,
        TimeTravelPreviewResponse, TimelineCategory, TimelineEntry, TranscriptChunk,
        TEXT_REPLAY_TICK_MS, terminal_replay_end_padding, terminal_replay_max_scroll_x,
        terminal_replay_scroll,
    };
    use crate::sessions_render::{
        diff_stat_line, rendered_text_height, replay_max_scroll, strip_inline_progress_noise,
        vt100_color_to_ratatui,
    };
    use crossterm::event::{KeyCode, KeyEvent, KeyModifiers};
    use flate2::write::GzEncoder;
    use flate2::Compression;
    use ratatui::{
        layout::Rect,
        style::{Color, Modifier},
        text::Line,
    };
    use reqwest::blocking::Client;
    use serde_json::json;
    use std::{
        cell::RefCell,
        collections::{BTreeMap, HashMap},
        env, fs,
        io::{Read, Write},
        net::TcpListener,
        process::Command,
        sync::Arc,
        thread,
        time::{Duration, Instant},
    };

    fn sample_session(session_id: &str, session_name: &str) -> SessionSummary {
        SessionSummary {
            session_id: session_id.to_string(),
            session_name: session_name.to_string(),
            ..SessionSummary::default()
        }
    }

    fn sample_app(daemon_url: &str, sessions: Vec<SessionSummary>) -> App {
        App {
            client: Client::builder().build().expect("build reqwest client"),
            args: SessionsArgs {
                daemon_url: daemon_url.to_string(),
                auth_token: String::new(),
                session_id: String::new(),
                limit: 200,
                replay: false,
            },
            all_sessions: sessions.clone(),
            sessions,
            selected: 0,
            filter_menu: false,
            filter_cursor: 0,
            filters: SessionFilters::default(),
            detail: None,
            status: "Ready".to_string(),
            flash_status: None,
            deep_link: false,
            needs_terminal_clear: false,
            event_modal_open: false,
            event_modal_scroll: 0,
            hovered_timeline_copy_row: None,
            hovered_event_modal_copy: false,
            hovered_header_copy: false,
            hovered_replay_toggle: false,
            copy_feedback: None,
            rename_modal: None,
            delete_modal: None,
            time_travel_modal: None,
            pending_time_travel_handoff: None,
        }
    }

    #[test]
    fn session_filters_cycle_across_all_available_agents() {
        let mut alpha = sample_session("sess-1", "One");
        alpha.agent = "codex".to_string();
        alpha.agent_name = "Codex".to_string();

        let mut beta = sample_session("sess-2", "Two");
        beta.agent = "claude_code".to_string();
        beta.agent_name = "Claude Code".to_string();

        let mut gamma = sample_session("sess-3", "Three");
        gamma.agent = "gemini_cli".to_string();
        gamma.agent_name = "Gemini CLI".to_string();

        let mut app = sample_app("http://127.0.0.1:22000", vec![alpha, beta, gamma]);
        app.filter_cursor = 1;

        assert_eq!(
            app.field_values("agent"),
            vec![
                "".to_string(),
                "Claude Code".to_string(),
                "Codex".to_string(),
                "Gemini CLI".to_string(),
            ]
        );

        app.cycle_filter_value(1);
        assert_eq!(app.filters.agent, "Claude Code");
        app.cycle_filter_value(1);
        assert_eq!(app.filters.agent, "Codex");
        app.cycle_filter_value(1);
        assert_eq!(app.filters.agent, "Gemini CLI");
        app.cycle_filter_value(1);
        assert!(app.filters.agent.is_empty());
    }

    #[test]
    fn sanitize_terminal_output_strips_ansi_sequences() {
        let input = "\u{1b}[32mgreen\u{1b}[0m text\r\nnext";
        assert_eq!(sanitize_terminal_output(input), "green text\nnext");
    }

    #[test]
    fn sanitize_inline_text_collapses_whitespace() {
        let input = "hello\tthere\n\u{1b}[31mworld\u{1b}[0m";
        assert_eq!(sanitize_inline_text(input), "hello there world");
    }

    #[test]
    fn format_duration_standardizes_session_units_and_24h_cap() {
        assert_eq!(format_duration(100, 100), "0s");
        assert_eq!(format_duration(100, 145), "45s");
        assert_eq!(format_duration(100, 274), "2m 54s");
        assert_eq!(format_duration(100, 4_420), "1h 12m");
        assert_eq!(format_duration(100, 86_500), "24h+");
        assert_eq!(format_duration(100, 86_501), "24h+");
    }

    #[test]
    fn collect_terminal_lines_filters_noisy_terminal_redraws() {
        let events = vec![
            SessionEvent {
                event_type: "terminal.stdout".to_string(),
                payload: json!({"data": "OpenAI Codex (v0.113.0)\n"}),
                ..SessionEvent::default()
            },
            SessionEvent {
                event_type: "terminal.stdout".to_string(),
                payload: json!({"data": "model: gpt-5.4 low  /model to change\ndirectory: ~\n"}),
                ..SessionEvent::default()
            },
            SessionEvent {
                event_type: "terminal.stdout".to_string(),
                payload: json!({"data": "~Working(0s • esc to interrupt)\rWorking\r"}),
                ..SessionEvent::default()
            },
            SessionEvent {
                event_type: "terminal.stdout".to_string(),
                payload: json!({"data": "• Ran pwd\n"}),
                ..SessionEvent::default()
            },
            SessionEvent {
                event_type: "terminal.stdout".to_string(),
                payload: json!({"data": "└ /Users/alessioleodori/HelloWorld/ai_terminal2\n"}),
                ..SessionEvent::default()
            },
            SessionEvent {
                event_type: "terminal.stdout".to_string(),
                payload: json!({"data": "The repo is clean and ready.\n"}),
                ..SessionEvent::default()
            },
        ];

        assert_eq!(
            collect_terminal_lines(&events, true),
            vec![
                "• Ran pwd".to_string(),
                "└ /Users/alessioleodori/HelloWorld/ai_terminal2".to_string(),
                "The repo is clean and ready.".to_string(),
            ]
        );
    }

    #[test]
    fn build_display_model_condenses_terminal_runs() {
        let events = vec![
            SessionEvent {
                event_type: "command.recorded".to_string(),
                payload: json!({"command": "codex"}),
                ..SessionEvent::default()
            },
            SessionEvent {
                event_type: "terminal.stdout".to_string(),
                payload: json!({"data": "Wo\r"}),
                ..SessionEvent::default()
            },
            SessionEvent {
                event_type: "terminal.stdout".to_string(),
                payload: json!({"data": "Wor\r"}),
                ..SessionEvent::default()
            },
            SessionEvent {
                event_type: "terminal.stdout".to_string(),
                payload: json!({"data": "Working\r"}),
                ..SessionEvent::default()
            },
            SessionEvent {
                event_type: "terminal.stdout".to_string(),
                payload: json!({"data": "• Ran git status --short --branch\n"}),
                ..SessionEvent::default()
            },
            SessionEvent {
                event_type: "terminal.stdout".to_string(),
                payload: json!({"data": "The repo is clean.\n"}),
                ..SessionEvent::default()
            },
            SessionEvent {
                event_type: "terminal.stdout".to_string(),
                payload: json!({"data": "Tip: Use /rename to rename your threads.\n"}),
                ..SessionEvent::default()
            },
            SessionEvent {
                event_type: "process.exited".to_string(),
                payload: json!({"command": "codex"}),
                ..SessionEvent::default()
            },
        ];

        let (timeline, replay, replay_timeline_indices) = build_display_model(&events);

        assert_eq!(timeline.len(), 3);
        assert_eq!(timeline[1].event_type, "terminal.output");
        assert_eq!(
            timeline[1].summary,
            "• Ran git status --short --branch (+1 lines)"
        );
        assert_eq!(
            replay,
            vec!["• Ran git status --short --branch\nThe repo is clean.\n\n".to_string()]
        );
        assert_eq!(replay_timeline_indices, vec![1]);
    }

    #[test]
    fn timeline_kind_styles_vary_within_categories() {
        assert_eq!(
            TimelineCategory::from_event_type("command.recorded"),
            TimelineCategory::Command
        );
        assert_eq!(
            TimelineCategory::from_event_type("process.exited"),
            TimelineCategory::Process
        );
        assert_eq!(
            TimelineCategory::from_event_type("terminal.output"),
            TimelineCategory::Terminal
        );
        assert_eq!(
            TimelineCategory::from_event_type("git.commit.created"),
            TimelineCategory::Git
        );
        assert_eq!(
            TimelineCategory::from_event_type("git.commit.sess_sync"),
            TimelineCategory::Git
        );
        assert_eq!(
            TimelineCategory::from_event_type("marker.session.started"),
            TimelineCategory::Marker
        );
        assert_eq!(
            TimelineCategory::from_event_type("violation.noted"),
            TimelineCategory::Violation
        );
        assert_eq!(
            TimelineCategory::from_event_type("session.started"),
            TimelineCategory::Session
        );
        assert_eq!(
            TimelineCategory::from_event_type("custom.event"),
            TimelineCategory::Other
        );

        assert_eq!(
            timeline_kind_style("process.spawned").fg,
            Some(Color::Rgb(94, 244, 126))
        );
        assert_eq!(
            timeline_kind_style("process.exited").fg,
            Some(Color::Rgb(64, 214, 112))
        );
        assert_eq!(
            timeline_kind_style("terminal.output").fg,
            Some(Color::Rgb(84, 232, 255))
        );
        assert_eq!(
            timeline_kind_style("terminal.resize").fg,
            Some(Color::Rgb(120, 170, 255))
        );
        assert_eq!(
            timeline_kind_style("marker.session.started").fg,
            Some(Color::Rgb(255, 106, 214))
        );
        assert_eq!(
            timeline_kind_style("marker.session.finished").fg,
            Some(Color::Rgb(234, 90, 174))
        );
        assert_eq!(
            timeline_kind_style("command.custom").fg,
            Some(timeline_category_color(TimelineCategory::Command))
        );
        assert_eq!(timeline_kind_style("custom.event").fg, Some(Color::White));
        assert_ne!(
            timeline_kind_style("process.spawned").fg,
            timeline_kind_style("process.exited").fg
        );
        assert_ne!(
            timeline_kind_style("terminal.output").fg,
            timeline_kind_style("terminal.resize").fg
        );
        assert_ne!(
            timeline_kind_style("marker.session.started").fg,
            timeline_kind_style("marker.session.finished").fg
        );
    }

    #[test]
    fn collect_terminal_lines_keeps_plain_output_above_two_chars() {
        let events = vec![SessionEvent {
            event_type: "terminal.stdout".to_string(),
            payload: json!({"data": "done\n"}),
            ..SessionEvent::default()
        }];

        assert_eq!(
            collect_terminal_lines(&events, false),
            vec!["done".to_string()]
        );
    }

    #[test]
    fn collect_terminal_lines_drops_one_and_two_char_rows() {
        let events = vec![
            SessionEvent {
                event_type: "terminal.stdout".to_string(),
                payload: json!({"data": "i\n"}),
                ..SessionEvent::default()
            },
            SessionEvent {
                event_type: "terminal.stdout".to_string(),
                payload: json!({"data": "ok\n"}),
                ..SessionEvent::default()
            },
            SessionEvent {
                event_type: "terminal.stdout".to_string(),
                payload: json!({"data": "yes\n"}),
                ..SessionEvent::default()
            },
        ];

        assert_eq!(
            collect_terminal_lines(&events, false),
            vec!["yes".to_string()]
        );
    }

    #[test]
    fn replay_max_scroll_accounts_for_wrapped_lines() {
        let area = Rect::new(0, 0, 20, 6);
        let text = "1234567890123456789012345678901234567890\nok";

        assert_eq!(rendered_text_height(text, 18), 4);
        assert_eq!(replay_max_scroll(text, area), 0);

        let taller_text = format!("{}\n{}", text, "abcdefghijklmnopqrstuvwxyz0123456789");
        assert!(replay_max_scroll(&taller_text, area) > 0);
    }

    #[test]
    fn terminal_replay_end_padding_only_applies_to_last_frame() {
        assert_eq!(terminal_replay_end_padding(0, 3), 0);
        assert_eq!(terminal_replay_end_padding(2, 3), 20);
    }

    #[test]
    fn terminal_replay_scroll_includes_end_padding_rows() {
        let area = Rect::new(0, 0, 40, 12);
        let frame = TerminalReplayFrame {
            plain_text: String::new(),
            lines: vec![Line::from(""); 30],
            source_seq_end: Some(1),
            cursor_row: 29,
            last_content_row: 29,
            rows: 30,
            cols: 40,
        };

        assert_eq!(terminal_replay_scroll(&frame, area, 0), 20);
        assert_eq!(terminal_replay_scroll(&frame, area, 20), 22);
    }

    #[test]
    fn terminal_replay_max_scroll_x_detects_horizontal_overflow() {
        let area = Rect::new(0, 0, 40, 12);
        let frame = TerminalReplayFrame {
            plain_text: String::new(),
            lines: vec![Line::from(""); 10],
            source_seq_end: Some(1),
            cursor_row: 9,
            last_content_row: 9,
            rows: 10,
            cols: 60,
        };

        assert_eq!(terminal_replay_max_scroll_x(&frame, area), 22);
    }

    #[test]
    fn terminal_replay_non_final_frames_follow_cursor_when_viewport_is_shorter() {
        let area = Rect::new(0, 0, 40, 12);
        let frame = TerminalReplayFrame {
            plain_text: String::new(),
            lines: vec![Line::from(""); 30],
            source_seq_end: Some(1),
            cursor_row: 29,
            last_content_row: 29,
            rows: 30,
            cols: 40,
        };

        let padding_rows = terminal_replay_end_padding(1, 3);
        assert_eq!(padding_rows, 0);
        assert_eq!(terminal_replay_scroll(&frame, area, padding_rows), 20);
    }

    #[test]
    fn terminal_replay_keeps_top_aligned_when_cursor_is_near_top() {
        let area = Rect::new(0, 0, 40, 12);
        let frame = TerminalReplayFrame {
            plain_text: String::new(),
            lines: vec![Line::from(""); 30],
            source_seq_end: Some(1),
            cursor_row: 2,
            last_content_row: 2,
            rows: 30,
            cols: 40,
        };

        assert_eq!(terminal_replay_scroll(&frame, area, 0), 0);
    }

    #[test]
    fn terminal_replay_follows_lower_content_when_cursor_stays_above_it() {
        let area = Rect::new(0, 0, 40, 12);
        let frame = TerminalReplayFrame {
            plain_text: String::new(),
            lines: vec![Line::from(""); 30],
            source_seq_end: Some(1),
            cursor_row: 5,
            last_content_row: 29,
            rows: 30,
            cols: 40,
        };

        assert_eq!(terminal_replay_scroll(&frame, area, 0), 20);
    }

    #[test]
    fn terminal_replay_reconstructs_cursor_positioned_spacing() {
        let frames = build_terminal_replay_frames(
            &[TranscriptChunk {
                direction: "pty".to_string(),
                data: b"Hello\r\x1b[6Cthere".to_vec(),
                seq: Some(10),
                rows: None,
                cols: None,
            }],
            4,
            20,
        );

        assert_eq!(frames.len(), 1);
        assert_eq!(
            frames[0].plain_text.lines().next().unwrap_or_default(),
            "Hello there"
        );
        assert_eq!(frames[0].source_seq_end, Some(10));
    }

    #[test]
    fn terminal_replay_preserves_color_and_style() {
        let frames = build_terminal_replay_frames(
            &[TranscriptChunk {
                direction: "pty".to_string(),
                data: b"\x1b[31;1mERR\x1b[0m".to_vec(),
                seq: Some(11),
                rows: None,
                cols: None,
            }],
            2,
            8,
        );

        assert_eq!(frames.len(), 1);
        assert_eq!(frames[0].lines[0].spans[0].content.as_ref(), "ERR");
        assert_eq!(frames[0].lines[0].spans[0].style.fg, Some(Color::Red));
        assert!(frames[0].lines[0].spans[0]
            .style
            .add_modifier
            .contains(Modifier::BOLD));
        assert_eq!(
            vt100_color_to_ratatui(vt100::Color::Idx(42)),
            Color::Indexed(42)
        );
    }

    #[test]
    fn terminal_replay_uses_recorded_terminal_geometry() {
        let frames = build_terminal_replay_frames(
            &[
                TranscriptChunk {
                    direction: "resize".to_string(),
                    data: Vec::new(),
                    seq: Some(1),
                    rows: Some(6),
                    cols: Some(24),
                },
                TranscriptChunk {
                    direction: "pty".to_string(),
                    data: b"Hello\r\x1b[6Cthere".to_vec(),
                    seq: Some(2),
                    rows: None,
                    cols: None,
                },
            ],
            4,
            8,
        );

        assert_eq!(frames.len(), 1);
        assert_eq!(frames[0].rows, 6);
        assert_eq!(frames[0].cols, 24);
        assert_eq!(
            frames[0].plain_text.lines().next().unwrap_or_default(),
            "Hello there"
        );
    }

    #[test]
    fn terminal_replay_applies_recorded_resize_events_mid_session() {
        let frames = build_terminal_replay_frames(
            &[
                TranscriptChunk {
                    direction: "resize".to_string(),
                    data: Vec::new(),
                    seq: Some(1),
                    rows: Some(2),
                    cols: Some(5),
                },
                TranscriptChunk {
                    direction: "pty".to_string(),
                    data: b"abcde".to_vec(),
                    seq: Some(2),
                    rows: None,
                    cols: None,
                },
                TranscriptChunk {
                    direction: "resize".to_string(),
                    data: Vec::new(),
                    seq: Some(3),
                    rows: Some(2),
                    cols: Some(10),
                },
                TranscriptChunk {
                    direction: "pty".to_string(),
                    data: b"\x1b[H1234567890".to_vec(),
                    seq: Some(4),
                    rows: None,
                    cols: None,
                },
            ],
            2,
            5,
        );

        assert_eq!(frames.len(), 2);
        assert_eq!(frames[0].cols, 5);
        assert_eq!(frames[1].cols, 10);
        assert_eq!(
            frames[1].plain_text.lines().next().unwrap_or_default(),
            "1234567890"
        );
    }

    #[test]
    fn exact_terminal_replay_sync_uses_transcript_seq_metadata() {
        let mut detail = DetailState {
            session: SessionSummary::default(),
            events: Vec::new(),
            timeline_entries: vec![
                TimelineEntry {
                    event_index: 0,
                    event_start_index: 0,
                    event_end_index: 0,
                    seq_start: 1,
                    seq_end: 1,
                    ts_wall: 0.0,
                    event_type: "command.recorded".to_string(),
                    summary: "pwd".to_string(),
                    copy_command: Some("pwd".to_string()),
                },
                TimelineEntry {
                    event_index: 1,
                    event_start_index: 1,
                    event_end_index: 1,
                    seq_start: 2,
                    seq_end: 2,
                    ts_wall: 0.0,
                    event_type: "terminal.output".to_string(),
                    summary: "/tmp".to_string(),
                    copy_command: None,
                },
                TimelineEntry {
                    event_index: 2,
                    event_start_index: 2,
                    event_end_index: 2,
                    seq_start: 3,
                    seq_end: 3,
                    ts_wall: 0.0,
                    event_type: "process.exited".to_string(),
                    summary: "exited".to_string(),
                    copy_command: None,
                },
            ],
            replay_mode: ReplayMode::Terminal,
            focus: FocusPane::Timeline,
            replay_fullscreen: false,
            timeline_index: 0,
            text_replay_chunks: Vec::new(),
            text_replay_cached_step: None,
            text_replay_cached_value: String::new(),
            replay_timeline_indices: vec![1],
            git_checkpoint_records: Vec::new(),
            transcript_chunks: Vec::new(),
            checkpoint_records: Vec::new(),
            terminal_replay_frames: Arc::new(vec![TerminalReplayFrame {
                plain_text: "/tmp".to_string(),
                lines: vec![Line::from("/tmp")],
                source_seq_end: Some(2),
                cursor_row: 0,
                last_content_row: 0,
                rows: 1,
                cols: 4,
            }]),
            terminal_replay_cache: HashMap::new(),
            git_change_view_cache: RefCell::new(HashMap::new()),
            terminal_cache_key: None,
            pending_replay_cache_key: None,
            replay_cache_rx: None,
            replay_notice: None,
            replay_loading: false,
            replay_step: 0,
            replay_text: String::new(),
            replay_visible: false,
            changes_scroll: 0,
            replay_scroll: 0,
            replay_scroll_x: 0,
            replay_follow_end: false,
            autoplay: false,
            last_tick: Instant::now(),
        };

        detail.set_timeline_index(0);
        assert!(!detail.replay_visible);

        detail.set_timeline_index(1);
        assert!(detail.replay_visible);
        assert_eq!(detail.replay_step, 0);

        detail.set_timeline_index(2);
        assert!(detail.replay_visible);
        assert_eq!(detail.replay_step, 0);
    }

    #[test]
    fn session_detail_layout_switches_to_replay_fullscreen() {
        let mut detail = DetailState::new(SessionSummary::default(), Vec::new(), false);
        let split = session_detail_layout_in_area(&detail, Rect::new(0, 0, 120, 40))
            .expect("layout should exist");
        assert!(split.timeline.width > 0);
        assert!(split.changes.height > 0);

        detail.toggle_replay_fullscreen();
        let fullscreen = session_detail_layout_in_area(&detail, Rect::new(0, 0, 120, 40))
            .expect("layout should exist");
        assert_eq!(fullscreen.timeline.width, 0);
        assert_eq!(fullscreen.changes.height, 0);
        assert_eq!(fullscreen.replay.x, 0);
        assert_eq!(fullscreen.replay.y, split.header.height);
        assert_eq!(fullscreen.replay.width, 120);
        assert_eq!(
            fullscreen.replay.height + split.header.height + split.footer.height,
            40
        );
    }

    #[test]
    fn changes_pane_merges_file_list_with_committed_diff_stat() {
        let mut session = SessionSummary::default();
        session.changes = json!({
            "files_changed": ["agensic.bash", "rust/provenance_tui/src/main.rs", "tests/integration/test_agensic_bash_sessions.py"],
            "committed_diff_stat": "agensic.bash | 11 +++++++++++\nrust/provenance_tui/src/main.rs | 23 +++++++++++++++++++++++---\ntests/integration/test_agensic_bash_sessions.py | 27 +++++++++++++++++++++++++++\n3 files changed, 58 insertions(+), 3 deletions(-)"
        });
        let detail = DetailState::new(session, Vec::new(), false);

        let rendered = build_changes_lines(&detail)
            .iter()
            .map(ToString::to_string)
            .collect::<Vec<_>>();

        let summary_idx = rendered
            .iter()
            .position(|line| line.contains("3 files changed, 58 insertions(+), 3 deletions(-)"))
            .expect("summary should be rendered");
        let files_changed_idx = rendered
            .iter()
            .position(|line| line.contains("Changes made in this checkpoint"))
            .expect("checkpoint heading should be rendered");

        assert!(summary_idx < files_changed_idx);
        assert!(rendered
            .iter()
            .any(|line| line.contains("• agensic.bash | 11 +++++++++++")));
        assert!(!rendered.iter().any(|line| line == "Committed diff stat"));
    }

    #[test]
    fn changes_pane_shows_checkpoint_delta_and_cumulative_views() {
        let mut detail = DetailState::new(SessionSummary::default(), Vec::new(), false);
        detail.session.changes = json!({
            "files_changed": ["modifications.md"],
            "committed_diff_stat": "modifications.md | 6 ++++++\n1 file changed, 6 insertions(+)",
        });
        detail.timeline_entries = vec![TimelineEntry {
            event_index: 0,
            event_start_index: 0,
            event_end_index: 0,
            seq_start: 3,
            seq_end: 3,
            ts_wall: 0.0,
            event_type: "git.snapshot.chkpt".to_string(),
            summary: "chkpt-0003".to_string(),
            copy_command: None,
        }];
        detail.timeline_index = 0;
        detail.git_checkpoint_records = vec![GitCheckpointRecord {
            checkpoint_id: "chkpt-0003".to_string(),
            seq: 3,
            timestamp: 0,
            reason: String::new(),
            repo_root: String::new(),
            branch: String::new(),
            head: String::new(),
            comparison_base_head: String::new(),
            status_porcelain: String::new(),
            status_fingerprint: String::new(),
            tracked_patch_sha256: String::new(),
            committed_diff_stat: String::new(),
            committed_files: Vec::new(),
            worktree_diff_stat: String::new(),
            changed_files: vec!["modifications.md".to_string()],
            untracked_paths: Vec::new(),
            fingerprint: String::new(),
            delta_diff_stat: "modifications.md | 3 +++".to_string(),
            delta_files: vec!["modifications.md".to_string()],
            delta_file_markers: [("modifications.md".to_string(), "~".to_string())]
                .into_iter()
                .collect(),
            cumulative_diff_stat: "modifications.md | 6 ++++++".to_string(),
            cumulative_files: vec!["modifications.md".to_string()],
            cumulative_file_markers: [("modifications.md".to_string(), "~".to_string())]
                .into_iter()
                .collect(),
        }];

        let rendered = build_changes_lines(&detail)
            .iter()
            .map(ToString::to_string)
            .collect::<Vec<_>>();

        assert!(rendered
            .iter()
            .any(|line| line.contains("Changes made in this checkpoint (chkpt-0003)")));
        assert!(rendered
            .iter()
            .any(|line| line.contains("~ modifications.md | 3 +++")));
        assert!(rendered
            .iter()
            .any(|line| line.contains("Changes made since session start (chkpt-0003)")));
        assert!(rendered
            .iter()
            .any(|line| line.contains("~ modifications.md | 6 ++++++")));
    }

    #[test]
    fn changes_pane_uses_selected_end_snapshot_after_last_checkpoint() {
        let suffix = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|duration| duration.as_nanos())
            .unwrap_or(0);
        let repo_root = std::env::temp_dir().join(format!("sessions-end-snapshot-{suffix}"));
        fs::create_dir_all(&repo_root).expect("create repo root");

        Command::new("git")
            .args(["init"])
            .current_dir(&repo_root)
            .output()
            .expect("git init");
        Command::new("git")
            .args(["config", "user.email", "test@example.com"])
            .current_dir(&repo_root)
            .output()
            .expect("git config email");
        Command::new("git")
            .args(["config", "user.name", "Test User"])
            .current_dir(&repo_root)
            .output()
            .expect("git config name");

        fs::write(repo_root.join("base.txt"), "base\n").expect("write base file");
        Command::new("git")
            .args(["add", "base.txt"])
            .current_dir(&repo_root)
            .output()
            .expect("git add base");
        Command::new("git")
            .args(["commit", "-m", "base"])
            .current_dir(&repo_root)
            .output()
            .expect("git commit base");
        let head_start = String::from_utf8(
            Command::new("git")
                .args(["rev-parse", "HEAD"])
                .current_dir(&repo_root)
                .output()
                .expect("git rev-parse base")
                .stdout,
        )
        .expect("utf8 head")
        .trim()
        .to_string();

        fs::write(
            repo_root.join("modifications.md"),
            "# Modifications\nline 1\nline 2\n",
        )
        .expect("write first version");
        Command::new("git")
            .args(["add", "modifications.md"])
            .current_dir(&repo_root)
            .output()
            .expect("git add first");
        Command::new("git")
            .args(["commit", "-m", "commit 1"])
            .current_dir(&repo_root)
            .output()
            .expect("git commit first");
        let checkpoint_head = String::from_utf8(
            Command::new("git")
                .args(["rev-parse", "HEAD"])
                .current_dir(&repo_root)
                .output()
                .expect("git rev-parse checkpoint")
                .stdout,
        )
        .expect("utf8 checkpoint head")
        .trim()
        .to_string();

        fs::write(
            repo_root.join("modifications.md"),
            "# Modifications\nline 1\nline 2\nline 3\nline 4\nline 5\n",
        )
        .expect("write second version");
        Command::new("git")
            .args(["add", "modifications.md"])
            .current_dir(&repo_root)
            .output()
            .expect("git add second");
        Command::new("git")
            .args(["commit", "-m", "commit 2"])
            .current_dir(&repo_root)
            .output()
            .expect("git commit second");
        let head_end = String::from_utf8(
            Command::new("git")
                .args(["rev-parse", "HEAD"])
                .current_dir(&repo_root)
                .output()
                .expect("git rev-parse end")
                .stdout,
        )
        .expect("utf8 end head")
        .trim()
        .to_string();

        let mut detail = DetailState::new(SessionSummary::default(), Vec::new(), false);
        detail.session.repo_root = repo_root.to_string_lossy().into_owned();
        detail.session.head_start = head_start;
        detail.session.head_end = head_end.clone();
        detail.session.changes = json!({
            "files_changed": ["modifications.md"],
            "committed_diff_stat": "modifications.md | 6 ++++++\n1 file changed, 6 insertions(+)",
        });
        detail.events = vec![SessionEvent {
            session_id: "sess-1".to_string(),
            seq: 5,
            ts_wall: 0.0,
            ts_monotonic_ms: 0,
            event_type: "git.snapshot.end".to_string(),
            payload: json!({"head": head_end}),
        }];
        detail.timeline_entries = vec![TimelineEntry {
            event_index: 0,
            event_start_index: 0,
            event_end_index: 0,
            seq_start: 5,
            seq_end: 5,
            ts_wall: 0.0,
            event_type: "git.snapshot.end".to_string(),
            summary: "snapshot end".to_string(),
            copy_command: None,
        }];
        detail.timeline_index = 0;
        detail.git_checkpoint_records = vec![GitCheckpointRecord {
            checkpoint_id: "chkpt-0003".to_string(),
            seq: 3,
            timestamp: 0,
            reason: String::new(),
            repo_root: repo_root.to_string_lossy().into_owned(),
            branch: String::new(),
            head: checkpoint_head,
            comparison_base_head: String::new(),
            status_porcelain: String::new(),
            status_fingerprint: String::new(),
            tracked_patch_sha256: String::new(),
            committed_diff_stat: String::new(),
            committed_files: Vec::new(),
            worktree_diff_stat: String::new(),
            changed_files: vec!["modifications.md".to_string()],
            untracked_paths: Vec::new(),
            fingerprint: String::new(),
            delta_diff_stat: "modifications.md | 3 +++".to_string(),
            delta_files: vec!["modifications.md".to_string()],
            delta_file_markers: [("modifications.md".to_string(), "~".to_string())]
                .into_iter()
                .collect(),
            cumulative_diff_stat: "modifications.md | 3 +++".to_string(),
            cumulative_files: vec!["modifications.md".to_string()],
            cumulative_file_markers: [("modifications.md".to_string(), "+".to_string())]
                .into_iter()
                .collect(),
        }];

        let rendered = build_changes_lines(&detail)
            .iter()
            .map(ToString::to_string)
            .collect::<Vec<_>>();

        assert!(rendered
            .iter()
            .any(|line| line.contains("Changes made in this checkpoint (snapshot end)")));
        assert!(rendered
            .iter()
            .any(|line| line.contains("modifications.md | 3 +++")));
        assert!(rendered
            .iter()
            .any(|line| line.contains("Changes made since session start (snapshot end)")));
        assert!(rendered
            .iter()
            .any(|line| line.contains("modifications.md | 6 ++++++")));

        let _ = fs::remove_dir_all(repo_root);
    }

    #[test]
    fn changes_pane_commit_rows_reuse_cached_git_range_views() {
        let suffix = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|duration| duration.as_nanos())
            .unwrap_or(0);
        let repo_root = std::env::temp_dir().join(format!("sessions-commit-cache-{suffix}"));
        fs::create_dir_all(&repo_root).expect("create repo root");

        Command::new("git")
            .args(["init"])
            .current_dir(&repo_root)
            .output()
            .expect("git init");
        Command::new("git")
            .args(["config", "user.email", "test@example.com"])
            .current_dir(&repo_root)
            .output()
            .expect("git config email");
        Command::new("git")
            .args(["config", "user.name", "Test User"])
            .current_dir(&repo_root)
            .output()
            .expect("git config name");

        fs::write(repo_root.join("base.txt"), "base\n").expect("write base");
        Command::new("git")
            .args(["add", "base.txt"])
            .current_dir(&repo_root)
            .output()
            .expect("git add base");
        Command::new("git")
            .args(["commit", "-m", "base"])
            .current_dir(&repo_root)
            .output()
            .expect("git commit base");
        let head_start = String::from_utf8(
            Command::new("git")
                .args(["rev-parse", "HEAD"])
                .current_dir(&repo_root)
                .output()
                .expect("git rev-parse base")
                .stdout,
        )
        .expect("utf8 start head")
        .trim()
        .to_string();

        fs::write(repo_root.join("modifications.md"), "line 1\nline 2\n").expect("write first");
        Command::new("git")
            .args(["add", "modifications.md"])
            .current_dir(&repo_root)
            .output()
            .expect("git add first");
        Command::new("git")
            .args(["commit", "-m", "commit 1"])
            .current_dir(&repo_root)
            .output()
            .expect("git commit first");
        let checkpoint_head = String::from_utf8(
            Command::new("git")
                .args(["rev-parse", "HEAD"])
                .current_dir(&repo_root)
                .output()
                .expect("git rev-parse checkpoint")
                .stdout,
        )
        .expect("utf8 checkpoint head")
        .trim()
        .to_string();

        fs::write(
            repo_root.join("modifications.md"),
            "line 1\nline 2\nline 3\nline 4\nline 5\n",
        )
        .expect("write second");
        Command::new("git")
            .args(["add", "modifications.md"])
            .current_dir(&repo_root)
            .output()
            .expect("git add second");
        Command::new("git")
            .args(["commit", "-m", "commit 2"])
            .current_dir(&repo_root)
            .output()
            .expect("git commit second");
        let commit_head = String::from_utf8(
            Command::new("git")
                .args(["rev-parse", "HEAD"])
                .current_dir(&repo_root)
                .output()
                .expect("git rev-parse commit")
                .stdout,
        )
        .expect("utf8 commit head")
        .trim()
        .to_string();

        let mut detail = DetailState::new(SessionSummary::default(), Vec::new(), false);
        detail.session.repo_root = repo_root.to_string_lossy().into_owned();
        detail.session.head_start = head_start;
        detail.session.head_end = commit_head.clone();
        detail.events = vec![SessionEvent {
            session_id: "sess-1".to_string(),
            seq: 5,
            ts_wall: 0.0,
            ts_monotonic_ms: 0,
            event_type: "git.commit.created".to_string(),
            payload: json!({"sha": commit_head}),
        }];
        detail.timeline_entries = vec![TimelineEntry {
            event_index: 0,
            event_start_index: 0,
            event_end_index: 0,
            seq_start: 5,
            seq_end: 5,
            ts_wall: 0.0,
            event_type: "git.commit.created".to_string(),
            summary: "commit 2".to_string(),
            copy_command: None,
        }];
        detail.timeline_index = 0;
        detail.git_checkpoint_records = vec![GitCheckpointRecord {
            checkpoint_id: "chkpt-0001".to_string(),
            seq: 3,
            timestamp: 0,
            reason: String::new(),
            repo_root: repo_root.to_string_lossy().into_owned(),
            branch: String::new(),
            head: checkpoint_head,
            comparison_base_head: String::new(),
            status_porcelain: String::new(),
            status_fingerprint: String::new(),
            tracked_patch_sha256: String::new(),
            committed_diff_stat: String::new(),
            committed_files: Vec::new(),
            worktree_diff_stat: String::new(),
            changed_files: vec!["modifications.md".to_string()],
            untracked_paths: Vec::new(),
            fingerprint: String::new(),
            delta_diff_stat: "modifications.md | 2 ++".to_string(),
            delta_files: vec!["modifications.md".to_string()],
            delta_file_markers: [("modifications.md".to_string(), "~".to_string())]
                .into_iter()
                .collect(),
            cumulative_diff_stat: "modifications.md | 2 ++".to_string(),
            cumulative_files: vec!["modifications.md".to_string()],
            cumulative_file_markers: [("modifications.md".to_string(), "+".to_string())]
                .into_iter()
                .collect(),
        }];

        let _ = build_changes_lines(&detail);
        let first_cache_size = detail.git_change_view_cache.borrow().len();
        let _ = build_changes_lines(&detail);
        let second_cache_size = detail.git_change_view_cache.borrow().len();

        assert_eq!(first_cache_size, 2);
        assert_eq!(second_cache_size, first_cache_size);

        let _ = fs::remove_dir_all(repo_root);
    }

    #[test]
    fn changes_pane_commit_rows_skip_same_head_checkpoint_when_resolving_base() {
        let suffix = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|duration| duration.as_nanos())
            .unwrap_or(0);
        let repo_root = std::env::temp_dir().join(format!("sessions-commit-base-{suffix}"));
        fs::create_dir_all(&repo_root).expect("create repo root");

        Command::new("git")
            .args(["init"])
            .current_dir(&repo_root)
            .output()
            .expect("git init");
        Command::new("git")
            .args(["config", "user.email", "test@example.com"])
            .current_dir(&repo_root)
            .output()
            .expect("git config email");
        Command::new("git")
            .args(["config", "user.name", "Test User"])
            .current_dir(&repo_root)
            .output()
            .expect("git config name");

        fs::write(repo_root.join("base.txt"), "base\n").expect("write base");
        Command::new("git")
            .args(["add", "base.txt"])
            .current_dir(&repo_root)
            .output()
            .expect("git add base");
        Command::new("git")
            .args(["commit", "-m", "base"])
            .current_dir(&repo_root)
            .output()
            .expect("git commit base");
        let head_start = String::from_utf8(
            Command::new("git")
                .args(["rev-parse", "HEAD"])
                .current_dir(&repo_root)
                .output()
                .expect("git rev-parse base")
                .stdout,
        )
        .expect("utf8 start head")
        .trim()
        .to_string();

        fs::write(repo_root.join("modifications.md"), "line 1\nline 2\n").expect("write first");
        Command::new("git")
            .args(["add", "modifications.md"])
            .current_dir(&repo_root)
            .output()
            .expect("git add first");
        Command::new("git")
            .args(["commit", "-m", "commit 1"])
            .current_dir(&repo_root)
            .output()
            .expect("git commit first");
        let head_checkpoint = String::from_utf8(
            Command::new("git")
                .args(["rev-parse", "HEAD"])
                .current_dir(&repo_root)
                .output()
                .expect("git rev-parse checkpoint")
                .stdout,
        )
        .expect("utf8 checkpoint head")
        .trim()
        .to_string();

        fs::write(
            repo_root.join("modifications.md"),
            "line 1\nline 2\nline 3\nline 4\nline 5\n",
        )
        .expect("write second");
        Command::new("git")
            .args(["add", "modifications.md"])
            .current_dir(&repo_root)
            .output()
            .expect("git add second");
        Command::new("git")
            .args(["commit", "-m", "commit 2"])
            .current_dir(&repo_root)
            .output()
            .expect("git commit second");
        let commit_head = String::from_utf8(
            Command::new("git")
                .args(["rev-parse", "HEAD"])
                .current_dir(&repo_root)
                .output()
                .expect("git rev-parse commit")
                .stdout,
        )
        .expect("utf8 commit head")
        .trim()
        .to_string();

        let mut detail = DetailState::new(SessionSummary::default(), Vec::new(), false);
        detail.session.repo_root = repo_root.to_string_lossy().into_owned();
        detail.session.head_start = head_start;
        detail.events = vec![SessionEvent {
            session_id: "sess-1".to_string(),
            seq: 7,
            ts_wall: 0.0,
            ts_monotonic_ms: 0,
            event_type: "git.commit.created".to_string(),
            payload: json!({"sha": commit_head}),
        }];
        detail.timeline_entries = vec![TimelineEntry {
            event_index: 0,
            event_start_index: 0,
            event_end_index: 0,
            seq_start: 7,
            seq_end: 7,
            ts_wall: 0.0,
            event_type: "git.commit.created".to_string(),
            summary: "commit 2".to_string(),
            copy_command: None,
        }];
        detail.timeline_index = 0;
        detail.git_checkpoint_records = vec![
            GitCheckpointRecord {
                checkpoint_id: "chkpt-0001".to_string(),
                seq: 3,
                timestamp: 0,
                reason: String::new(),
                repo_root: repo_root.to_string_lossy().into_owned(),
                branch: String::new(),
                head: head_checkpoint,
                comparison_base_head: String::new(),
                status_porcelain: String::new(),
                status_fingerprint: String::new(),
                tracked_patch_sha256: String::new(),
                committed_diff_stat: String::new(),
                committed_files: Vec::new(),
                worktree_diff_stat: String::new(),
                changed_files: vec!["modifications.md".to_string()],
                untracked_paths: Vec::new(),
                fingerprint: String::new(),
                delta_diff_stat: "modifications.md | 2 ++".to_string(),
                delta_files: vec!["modifications.md".to_string()],
                delta_file_markers: [("modifications.md".to_string(), "~".to_string())]
                    .into_iter()
                    .collect(),
                cumulative_diff_stat: "modifications.md | 2 ++".to_string(),
                cumulative_files: vec!["modifications.md".to_string()],
                cumulative_file_markers: [("modifications.md".to_string(), "+".to_string())]
                    .into_iter()
                    .collect(),
            },
            GitCheckpointRecord {
                checkpoint_id: "chkpt-0002".to_string(),
                seq: 6,
                timestamp: 0,
                reason: String::new(),
                repo_root: repo_root.to_string_lossy().into_owned(),
                branch: String::new(),
                head: commit_head.clone(),
                comparison_base_head: String::new(),
                status_porcelain: String::new(),
                status_fingerprint: String::new(),
                tracked_patch_sha256: String::new(),
                committed_diff_stat: String::new(),
                committed_files: Vec::new(),
                worktree_diff_stat: String::new(),
                changed_files: Vec::new(),
                untracked_paths: Vec::new(),
                fingerprint: String::new(),
                delta_diff_stat: String::new(),
                delta_files: Vec::new(),
                delta_file_markers: BTreeMap::new(),
                cumulative_diff_stat: "modifications.md | 5 +++++".to_string(),
                cumulative_files: vec!["modifications.md".to_string()],
                cumulative_file_markers: [("modifications.md".to_string(), "+".to_string())]
                    .into_iter()
                    .collect(),
            },
        ];

        let rendered = build_changes_lines(&detail)
            .iter()
            .map(ToString::to_string)
            .collect::<Vec<_>>();

        assert!(rendered
            .iter()
            .any(|line| line.contains("Changes made in this checkpoint")));
        assert!(rendered
            .iter()
            .any(|line| line.contains("modifications.md | 3 +++")));
        assert!(rendered
            .iter()
            .any(|line| line.contains("Changes made since session start")));
        assert!(rendered
            .iter()
            .any(|line| line.contains("modifications.md | 5 +++++")));

        let _ = fs::remove_dir_all(repo_root);
    }

    #[test]
    fn changes_pane_commit_rows_fall_back_to_same_head_checkpoint_stats_without_older_base() {
        let suffix = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|duration| duration.as_nanos())
            .unwrap_or(0);
        let repo_root =
            std::env::temp_dir().join(format!("sessions-commit-fallback-base-{suffix}"));
        fs::create_dir_all(&repo_root).expect("create repo root");

        Command::new("git")
            .args(["init"])
            .current_dir(&repo_root)
            .output()
            .expect("git init");
        Command::new("git")
            .args(["config", "user.email", "test@example.com"])
            .current_dir(&repo_root)
            .output()
            .expect("git config email");
        Command::new("git")
            .args(["config", "user.name", "Test User"])
            .current_dir(&repo_root)
            .output()
            .expect("git config name");

        fs::write(repo_root.join("base.txt"), "base\n").expect("write base");
        Command::new("git")
            .args(["add", "base.txt"])
            .current_dir(&repo_root)
            .output()
            .expect("git add base");
        Command::new("git")
            .args(["commit", "-m", "base"])
            .current_dir(&repo_root)
            .output()
            .expect("git commit base");
        let head_start = String::from_utf8(
            Command::new("git")
                .args(["rev-parse", "HEAD"])
                .current_dir(&repo_root)
                .output()
                .expect("git rev-parse base")
                .stdout,
        )
        .expect("utf8 start head")
        .trim()
        .to_string();

        fs::write(
            repo_root.join("modifications.md"),
            "line 1\nline 2\nline 3\n",
        )
        .expect("write commit contents");
        Command::new("git")
            .args(["add", "modifications.md"])
            .current_dir(&repo_root)
            .output()
            .expect("git add commit contents");
        Command::new("git")
            .args(["commit", "-m", "commit 1"])
            .current_dir(&repo_root)
            .output()
            .expect("git commit commit contents");
        let commit_head = String::from_utf8(
            Command::new("git")
                .args(["rev-parse", "HEAD"])
                .current_dir(&repo_root)
                .output()
                .expect("git rev-parse commit")
                .stdout,
        )
        .expect("utf8 commit head")
        .trim()
        .to_string();

        let mut detail = DetailState::new(SessionSummary::default(), Vec::new(), false);
        detail.session.repo_root = repo_root.to_string_lossy().into_owned();
        detail.session.head_start = head_start;
        detail.events = vec![SessionEvent {
            session_id: "sess-1".to_string(),
            seq: 5,
            ts_wall: 0.0,
            ts_monotonic_ms: 0,
            event_type: "git.commit.created".to_string(),
            payload: json!({"sha": commit_head.clone()}),
        }];
        detail.timeline_entries = vec![TimelineEntry {
            event_index: 0,
            event_start_index: 0,
            event_end_index: 0,
            seq_start: 5,
            seq_end: 5,
            ts_wall: 0.0,
            event_type: "git.commit.created".to_string(),
            summary: "commit 1".to_string(),
            copy_command: None,
        }];
        detail.timeline_index = 0;
        detail.git_checkpoint_records = vec![GitCheckpointRecord {
            checkpoint_id: "chkpt-0001".to_string(),
            seq: 4,
            timestamp: 0,
            reason: String::new(),
            repo_root: repo_root.to_string_lossy().into_owned(),
            branch: String::new(),
            head: commit_head,
            comparison_base_head: String::new(),
            status_porcelain: String::new(),
            status_fingerprint: String::new(),
            tracked_patch_sha256: String::new(),
            committed_diff_stat: "modifications.md | 3 +++".to_string(),
            committed_files: vec!["modifications.md".to_string()],
            worktree_diff_stat: String::new(),
            changed_files: Vec::new(),
            untracked_paths: Vec::new(),
            fingerprint: String::new(),
            delta_diff_stat: "modifications.md | 3 +++".to_string(),
            delta_files: vec!["modifications.md".to_string()],
            delta_file_markers: [("modifications.md".to_string(), "+".to_string())]
                .into_iter()
                .collect(),
            cumulative_diff_stat: "modifications.md | 3 +++".to_string(),
            cumulative_files: vec!["modifications.md".to_string()],
            cumulative_file_markers: [("modifications.md".to_string(), "+".to_string())]
                .into_iter()
                .collect(),
        }];

        let rendered = build_changes_lines(&detail)
            .iter()
            .map(ToString::to_string)
            .collect::<Vec<_>>();

        assert!(rendered
            .iter()
            .any(|line| line.contains("Changes made in this checkpoint")));
        assert!(rendered
            .iter()
            .any(|line| line.contains("+ modifications.md | 3 +++")));
        assert!(rendered
            .iter()
            .any(|line| line.contains("Changes made since session start")));
        assert!(rendered
            .iter()
            .any(|line| line.contains("+ modifications.md | 3 +++")));

        let _ = fs::remove_dir_all(repo_root);
    }

    #[test]
    fn changes_pane_scrolls_without_affecting_timeline_selection() {
        let mut session = SessionSummary::default();
        session.changes = json!({
            "files_changed": (0..30).map(|idx| format!("file-{idx}.rs")).collect::<Vec<_>>(),
        });
        let events = vec![SessionEvent {
            session_id: "sess-1".to_string(),
            seq: 1,
            ts_wall: 0.0,
            ts_monotonic_ms: 0,
            event_type: "command.recorded".to_string(),
            payload: json!({"command": "pwd"}),
        }];
        let mut detail = DetailState::new(session, events, false);
        detail.focus = FocusPane::Changes;
        let timeline_index_before = detail.timeline_index;
        let changes_area = Rect::new(0, 0, 50, 10);

        assert!(changes_max_scroll(&detail, changes_area) > 0);
        detail.scroll_changes_by(3, changes_area);

        assert_eq!(detail.timeline_index, timeline_index_before);
        assert_eq!(detail.changes_scroll, 3);
    }

    #[test]
    fn uppercase_d_opens_delete_modal_for_selected_session() {
        let mut app = sample_app(
            "http://127.0.0.1:9",
            vec![sample_session("sess-1", "Release prep")],
        );

        handle_key(
            &mut app,
            KeyEvent::new(KeyCode::Char('D'), KeyModifiers::SHIFT),
        )
        .expect("handle key succeeds");

        let modal = app.delete_modal.expect("delete modal should open");
        assert_eq!(modal.session_id, "sess-1");
        assert_eq!(modal.session_name, "Release prep");
    }

    #[test]
    fn uppercase_t_opens_time_travel_modal_for_selected_timeline_entry() {
        let listener = TcpListener::bind("127.0.0.1:0").expect("bind test listener");
        let base_url = format!("http://{}", listener.local_addr().expect("listener addr"));
        let server = thread::spawn(move || {
            let (mut stream, _) = listener.accept().expect("accept request");
            let mut buffer = [0_u8; 4096];
            let read = stream.read(&mut buffer).expect("read request");
            let request = String::from_utf8_lossy(&buffer[..read]);
            assert!(request.starts_with("POST /sessions/sess-1/time-travel/preview"));
            assert!(request.contains("\"target_ts\":1234"));
            let body = r#"{"status":"ok","session_id":"sess-1","target_seq":2,"resolved_checkpoint":{"seq":2,"branch":"main","head":"abc123","worktree_diff_stat":" tracked.txt | 1 +"},"exact_match":true,"current_repo_state":{"branch":"main","head":"def456","dirty":false},"can_fork":true,"blocking_reason":"","suggested_branch":"agensic/time-travel/sess-1-2","action":"fork_branch_restore","repo_root":"/tmp/project"}"#;
            let response = format!(
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                body.len(),
                body
            );
            stream
                .write_all(response.as_bytes())
                .expect("write response");
        });
        let mut app = sample_app(&base_url, vec![sample_session("sess-1", "Release prep")]);
        app.detail = Some(DetailState::new(
            sample_session("sess-1", "Release prep"),
            vec![SessionEvent {
                seq: 2,
                ts_wall: 1234.0,
                event_type: "command.recorded".to_string(),
                payload: json!({"command":"git status"}),
                ..SessionEvent::default()
            }],
            false,
        ));

        handle_key(
            &mut app,
            KeyEvent::new(KeyCode::Char('T'), KeyModifiers::SHIFT),
        )
        .expect("handle key succeeds");

        let modal = app
            .time_travel_modal
            .expect("time travel modal should open");
        assert_eq!(modal.session_id, "sess-1");
        assert!(modal.error.is_empty());
        assert_eq!(modal.target_seq, 2);
        assert_eq!(
            modal
                .preview
                .as_ref()
                .expect("preview loaded")
                .suggested_branch,
            "agensic/time-travel/sess-1-2"
        );
        server.join().expect("server thread");
    }

    #[test]
    fn enter_on_time_travel_modal_stores_handoff_and_exits_tui_loop() {
        let listener = TcpListener::bind("127.0.0.1:0").expect("bind test listener");
        let base_url = format!("http://{}", listener.local_addr().expect("listener addr"));
        let server = thread::spawn(move || {
            let (mut stream, _) = listener.accept().expect("accept request");
            let mut buffer = [0_u8; 4096];
            let read = stream.read(&mut buffer).expect("read request");
            let request = String::from_utf8_lossy(&buffer[..read]);
            assert!(request.starts_with("POST /sessions/sess-1/time-travel/fork"));
            let body = r#"{"status":"ok","branch_name":"agensic/time-travel/sess-1-2","working_directory":"/tmp/project","launch_payload":{"agent":"codex","resolved_agent":"codex","working_directory":"/tmp/project","launch_command":["agensic","run","codex"],"source_session_id":"sess-1","source_target_seq":2,"resolved_checkpoint_seq":2,"branch_name":"agensic/time-travel/sess-1-2"}}"#;
            let response = format!(
                "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                body.len(),
                body
            );
            stream
                .write_all(response.as_bytes())
                .expect("write response");
        });
        let mut app = sample_app(&base_url, vec![sample_session("sess-1", "Release prep")]);
        app.time_travel_modal = Some(TimeTravelModalState {
            session_id: "sess-1".to_string(),
            target_seq: 2,
            event_summary: "git status".to_string(),
            preview: Some(TimeTravelPreviewResponse {
                can_fork: true,
                exact_match: true,
                resolved_checkpoint: json!({"seq": 2}),
                ..TimeTravelPreviewResponse::default()
            }),
            error: String::new(),
        });

        let should_exit = handle_key(&mut app, KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE))
            .expect("handle key succeeds");

        assert!(should_exit);
        let handoff = app
            .pending_time_travel_handoff
            .as_ref()
            .expect("handoff should be stored");
        assert_eq!(handoff.command, vec!["agensic", "run", "codex"]);
        assert_eq!(handoff.working_directory, "/tmp/project");
        assert_eq!(handoff.branch_name, "agensic/time-travel/sess-1-2");
        assert!(handoff
            .replay_metadata_json
            .contains("\"fork_branch\":\"agensic/time-travel/sess-1-2\""));
        server.join().expect("server thread");
    }

    #[test]
    fn time_travel_modal_shows_error_detail_for_failed_preview() {
        let listener = TcpListener::bind("127.0.0.1:0").expect("bind test listener");
        let base_url = format!("http://{}", listener.local_addr().expect("listener addr"));
        let server = thread::spawn(move || {
            let (mut stream, _) = listener.accept().expect("accept request");
            let mut buffer = [0_u8; 4096];
            let read = stream.read(&mut buffer).expect("read request");
            let request = String::from_utf8_lossy(&buffer[..read]);
            assert!(request.starts_with("POST /sessions/sess-1/time-travel/preview"));
            let body = r#"{"detail":"session_repo_missing"}"#;
            let response = format!(
                "HTTP/1.1 404 Not Found\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                body.len(),
                body
            );
            stream
                .write_all(response.as_bytes())
                .expect("write response");
        });
        let mut app = sample_app(&base_url, vec![sample_session("sess-1", "Release prep")]);
        app.detail = Some(DetailState::new(
            sample_session("sess-1", "Release prep"),
            vec![SessionEvent {
                seq: 2,
                event_type: "command.recorded".to_string(),
                payload: json!({"command":"git status"}),
                ..SessionEvent::default()
            }],
            false,
        ));

        handle_key(
            &mut app,
            KeyEvent::new(KeyCode::Char('T'), KeyModifiers::SHIFT),
        )
        .expect("handle key succeeds");

        let modal = app
            .time_travel_modal
            .expect("time travel modal should open");
        assert_eq!(
            modal.error,
            "preview failed: 404 Not Found (session_repo_missing)"
        );
        server.join().expect("server thread");
    }

    #[test]
    fn submit_delete_removes_selected_session_and_closes_detail() {
        let listener = TcpListener::bind("127.0.0.1:0").expect("bind test listener");
        let base_url = format!("http://{}", listener.local_addr().expect("listener addr"));
        let server = thread::spawn(move || {
            for expected_method in ["DELETE", "GET"] {
                let (mut stream, _) = listener.accept().expect("accept request");
                let mut buffer = [0_u8; 4096];
                let read = stream.read(&mut buffer).expect("read request");
                let request = String::from_utf8_lossy(&buffer[..read]);
                assert!(
                    request.starts_with(expected_method),
                    "unexpected request: {request}"
                );
                let body = if expected_method == "DELETE" {
                    r#"{"status":"ok"}"#
                } else {
                    r#"{"sessions":[{"session_id":"sess-1","status":"exited","launch_mode":"","agent":"","model":"","agent_name":"","session_name":"Kept","working_directory":"","root_command":"","transcript_path":"","started_at":0,"ended_at":0,"updated_at":0,"violation_code":"","exit_code":0,"repo_root":"","branch_start":"","branch_end":"","head_start":"","head_end":"","aggregate":{},"changes":{}}]}"#
                };
                let response = format!(
                    "HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{}",
                    body.len(),
                    body
                );
                stream
                    .write_all(response.as_bytes())
                    .expect("write response");
            }
        });

        let mut app = sample_app(
            &base_url,
            vec![
                sample_session("sess-1", "Kept"),
                sample_session("sess-2", "Delete me"),
            ],
        );
        app.selected = 1;
        app.detail = Some(DetailState::new(
            sample_session("sess-2", "Delete me"),
            Vec::new(),
            false,
        ));
        app.delete_modal = Some(DeleteModalState {
            session_id: "sess-2".to_string(),
            session_name: "Delete me".to_string(),
        });

        app.submit_delete().expect("delete request succeeds");
        server.join().expect("server thread exits");

        assert_eq!(app.sessions.len(), 1);
        assert_eq!(app.sessions[0].session_id, "sess-1");
        assert_eq!(app.selected, 0);
        assert!(app.detail.is_none());
        assert!(app.delete_modal.is_none());
        assert_eq!(app.status, "Deleted session sess-2");
    }

    #[test]
    fn strip_inline_progress_noise_removes_working_substring() {
        let input = "Read .env •Working(17s • esc to interrupt) > next";
        assert_eq!(strip_inline_progress_noise(input), "Read .env > next");
    }

    #[test]
    fn format_header_outcome_omits_violation() {
        assert_eq!(format_header_outcome(Some(0), "exited"), "exited exit=0");
    }

    #[test]
    fn format_outcome_hides_session_boundary_escape() {
        assert_eq!(
            format_outcome(Some(0), "exited", "session_boundary_escape"),
            "exited exit=0"
        );
        assert_eq!(
            format_outcome(Some(1), "failed", "escape_primitive_blocked"),
            "failed exit=1 violation=escape_primitive_blocked"
        );
    }

    #[test]
    fn repo_display_name_uses_repo_basename() {
        assert_eq!(
            repo_display_name("/Users/alessioleodori/HelloWorld/ai_terminal2"),
            "ai_terminal2"
        );
        assert_eq!(repo_display_name("/tmp/example-repo/"), "example-repo");
    }

    #[test]
    fn diff_stat_line_colors_plus_and_minus_segments() {
        let line = diff_stat_line("README.md | 242 +++++---");

        assert_eq!(line.spans.len(), 3);
        assert_eq!(line.spans[0].content.as_ref(), "README.md | 242 ");
        assert_eq!(line.spans[1].content.as_ref(), "+++++");
        assert_eq!(line.spans[1].style.fg, Some(Color::Green));
        assert_eq!(line.spans[2].content.as_ref(), "---");
        assert_eq!(line.spans[2].style.fg, Some(Color::Red));
    }

    #[test]
    fn timeline_ordinal_compacts_large_values() {
        assert_eq!(format_timeline_ordinal(42), "42");
        assert_eq!(format_timeline_ordinal(12_345), "12.3k");
        assert_eq!(format_timeline_ordinal(3_100_000), "3.1m");
    }

    #[test]
    fn load_transcript_chunks_reads_gzip_artifacts() {
        let out = env::temp_dir().join(format!(
            "agensic-transcript-loader-{}.transcript.jsonl.gz",
            std::process::id()
        ));
        let file = fs::File::create(&out).expect("create gzip transcript");
        let mut encoder = GzEncoder::new(file, Compression::default());
        encoder
            .write_all(br#"{"direction":"pty","data_b64":"aGVsbG8=","seq":5}"#)
            .expect("write payload");
        encoder.write_all(b"\n").expect("write newline");
        encoder.finish().expect("finish gzip transcript");

        let chunks = load_transcript_chunks(out.to_str().expect("valid temp path"));
        assert_eq!(chunks.len(), 1);
        assert_eq!(chunks[0].direction, "pty");
        assert_eq!(chunks[0].data, b"hello");

        let _ = fs::remove_file(out);
    }

    #[test]
    fn detail_state_uses_text_fallback_notice_when_terminal_replay_is_missing() {
        let detail = DetailState::new(
            SessionSummary {
                transcript_path: "/tmp/missing.transcript.jsonl.gz".to_string(),
                ..SessionSummary::default()
            },
            vec![SessionEvent {
                seq: 1,
                event_type: "terminal.stdout".to_string(),
                payload: json!({"data": "hello\n"}),
                ..SessionEvent::default()
            }],
            false,
        );
        assert_eq!(detail.replay_mode, ReplayMode::Text);
        assert!(detail
            .replay_notice
            .as_deref()
            .unwrap_or_default()
            .contains("Showing the cleaned transcript fallback instead."));
    }

    #[test]
    fn build_conversation_export_rows_groups_terminal_io_by_role() {
        let detail = DetailState::new(
            SessionSummary {
                session_id: "sess-1".to_string(),
                session_name: "Demo".to_string(),
                ..SessionSummary::default()
            },
            vec![
                SessionEvent {
                    seq: 10,
                    ts_wall: 1.0,
                    ts_monotonic_ms: 100,
                    event_type: "terminal.stdin".to_string(),
                    payload: json!({"data": "hello agent\n"}),
                    ..SessionEvent::default()
                },
                SessionEvent {
                    seq: 11,
                    ts_wall: 2.0,
                    ts_monotonic_ms: 200,
                    event_type: "terminal.stdout".to_string(),
                    payload: json!({"data": "hello user\n"}),
                    ..SessionEvent::default()
                },
                SessionEvent {
                    seq: 12,
                    ts_wall: 3.0,
                    ts_monotonic_ms: 300,
                    event_type: "terminal.stdout".to_string(),
                    payload: json!({"data": "more detail\n"}),
                    ..SessionEvent::default()
                },
            ],
            false,
        );

        let rows = build_conversation_export_rows(&detail);

        assert_eq!(rows.len(), 2);
        assert_eq!(rows[0].role, "user");
        assert_eq!(rows[0].source_event_type, "terminal.input");
        assert_eq!(rows[0].text, "hello agent");
        assert_eq!(rows[1].role, "agent");
        assert_eq!(rows[1].source_event_type, "terminal.output");
        assert_eq!(rows[1].text, "hello user\nmore detail");
        assert_eq!(rows[1].event_index_start, 1);
        assert_eq!(rows[1].event_index_end, 2);
    }

    #[test]
    fn export_conversation_jsonl_writes_line_delimited_rows() {
        let detail = DetailState::new(
            SessionSummary {
                session_id: "session-1234567890".to_string(),
                session_name: "Demo".to_string(),
                ..SessionSummary::default()
            },
            vec![
                SessionEvent {
                    seq: 10,
                    ts_wall: 1.0,
                    ts_monotonic_ms: 100,
                    event_type: "terminal.stdin".to_string(),
                    payload: json!({"data": "hello\n"}),
                    ..SessionEvent::default()
                },
                SessionEvent {
                    seq: 11,
                    ts_wall: 2.0,
                    ts_monotonic_ms: 200,
                    event_type: "terminal.stdout".to_string(),
                    payload: json!({"data": "world\n"}),
                    ..SessionEvent::default()
                },
            ],
            false,
        );
        let out = env::temp_dir().join(format!(
            "agensic-session-conversation-export-{}.jsonl",
            std::process::id()
        ));

        let count = export_conversation_jsonl(&detail, out.to_str().expect("valid temp path"))
            .expect("jsonl export");
        let text = fs::read_to_string(&out).expect("read jsonl export");
        let rows = text
            .lines()
            .map(|line| serde_json::from_str::<serde_json::Value>(line).expect("parse jsonl row"))
            .collect::<Vec<_>>();

        assert_eq!(count, 2);
        assert_eq!(rows.len(), 2);
        assert_eq!(
            rows[0].get("role").and_then(|value| value.as_str()),
            Some("user")
        );
        assert_eq!(
            rows[0].get("text").and_then(|value| value.as_str()),
            Some("hello")
        );
        assert_eq!(
            rows[1].get("role").and_then(|value| value.as_str()),
            Some("agent")
        );
        assert_eq!(
            rows[1].get("text").and_then(|value| value.as_str()),
            Some("world")
        );

        let _ = fs::remove_file(out);
    }

    #[test]
    fn export_timeline_rows_writes_raw_event_ranges() {
        let events = vec![
            SessionEvent {
                seq: 10,
                ts_wall: 1.0,
                ts_monotonic_ms: 100,
                event_type: "command.recorded".to_string(),
                payload: json!({"command": "pwd"}),
                ..SessionEvent::default()
            },
            SessionEvent {
                seq: 11,
                ts_wall: 2.0,
                ts_monotonic_ms: 200,
                event_type: "terminal.stdout".to_string(),
                payload: json!({"data": "hello\n"}),
                ..SessionEvent::default()
            },
            SessionEvent {
                seq: 12,
                ts_wall: 3.0,
                ts_monotonic_ms: 300,
                event_type: "terminal.stdout".to_string(),
                payload: json!({"data": "world\n"}),
                ..SessionEvent::default()
            },
        ];
        let detail = DetailState::new(
            SessionSummary {
                session_id: "session-1234567890".to_string(),
                ..SessionSummary::default()
            },
            events,
            false,
        );
        let out = env::temp_dir().join(format!(
            "agensic-session-timeline-export-{}.csv",
            std::process::id()
        ));

        export_timeline_rows(&detail, out.to_str().expect("valid temp path")).expect("csv export");

        let mut reader = csv::Reader::from_path(&out).expect("open csv export");
        let rows = reader
            .records()
            .collect::<Result<Vec<_>, _>>()
            .expect("read csv rows");
        assert_eq!(rows.len(), 2);
        assert_eq!(rows[1].get(3), Some("2"));
        assert_eq!(rows[1].get(4), Some("1"));
        assert_eq!(rows[1].get(5), Some("2"));
        assert_eq!(rows[1].get(7), Some("11"));
        assert_eq!(rows[1].get(8), Some("12"));
        assert_eq!(rows[1].get(13), Some("2"));

        let _ = fs::remove_file(out);
    }

    #[test]
    fn uppercase_r_opens_rename_modal_for_selected_session() {
        let mut app = sample_app(
            "http://127.0.0.1:9",
            vec![sample_session("sess-1", "Release prep")],
        );

        handle_key(
            &mut app,
            KeyEvent::new(KeyCode::Char('R'), KeyModifiers::SHIFT),
        )
        .expect("handle key succeeds");

        let modal = app.rename_modal.expect("rename modal should open");
        assert_eq!(modal.session_id, "sess-1");
        assert_eq!(modal.input, "Release prep");
    }

    #[test]
    fn detail_state_opens_at_end_state_without_autoplay() {
        let detail = DetailState::new(
            SessionSummary::default(),
            vec![
                SessionEvent {
                    event_type: "command.recorded".to_string(),
                    payload: json!({"command": "pwd"}),
                    ..SessionEvent::default()
                },
                SessionEvent {
                    event_type: "terminal.stdout".to_string(),
                    payload: json!({"data": "/tmp\n"}),
                    ..SessionEvent::default()
                },
                SessionEvent {
                    event_type: "process.exited".to_string(),
                    ..SessionEvent::default()
                },
            ],
            false,
        );

        assert_eq!(detail.timeline_index, 2);
        assert!(detail.replay_visible);
        assert!(detail.replay_text.contains("/tmp"));
    }

    #[test]
    fn timeline_navigation_wraps_across_boundaries_in_text_mode() {
        let mut detail = DetailState::new(
            SessionSummary::default(),
            vec![
                SessionEvent {
                    event_type: "command.recorded".to_string(),
                    payload: json!({"command": "pwd"}),
                    ..SessionEvent::default()
                },
                SessionEvent {
                    event_type: "terminal.stdout".to_string(),
                    payload: json!({"data": "/tmp\n"}),
                    ..SessionEvent::default()
                },
                SessionEvent {
                    event_type: "process.exited".to_string(),
                    ..SessionEvent::default()
                },
            ],
            false,
        );

        assert_eq!(detail.timeline_index, 2);
        assert!(detail.replay_visible);

        detail.move_selection(1);
        assert_eq!(detail.timeline_index, 0);
        assert!(!detail.replay_visible);
        assert!(detail.replay_text.is_empty());

        detail.move_selection(-1);
        assert_eq!(detail.timeline_index, 2);
        assert!(detail.replay_visible);
        assert!(detail.replay_text.contains("/tmp"));
    }

    #[test]
    fn timeline_navigation_syncs_text_replay() {
        let mut detail = DetailState::new(
            SessionSummary::default(),
            vec![
                SessionEvent {
                    event_type: "command.recorded".to_string(),
                    payload: json!({"command": "pwd"}),
                    ..SessionEvent::default()
                },
                SessionEvent {
                    event_type: "terminal.stdout".to_string(),
                    payload: json!({"data": "/tmp\n"}),
                    ..SessionEvent::default()
                },
                SessionEvent {
                    event_type: "process.exited".to_string(),
                    ..SessionEvent::default()
                },
            ],
            true,
        );

        assert_eq!(detail.timeline_index, 0);
        assert!(!detail.replay_visible);
        assert!(detail.replay_text.is_empty());

        detail.move_selection(1);
        assert_eq!(detail.timeline_index, 1);
        assert!(detail.replay_visible);
        assert!(detail.replay_text.contains("/tmp"));

        detail.move_selection(1);
        assert_eq!(detail.timeline_index, 2);
        assert!(detail.replay_text.contains("/tmp"));
    }

    #[test]
    fn autoplay_stops_at_end() {
        let mut detail = DetailState::new(
            SessionSummary::default(),
            vec![
                SessionEvent {
                    event_type: "command.recorded".to_string(),
                    payload: json!({"command": "pwd"}),
                    ..SessionEvent::default()
                },
                SessionEvent {
                    event_type: "terminal.stdout".to_string(),
                    payload: json!({"data": "/tmp\n"}),
                    ..SessionEvent::default()
                },
                SessionEvent {
                    event_type: "process.exited".to_string(),
                    ..SessionEvent::default()
                },
            ],
            false,
        );

        detail.autoplay = true;
        detail.last_tick = Instant::now() - Duration::from_millis(TEXT_REPLAY_TICK_MS + 1);
        detail.advance_autoplay();

        assert_eq!(detail.timeline_index, 2);
        assert!(!detail.autoplay);
        assert!(detail.replay_visible);
    }

    #[test]
    fn timeline_navigation_wraps_and_syncs_terminal_replay() {
        let mut detail = DetailState {
            session: SessionSummary::default(),
            events: Vec::new(),
            timeline_entries: vec![
                TimelineEntry {
                    event_index: 0,
                    event_start_index: 0,
                    event_end_index: 0,
                    seq_start: 1,
                    seq_end: 1,
                    ts_wall: 0.0,
                    event_type: "command.recorded".to_string(),
                    summary: "pwd".to_string(),
                    copy_command: Some("pwd".to_string()),
                },
                TimelineEntry {
                    event_index: 1,
                    event_start_index: 1,
                    event_end_index: 1,
                    seq_start: 2,
                    seq_end: 2,
                    ts_wall: 0.0,
                    event_type: "terminal.output".to_string(),
                    summary: "/tmp".to_string(),
                    copy_command: None,
                },
                TimelineEntry {
                    event_index: 2,
                    event_start_index: 2,
                    event_end_index: 2,
                    seq_start: 3,
                    seq_end: 3,
                    ts_wall: 0.0,
                    event_type: "process.exited".to_string(),
                    summary: "exited".to_string(),
                    copy_command: None,
                },
            ],
            replay_mode: ReplayMode::Terminal,
            focus: FocusPane::Timeline,
            replay_fullscreen: false,
            timeline_index: 2,
            text_replay_chunks: Vec::new(),
            text_replay_cached_step: None,
            text_replay_cached_value: String::new(),
            replay_timeline_indices: vec![1],
            git_checkpoint_records: Vec::new(),
            transcript_chunks: Vec::new(),
            checkpoint_records: Vec::new(),
            terminal_replay_frames: Arc::new(vec![TerminalReplayFrame {
                plain_text: "/tmp".to_string(),
                lines: vec![Line::from("/tmp")],
                source_seq_end: Some(2),
                cursor_row: 0,
                last_content_row: 0,
                rows: 1,
                cols: 4,
            }]),
            terminal_replay_cache: HashMap::new(),
            git_change_view_cache: RefCell::new(HashMap::new()),
            terminal_cache_key: None,
            pending_replay_cache_key: None,
            replay_cache_rx: None,
            replay_notice: None,
            replay_loading: false,
            replay_step: 0,
            replay_text: "/tmp".to_string(),
            replay_visible: true,
            changes_scroll: 0,
            replay_scroll: 0,
            replay_scroll_x: 0,
            replay_follow_end: false,
            autoplay: false,
            last_tick: Instant::now(),
        };

        detail.move_selection(1);
        assert_eq!(detail.timeline_index, 0);
        assert!(!detail.replay_visible);

        detail.move_selection(-1);
        assert_eq!(detail.timeline_index, 2);
        assert!(detail.replay_visible);
        assert_eq!(detail.replay_step, 0);
        assert_eq!(detail.replay_text, "/tmp");
    }
}
