use base64::engine::general_purpose::STANDARD as BASE64_STANDARD;
use base64::Engine;
use chrono::TimeZone;
use clap::Parser;
use crossterm::event::{
    self, DisableMouseCapture, EnableMouseCapture, Event, KeyCode, KeyEvent, KeyEventKind,
    MouseButton, MouseEvent, MouseEventKind,
};
use crossterm::execute;
use crossterm::terminal::{
    disable_raw_mode, enable_raw_mode, size as terminal_size, EnterAlternateScreen,
    LeaveAlternateScreen,
};
use ratatui::backend::CrosstermBackend;
use ratatui::layout::{Constraint, Direction, Layout, Rect};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span, Text};
use ratatui::widgets::{Block, Borders, Cell, Clear, Paragraph, Row, Table, TableState, Wrap};
use ratatui::Terminal;
use reqwest::blocking::Client;
use serde::Deserialize;
use serde_json::Value;
use std::cmp::{max, min};
use std::fs;
use std::io::{self, Write};
use std::path::Path;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use unicode_width::{UnicodeWidthChar, UnicodeWidthStr};
use vt100::Parser as VtParser;

const TIMELINE_PAGE_STEP: usize = 500;
const TEXT_REPLAY_TICK_MS: u64 = 60;
const TERMINAL_REPLAY_TICK_MS: u64 = TEXT_REPLAY_TICK_MS / 3;
const TERMINAL_REPLAY_END_PADDING_ROWS: u16 = 20;
const MAX_SESSION_DURATION_SECONDS: i64 = 24 * 60 * 60;
const SESSION_COPY_BUTTON: &str = "[ Copy ]";
const SESSION_COPIED_BUTTON: &str = "[   ✓   ]";
const TIMELINE_ORDINAL_WIDTH: u16 = 6;
const TIMELINE_EVENT_TYPE_WIDTH: usize = 18;
const TIMELINE_COLUMN_SPACING: u16 = 1;

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

impl ReplayMode {
    fn label(self) -> &'static str {
        match self {
            ReplayMode::Terminal => "terminal",
            ReplayMode::Text => "text",
        }
    }
}

#[derive(Clone, Debug)]
struct TimelineEntry {
    event_index: usize,
    event_start_index: usize,
    event_end_index: usize,
    seq_start: i64,
    seq_end: i64,
    ts_wall: f64,
    ts_monotonic_ms: i64,
    event_type: String,
    summary: String,
    copy_command: Option<String>,
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

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
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
    timeline_index: usize,
    text_replay_chunks: Vec<String>,
    replay_timeline_indices: Vec<usize>,
    transcript_chunks: Vec<TranscriptChunk>,
    terminal_replay_frames: Vec<TerminalReplayFrame>,
    terminal_cache_key: Option<TerminalReplayCacheKey>,
    replay_notice: Option<String>,
    replay_step: usize,
    replay_text: String,
    replay_visible: bool,
    replay_scroll: u16,
    replay_scroll_x: u16,
    replay_follow_end: bool,
    autoplay: bool,
    last_tick: Instant,
}

impl DetailState {
    fn new(session: SessionSummary, events: Vec<SessionEvent>, autoplay: bool) -> Self {
        let (timeline_entries, text_replay_chunks, replay_timeline_indices) =
            build_display_model(&events);
        let transcript_chunks = load_transcript_chunks(&session.transcript_path);
        let transcript_missing = !session.transcript_path.trim().is_empty()
            && !Path::new(session.transcript_path.trim()).is_file();
        let replay_mode = if transcript_chunks.is_empty() {
            ReplayMode::Text
        } else {
            ReplayMode::Terminal
        };
        let replay_notice = if transcript_missing {
            Some(if text_replay_chunks.is_empty() {
                "Replay unavailable: tracked transcript expired or was pruned (kept 7 days / 1 GiB total)."
                    .to_string()
            } else {
                "Terminal replay expired or was pruned (kept 7 days / 1 GiB total). Showing cleaned session transcript fallback."
                    .to_string()
            })
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
            timeline_index: initial_timeline_index,
            text_replay_chunks,
            replay_timeline_indices,
            transcript_chunks,
            terminal_replay_frames: Vec::new(),
            terminal_cache_key: None,
            replay_notice,
            replay_step: 0,
            replay_text: String::new(),
            replay_visible: false,
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

    fn ensure_terminal_replay_cache(&mut self) {
        let Some(layout) = session_detail_layout(self) else {
            return;
        };
        let viewport_rows = layout.replay.height.saturating_sub(2).max(1);
        let viewport_cols = layout.replay.width.saturating_sub(2).max(1);
        let cache_key = if transcript_has_recorded_geometry(&self.transcript_chunks) {
            TerminalReplayCacheKey::RecordedGeometry
        } else {
            TerminalReplayCacheKey::Viewport(viewport_rows, viewport_cols)
        };
        if self.terminal_cache_key == Some(cache_key) && !self.terminal_replay_frames.is_empty() {
            return;
        }
        let (rows, cols) = first_recorded_terminal_size(&self.transcript_chunks)
            .unwrap_or((viewport_rows, viewport_cols));
        self.terminal_replay_frames =
            build_terminal_replay_frames(&self.transcript_chunks, rows, cols);
        self.terminal_cache_key = Some(cache_key);
        if self.replay_mode == ReplayMode::Terminal && self.terminal_replay_frames.is_empty() {
            self.replay_mode = ReplayMode::Text;
        }
    }

    fn set_replay_mode(&mut self, mode: ReplayMode) {
        if mode == ReplayMode::Terminal {
            self.ensure_terminal_replay_cache();
            if self.terminal_replay_frames.is_empty() {
                self.replay_mode = ReplayMode::Text;
                self.sync_replay_to_timeline();
                return;
            }
        }
        self.replay_mode = mode;
        self.replay_follow_end = self.replay_mode == ReplayMode::Text;
        if self.replay_follow_end {
            self.replay_scroll = u16::MAX;
        } else {
            self.replay_scroll = 0;
        }
        self.replay_scroll_x = 0;
        self.sync_replay_to_timeline();
    }

    fn toggle_replay_mode(&mut self) {
        let next = match self.replay_mode {
            ReplayMode::Terminal => ReplayMode::Text,
            ReplayMode::Text => ReplayMode::Terminal,
        };
        self.set_replay_mode(next);
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
            ReplayMode::Text => self.text_replay_chunks[..=self.replay_step].join(""),
            ReplayMode::Terminal => self.text_replay_chunks[..=self.replay_step].join(""),
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

    fn advance_autoplay(&mut self) {
        self.ensure_terminal_replay_cache();
        if !self.autoplay || self.timeline_entries.is_empty() {
            return;
        }
        let tick_ms = match self.replay_mode {
            ReplayMode::Terminal => TERMINAL_REPLAY_TICK_MS,
            ReplayMode::Text => TEXT_REPLAY_TICK_MS,
        };
        if self.last_tick.elapsed() < Duration::from_millis(tick_ms) {
            return;
        }
        self.last_tick = Instant::now();
        if self.timeline_index + 1 < self.timeline_entries.len() {
            self.move_selection(1);
        } else {
            self.autoplay = false;
        }
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
        let next_visible = (self.timeline_index as isize + delta)
            .clamp(0, self.timeline_entries.len().saturating_sub(1) as isize)
            as usize;
        self.set_timeline_index(next_visible);
    }

    fn page_selection(&mut self, delta: isize) {
        self.move_selection(delta.saturating_mul(TIMELINE_PAGE_STEP as isize));
    }

    fn selected_timeline_entry(&self) -> Option<&TimelineEntry> {
        self.timeline_entries.get(self.timeline_index)
    }

    fn selected_event(&self) -> Option<&SessionEvent> {
        self.selected_timeline_entry()
            .and_then(|entry| self.events.get(entry.event_index))
    }

    fn cycle_focus(&mut self) {
        self.focus = match self.focus {
            FocusPane::Timeline => FocusPane::Changes,
            FocusPane::Changes => FocusPane::Replay,
            FocusPane::Replay => FocusPane::Timeline,
        };
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
    sessions: Vec<SessionSummary>,
    selected: usize,
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
    copy_feedback: Option<CopyFeedback>,
}

#[derive(Clone, Debug, Default)]
struct TerminalReplayFrame {
    plain_text: String,
    lines: Vec<Line<'static>>,
    source_seq_end: Option<i64>,
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

impl CopyFeedback {
    fn active_target(&self) -> Option<CopyFeedbackTarget> {
        (Instant::now() <= self.expires_at).then_some(self.target)
    }
}

impl App {
    fn new(client: Client, args: SessionsArgs) -> Result<Self, String> {
        let mut app = Self {
            client,
            args: args.clone(),
            sessions: Vec::new(),
            selected: 0,
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
            copy_feedback: None,
        };
        app.refresh_sessions()?;
        if !args.session_id.trim().is_empty() {
            app.open_session(args.session_id.trim(), args.replay)?;
        }
        Ok(app)
    }

    fn request(&self, path: &str) -> reqwest::blocking::RequestBuilder {
        let url = format!(
            "{}/{}",
            self.args.daemon_url.trim_end_matches('/'),
            path.trim_start_matches('/')
        );
        let builder = self.client.get(url);
        if self.args.auth_token.trim().is_empty() {
            builder
        } else {
            builder.bearer_auth(self.args.auth_token.trim())
        }
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
        self.sessions = payload.sessions;
        if self.selected >= self.sessions.len() && !self.sessions.is_empty() {
            self.selected = self.sessions.len() - 1;
        }
        self.status = format!("Loaded {} sessions", self.sessions.len());
        Ok(())
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
            "Tracked terminal replay expired or was pruned; showing fallback text transcript."
                .to_string()
        } else {
            format!("Opened session {}", session_id)
        };
        self.detail = Some(detail);
        self.event_modal_open = false;
        self.event_modal_scroll = 0;
        self.hovered_timeline_copy_row = None;
        self.hovered_event_modal_copy = false;
        self.hovered_header_copy = false;
        self.copy_feedback = None;
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

    fn export_timeline_csv(&mut self) -> Result<String, String> {
        let detail = self
            .detail
            .as_ref()
            .ok_or_else(|| "No open session".to_string())?;
        let out = default_timeline_export_path(&detail.session.session_id, "csv");
        export_timeline_rows(detail, &out)?;
        Ok(out)
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

    loop {
        if app.needs_terminal_clear {
            terminal
                .clear()
                .map_err(|e| format!("terminal clear failed: {}", e))?;
            app.needs_terminal_clear = false;
        }
        if let Some(detail) = app.detail.as_mut() {
            detail.ensure_terminal_replay_cache();
            detail.advance_autoplay();
        }
        terminal
            .draw(|frame| draw_ui(frame, &app))
            .map_err(|e| format!("draw failed: {}", e))?;

        if event::poll(Duration::from_millis(50)).map_err(|e| format!("poll failed: {}", e))? {
            match event::read().map_err(|e| format!("read failed: {}", e))? {
                Event::Key(key) if key.kind == KeyEventKind::Press => {
                    if handle_key(&mut app, key)? {
                        break;
                    }
                }
                Event::Mouse(mouse) => handle_mouse(&mut app, mouse),
                _ => {}
            }
        }
    }

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
    Ok(())
}

fn handle_key(app: &mut App, key: KeyEvent) -> Result<bool, String> {
    if app.detail.is_some() {
        let mut flash_message: Option<String> = None;
        let mut copy_target: Option<CopyFeedbackTarget> = None;
        let mut export_timeline_csv = false;
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
                            return Ok(true);
                        }
                        app.detail = None;
                        app.status = "Back to sessions".to_string();
                        app.needs_terminal_clear = true;
                    }
                    KeyCode::Char('s') => detail.cycle_focus(),
                    KeyCode::Char('t') => {
                        detail.toggle_replay_mode();
                        app.status = format!("Replay mode: {}", detail.replay_mode.label());
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
                    KeyCode::Char('E') => export_timeline_csv = true,
                    KeyCode::Left if detail.focus == FocusPane::Replay => {
                        detail.scroll_replay_horizontal(-4)
                    }
                    KeyCode::Right if detail.focus == FocusPane::Replay => {
                        detail.scroll_replay_horizontal(4)
                    }
                    KeyCode::Up | KeyCode::Char('k') => detail.move_selection(-1),
                    KeyCode::Down | KeyCode::Char('j') => detail.move_selection(1),
                    KeyCode::BackTab if detail.focus == FocusPane::Timeline => {
                        detail.page_selection(-1)
                    }
                    KeyCode::Tab if detail.focus == FocusPane::Timeline => detail.page_selection(1),
                    _ => {}
                }
            }
        }
        if export_timeline_csv {
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

    match key.code {
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
}

fn draw_browser(frame: &mut ratatui::Frame<'_>, app: &App) {
    let area = frame.area();
    frame.render_widget(Clear, area);
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
            "session", "started", "agent", "model", "repo", "branch", "duration", "outcome",
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
            "↑↓ move  Enter open  r refresh  Esc quit    {}",
            app.status_text()
        ))
        .style(Style::default().fg(Color::White)),
        chunks[1],
    );
    frame.render_widget(
        Paragraph::new(format!("sessions: {}", app.sessions.len()))
            .style(Style::default().fg(Color::DarkGray)),
        chunks[2],
    );
}

fn draw_detail(frame: &mut ratatui::Frame<'_>, app: &App, detail: &DetailState) {
    let area = frame.area();
    frame.render_widget(Clear, area);
    let header_height = detail_header_height(detail);
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(header_height),
            Constraint::Min(12),
            Constraint::Length(2),
        ])
        .split(area);
    frame.render_widget(build_header(app, detail), chunks[0]);

    let body = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(52), Constraint::Percentage(48)])
        .split(chunks[1]);
    let right = Layout::default()
        .direction(Direction::Vertical)
        .constraints(changes_panel_constraints(detail))
        .split(body[1]);
    let timeline_view = timeline_viewport(detail, body[0].height);
    let mut timeline_state = TableState::default();
    if !detail.timeline_entries.is_empty() {
        let selected = detail
            .timeline_index
            .min(detail.timeline_entries.len().saturating_sub(1));
        timeline_state.select(Some(selected.saturating_sub(timeline_view.start)));
    }
    frame.render_stateful_widget(
        build_timeline(app, detail, body[0]),
        body[0],
        &mut timeline_state,
    );
    frame.render_widget(build_changes(detail), right[0]);
    frame.render_widget(build_replay(detail, right[1]), right[1]);
    frame.render_widget(
        Paragraph::new(vec![
            Line::from(Span::styled(
                "Space: Play/Pause   Mouse wheel / ↑↓: Move  ←/→: Replay horizontal scroll when needed  Tab/Shift+Tab: Jump 500  c: Copy command  E: Export(csv)  s: Switch pane  t: Toggle replay mode  Enter: Details  Esc: Back",
                Style::default().fg(Color::Yellow),
            )),
            Line::from(app.status_text().to_string()),
        ])
        .style(Style::default().fg(Color::White)),
        chunks[2],
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
            let button = if entry.copy_command.is_some() {
                copy_button_label(copied)
            } else {
                ""
            };
            Row::new(vec![
                Cell::from(format_timeline_ordinal(row_index + 1)),
                Cell::from(truncate_display_width(
                    &sanitize_inline_text(&entry.event_type),
                    layout.kind_width as usize,
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

fn build_changes(detail: &DetailState) -> Paragraph<'static> {
    let mut lines = vec![Line::from(format!(
        "commands {}    subprocesses {}    pushes {}    transcript events {}",
        metric(detail.session.aggregate.get("command_count")),
        metric(detail.session.aggregate.get("subprocess_count")),
        metric(detail.session.aggregate.get("push_attempt_count")),
        metric(detail.session.aggregate.get("event_count")),
    ))];
    lines.push(Line::from(format!(
        "commits {}    violations {}",
        metric(detail.session.aggregate.get("commit_count")),
        if detail.session.violation_code.trim().is_empty() {
            "-".to_string()
        } else {
            sanitize_inline_text(&detail.session.violation_code)
        },
    )));

    let files: Vec<String> = detail
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
    let commits: Vec<String> = detail
        .session
        .changes
        .get("commits_created")
        .and_then(Value::as_array)
        .map(|items| {
            items
                .iter()
                .take(6)
                .map(|commit| {
                    let sha = commit.get("sha").and_then(Value::as_str).unwrap_or("-");
                    let summary = commit.get("summary").and_then(Value::as_str).unwrap_or("-");
                    format!(
                        "{} {}",
                        sanitize_inline_text(sha),
                        sanitize_inline_text(summary)
                    )
                })
                .collect()
        })
        .unwrap_or_default();

    if files.is_empty()
        && committed_diff.is_empty()
        && worktree_diff.is_empty()
        && commits.is_empty()
    {
        lines.push(Line::from(""));
        lines.push(Line::from("No repo changes recorded."));
    } else {
        if !files.is_empty() {
            lines.push(Line::from(""));
            lines.push(Line::from(Span::styled(
                "Files changed",
                Style::default()
                    .fg(Color::Cyan)
                    .add_modifier(Modifier::BOLD),
            )));
            for file in files {
                lines.push(Line::from(format!("- {}", file)));
            }
        }
        if !committed_diff.is_empty() {
            lines.push(Line::from(""));
            lines.push(Line::from(Span::styled(
                "Committed diff stat",
                Style::default()
                    .fg(Color::Cyan)
                    .add_modifier(Modifier::BOLD),
            )));
            push_diff_stat_block(&mut lines, &committed_diff);
        }
        if !worktree_diff.is_empty() {
            lines.push(Line::from(""));
            lines.push(Line::from(Span::styled(
                "Worktree diff stat",
                Style::default()
                    .fg(Color::Cyan)
                    .add_modifier(Modifier::BOLD),
            )));
            push_diff_stat_block(&mut lines, &worktree_diff);
        }
        if !commits.is_empty() {
            lines.push(Line::from(""));
            lines.push(Line::from(Span::styled(
                "Commits created",
                Style::default()
                    .fg(Color::Cyan)
                    .add_modifier(Modifier::BOLD),
            )));
            for commit in commits {
                lines.push(Line::from(commit));
            }
        }
    }
    Paragraph::new(lines)
        .block(pane_block("Changes", detail.focus == FocusPane::Changes))
        .wrap(Wrap { trim: true })
}

fn build_replay(detail: &DetailState, area: Rect) -> Paragraph<'static> {
    let focused = detail.focus == FocusPane::Replay;
    let status_label = if detail.autoplay { "playing" } else { "paused" };
    let mode_label = match detail.replay_mode {
        ReplayMode::Terminal => "Terminal, faithful screen state",
        ReplayMode::Text => "Text, session transcript",
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
    let title = format!(
        "Replay ({}{}) [{}]{} ",
        mode_label, frame_size_label, status_label, horizontal_scroll_hint
    );
    let (text, scroll_y, scroll_x) = match detail.replay_mode {
        ReplayMode::Terminal => {
            let lines = if detail.replay_visible {
                detail
                    .terminal_replay_frames
                    .get(detail.replay_step)
                    .map(|frame| {
                        let mut lines = frame.lines.clone();
                        let padding_rows = terminal_replay_end_padding(
                            detail.replay_step,
                            detail.terminal_replay_frames.len(),
                        );
                        if padding_rows > 0 {
                            lines.extend(
                                std::iter::repeat_with(|| Line::from(""))
                                    .take(padding_rows as usize),
                            );
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
            let scroll = if detail.replay_visible {
                detail
                    .terminal_replay_frames
                    .get(detail.replay_step)
                    .map(|frame| {
                        let padding_rows = terminal_replay_end_padding(
                            detail.replay_step,
                            detail.terminal_replay_frames.len(),
                        );
                        if padding_rows > 0 {
                            terminal_replay_scroll(frame, area, padding_rows)
                        } else {
                            0
                        }
                    })
                    .unwrap_or(0)
            } else {
                0
            };
            let scroll_x = active_frame
                .map(|frame| {
                    detail
                        .replay_scroll_x
                        .min(terminal_replay_max_scroll_x(frame, area))
                })
                .unwrap_or(0);
            (Text::from(lines), scroll, scroll_x)
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
        .block(pane_block(&title, focused))
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
        "git.commit.created" => format!(
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

fn changes_panel_constraints(detail: &DetailState) -> [Constraint; 2] {
    if detail_has_meaningful_changes(detail) {
        [Constraint::Percentage(40), Constraint::Percentage(60)]
    } else {
        [Constraint::Length(7), Constraint::Min(10)]
    }
}

fn detail_has_meaningful_changes(detail: &DetailState) -> bool {
    has_nonempty_array(detail.session.changes.get("files_changed"))
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
                        ts_monotonic_ms: events[start].ts_monotonic_ms,
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
                        ts_monotonic_ms: events[start].ts_monotonic_ms,
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
                    ts_monotonic_ms: event.ts_monotonic_ms,
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
                ts_monotonic_ms: event.ts_monotonic_ms,
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
    let target = path.trim();
    if target.is_empty() {
        return Vec::new();
    }
    let Ok(contents) = fs::read_to_string(target) else {
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
    let mut lines = Vec::with_capacity(rows as usize);
    for row in 0..rows.max(1) {
        let mut spans: Vec<Span<'static>> = Vec::new();
        let mut current_style: Option<Style> = None;
        let mut buffer = String::new();
        let mut col = 0u16;
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
                Some(cell) if cell.has_contents() => cell.contents(),
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
    }
    TerminalReplayFrame {
        plain_text,
        lines,
        source_seq_end,
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

fn summarize_terminal_lines(lines: &[String]) -> String {
    let first = truncate(lines.first().map(String::as_str).unwrap_or("-"), 52);
    if lines.len() <= 1 {
        first
    } else {
        format!("{} (+{} lines)", first, lines.len() - 1)
    }
}

fn pane_block(title: &str, focused: bool) -> Block<'static> {
    let border_style = if focused {
        Style::default().fg(Color::LightGreen)
    } else {
        Style::default().fg(Color::DarkGray)
    };
    let title_style = if focused {
        Style::default()
            .fg(Color::LightGreen)
            .add_modifier(Modifier::BOLD)
    } else {
        Style::default().fg(Color::White)
    };
    Block::default()
        .borders(Borders::ALL)
        .border_style(border_style)
        .title(Line::from(Span::styled(title.to_string(), title_style)))
}

pub(crate) fn copy_button_style(hovered: bool, copied: bool, selected: bool) -> Style {
    if selected {
        let mut style = Style::default()
            .fg(Color::Black)
            .add_modifier(Modifier::BOLD);
        if hovered {
            style = style.add_modifier(Modifier::UNDERLINED);
        }
        return style;
    }
    if copied {
        Style::default()
            .fg(Color::LightGreen)
            .add_modifier(Modifier::BOLD)
    } else if hovered {
        Style::default()
            .fg(Color::LightCyan)
            .add_modifier(Modifier::BOLD | Modifier::UNDERLINED)
    } else {
        Style::default()
            .fg(Color::LightGreen)
            .add_modifier(Modifier::BOLD)
    }
}

fn flush_terminal_span(spans: &mut Vec<Span<'static>>, buffer: &mut String, style: Option<Style>) {
    if buffer.is_empty() {
        return;
    }
    let text = std::mem::take(buffer);
    match style {
        Some(style) => spans.push(Span::styled(text, style)),
        None => spans.push(Span::raw(text)),
    }
}

fn terminal_cell_style(cell: &vt100::Cell) -> Style {
    let mut style = Style::default()
        .fg(vt100_color_to_ratatui(cell.fgcolor()))
        .bg(vt100_color_to_ratatui(cell.bgcolor()));
    if cell.bold() {
        style = style.add_modifier(Modifier::BOLD);
    }
    if cell.italic() {
        style = style.add_modifier(Modifier::ITALIC);
    }
    if cell.underline() {
        style = style.add_modifier(Modifier::UNDERLINED);
    }
    if cell.inverse() {
        style = style.add_modifier(Modifier::REVERSED);
    }
    style
}

fn vt100_color_to_ratatui(color: vt100::Color) -> Color {
    match color {
        vt100::Color::Default => Color::Reset,
        vt100::Color::Idx(value) => match value {
            0 => Color::Black,
            1 => Color::Red,
            2 => Color::Green,
            3 => Color::Yellow,
            4 => Color::Blue,
            5 => Color::Magenta,
            6 => Color::Cyan,
            7 => Color::Gray,
            8 => Color::DarkGray,
            9 => Color::LightRed,
            10 => Color::LightGreen,
            11 => Color::LightYellow,
            12 => Color::LightBlue,
            13 => Color::LightMagenta,
            14 => Color::LightCyan,
            15 => Color::White,
            _ => Color::Indexed(value),
        },
        vt100::Color::Rgb(r, g, b) => Color::Rgb(r, g, b),
    }
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

fn push_text_block(lines: &mut Vec<Line<'static>>, text: &str) {
    for line in text.lines() {
        lines.push(Line::from(line.to_string()));
    }
    if text.lines().next().is_none() {
        lines.push(Line::from("-"));
    }
}

fn push_diff_stat_block(lines: &mut Vec<Line<'static>>, text: &str) {
    for line in text.lines() {
        lines.push(diff_stat_line(line));
    }
    if text.lines().next().is_none() {
        lines.push(Line::from("-"));
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum DiffStatSegmentKind {
    Default,
    Addition,
    Deletion,
}

fn diff_stat_line(line: &str) -> Line<'static> {
    let mut spans = Vec::new();
    let mut buffer = String::new();
    let mut current_kind = DiffStatSegmentKind::Default;

    for ch in line.chars() {
        let next_kind = match ch {
            '+' => DiffStatSegmentKind::Addition,
            '-' => DiffStatSegmentKind::Deletion,
            _ => DiffStatSegmentKind::Default,
        };
        if !buffer.is_empty() && next_kind != current_kind {
            push_diff_stat_span(&mut spans, &mut buffer, current_kind);
        }
        current_kind = next_kind;
        buffer.push(ch);
    }
    push_diff_stat_span(&mut spans, &mut buffer, current_kind);

    Line::from(spans)
}

fn push_diff_stat_span(
    spans: &mut Vec<Span<'static>>,
    buffer: &mut String,
    kind: DiffStatSegmentKind,
) {
    if buffer.is_empty() {
        return;
    }
    let text = std::mem::take(buffer);
    let span = match kind {
        DiffStatSegmentKind::Default => Span::raw(text),
        DiffStatSegmentKind::Addition => Span::styled(text, Style::default().fg(Color::Green)),
        DiffStatSegmentKind::Deletion => Span::styled(text, Style::default().fg(Color::Red)),
    };
    spans.push(span);
}

fn metric(value: Option<&Value>) -> String {
    match value {
        Some(Value::Number(number)) => number.to_string(),
        Some(Value::String(text)) if !text.trim().is_empty() => sanitize_inline_text(text),
        _ => "-".to_string(),
    }
}

fn collapse_blank_runs(value: &str, max_blank_lines: usize) -> String {
    let mut output: Vec<&str> = Vec::new();
    let mut blank_run = 0usize;
    for line in value.lines() {
        if line.trim().is_empty() {
            blank_run += 1;
            if blank_run <= max_blank_lines {
                output.push(line);
            }
        } else {
            blank_run = 0;
            output.push(line);
        }
    }
    output.join("\n")
}

fn replay_max_scroll(value: &str, area: Rect) -> u16 {
    let content_lines = rendered_text_height(value, area.width.saturating_sub(2).max(1) as usize);
    let visible_lines = area.height.saturating_sub(2).max(1) as usize;
    content_lines
        .saturating_sub(visible_lines)
        .min(u16::MAX as usize) as u16
}

fn terminal_replay_end_padding(frame_index: usize, total_frames: usize) -> u16 {
    if total_frames > 0 && frame_index + 1 >= total_frames {
        TERMINAL_REPLAY_END_PADDING_ROWS
    } else {
        0
    }
}

fn terminal_replay_scroll(frame: &TerminalReplayFrame, area: Rect, padding_rows: u16) -> u16 {
    let visible_lines = area.height.saturating_sub(2).max(1);
    frame
        .rows
        .saturating_add(padding_rows)
        .saturating_sub(visible_lines)
}

fn terminal_replay_max_scroll_x(frame: &TerminalReplayFrame, area: Rect) -> u16 {
    let visible_cols = area.width.saturating_sub(2).max(1);
    frame.cols.saturating_sub(visible_cols)
}

fn rendered_text_height(value: &str, width: usize) -> usize {
    value
        .lines()
        .map(|line| {
            let len = line.chars().count();
            if len == 0 {
                1
            } else {
                ((len - 1) / width.max(1)) + 1
            }
        })
        .sum::<usize>()
        .max(1)
}

fn strip_inline_progress_noise(value: &str) -> String {
    let mut output = value.to_string();
    loop {
        let Some(start) = output.find("Working(") else {
            break;
        };
        let Some(relative_end) = output[start..].find(')') else {
            break;
        };
        let mut remove_start = start;
        while remove_start > 0 {
            let prev = output[..remove_start].chars().last().unwrap_or(' ');
            if matches!(prev, ' ' | '\t' | '•' | '~') {
                remove_start -= prev.len_utf8();
            } else {
                break;
            }
        }
        let remove_end = start + relative_end + 1;
        output.replace_range(remove_start..remove_end, "");
    }
    output
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

fn default_timeline_export_path(session_id: &str, ext: &str) -> String {
    format!(
        "{}/timeline_export_{}_{}.{}",
        default_export_dir(),
        export_session_slug(session_id),
        now_epoch_seconds(),
        ext
    )
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
            "ts_monotonic_ms",
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
                entry.ts_monotonic_ms.to_string(),
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
    if app.detail.is_some() {
        let mut flash_message: Option<String> = None;
        let mut copy_target: Option<CopyFeedbackTarget> = None;
        let mut handled = false;
        {
            let detail = app.detail.as_mut().expect("detail checked above");
            app.hovered_header_copy = session_header_copy_hit(mouse, detail);
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
                        } else {
                            detail.move_selection(1);
                        }
                    }
                    MouseEventKind::ScrollUp => {
                        if app.event_modal_open {
                            app.event_modal_scroll = app.event_modal_scroll.saturating_sub(1);
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
            Constraint::Length(2),
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
    timeline: Rect,
    replay: Rect,
}

fn session_detail_layout(detail: &DetailState) -> Option<SessionDetailLayout> {
    let (width, height) = terminal_size().ok()?;
    let area = Rect::new(0, 0, width, height);
    let header_height = detail_header_height(detail);
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(header_height),
            Constraint::Min(12),
            Constraint::Length(2),
        ])
        .split(area);
    let body = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(52), Constraint::Percentage(48)])
        .split(chunks[1]);
    let right = Layout::default()
        .direction(Direction::Vertical)
        .constraints(changes_panel_constraints(detail))
        .split(body[1]);
    Some(SessionDetailLayout {
        timeline: body[0],
        replay: right[1],
    })
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
        build_display_model, build_terminal_replay_frames, collect_terminal_lines, diff_stat_line,
        export_timeline_rows, format_duration, format_header_outcome, format_outcome,
        format_timeline_ordinal, rendered_text_height, replay_max_scroll, repo_display_name,
        sanitize_inline_text, sanitize_terminal_output, strip_inline_progress_noise,
        terminal_replay_end_padding, terminal_replay_max_scroll_x, terminal_replay_scroll,
        vt100_color_to_ratatui, DetailState, FocusPane, ReplayMode, SessionEvent, SessionSummary,
        TerminalReplayFrame, TimelineEntry, TranscriptChunk,
    };
    use ratatui::{
        layout::Rect,
        style::{Color, Modifier},
        text::Line,
    };
    use serde_json::json;
    use std::{env, fs, time::Instant};

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
            rows: 30,
            cols: 40,
        };

        assert_eq!(terminal_replay_scroll(&frame, area, 0), 20);
        assert_eq!(terminal_replay_scroll(&frame, area, 20), 40);
    }

    #[test]
    fn terminal_replay_max_scroll_x_detects_horizontal_overflow() {
        let area = Rect::new(0, 0, 40, 12);
        let frame = TerminalReplayFrame {
            plain_text: String::new(),
            lines: vec![Line::from(""); 10],
            source_seq_end: Some(1),
            rows: 10,
            cols: 60,
        };

        assert_eq!(terminal_replay_max_scroll_x(&frame, area), 22);
    }

    #[test]
    fn terminal_replay_non_final_frames_stay_top_aligned() {
        let area = Rect::new(0, 0, 40, 12);
        let frame = TerminalReplayFrame {
            plain_text: String::new(),
            lines: vec![Line::from(""); 30],
            source_seq_end: Some(1),
            rows: 30,
            cols: 40,
        };

        let padding_rows = terminal_replay_end_padding(1, 3);
        assert_eq!(padding_rows, 0);
        let scroll = if padding_rows > 0 {
            terminal_replay_scroll(&frame, area, padding_rows)
        } else {
            0
        };
        assert_eq!(scroll, 0);
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
                    ts_monotonic_ms: 0,
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
                    ts_monotonic_ms: 0,
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
                    ts_monotonic_ms: 0,
                    event_type: "process.exited".to_string(),
                    summary: "exited".to_string(),
                    copy_command: None,
                },
            ],
            replay_mode: ReplayMode::Terminal,
            focus: FocusPane::Timeline,
            timeline_index: 0,
            text_replay_chunks: Vec::new(),
            replay_timeline_indices: vec![1],
            transcript_chunks: Vec::new(),
            terminal_replay_frames: vec![TerminalReplayFrame {
                plain_text: "/tmp".to_string(),
                lines: vec![Line::from("/tmp")],
                source_seq_end: Some(2),
                rows: 1,
                cols: 4,
            }],
            terminal_cache_key: None,
            replay_notice: None,
            replay_step: 0,
            replay_text: String::new(),
            replay_visible: false,
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
        assert_eq!(rows[1].get(14), Some("2"));

        let _ = fs::remove_file(out);
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
}
