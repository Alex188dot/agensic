use chrono::TimeZone;
use clap::Parser;
use crossterm::event::{
    self, DisableMouseCapture, EnableMouseCapture, Event, KeyCode, KeyEvent, KeyEventKind,
    MouseEvent, MouseEventKind,
};
use crossterm::execute;
use crossterm::terminal::{
    disable_raw_mode, enable_raw_mode, size as terminal_size, EnterAlternateScreen,
    LeaveAlternateScreen,
};
use ratatui::backend::CrosstermBackend;
use ratatui::layout::{Constraint, Direction, Layout, Rect};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, Borders, Cell, Clear, Paragraph, Row, Table, TableState, Wrap};
use ratatui::Terminal;
use reqwest::blocking::Client;
use serde::Deserialize;
use serde_json::Value;
use std::cmp::{max, min};
use std::io::{self, Write};
use std::time::{Duration, Instant};

const TIMELINE_PAGE_STEP: usize = 500;

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

#[derive(Clone, Debug)]
struct TimelineEntry {
    event_index: usize,
    event_type: String,
    summary: String,
}

struct DetailState {
    session: SessionSummary,
    events: Vec<SessionEvent>,
    timeline_entries: Vec<TimelineEntry>,
    focus: FocusPane,
    timeline_index: usize,
    replay_chunks: Vec<String>,
    replay_step: usize,
    replay_text: String,
    replay_scroll: u16,
    replay_follow_end: bool,
    autoplay: bool,
    last_tick: Instant,
}

impl DetailState {
    fn new(session: SessionSummary, events: Vec<SessionEvent>, autoplay: bool) -> Self {
        let (timeline_entries, replay_chunks) = build_display_model(&events);
        let initial_timeline_index = timeline_entries
            .iter()
            .position(|entry| entry.event_type == "command.recorded")
            .unwrap_or(0);
        let mut out = Self {
            session,
            events,
            timeline_entries,
            focus: if autoplay {
                FocusPane::Replay
            } else {
                FocusPane::Timeline
            },
            timeline_index: initial_timeline_index,
            replay_chunks,
            replay_step: 0,
            replay_text: String::new(),
            replay_scroll: 0,
            replay_follow_end: true,
            autoplay,
            last_tick: Instant::now(),
        };
        if out.autoplay {
            out.rebuild_replay_text(0);
        } else if !out.replay_chunks.is_empty() {
            let last = out.replay_chunks.len().saturating_sub(1);
            out.rebuild_replay_text(last);
        }
        out
    }

    fn rebuild_replay_text(&mut self, step: usize) {
        self.replay_step = min(step, self.replay_chunks.len().saturating_sub(1));
        if self.replay_chunks.is_empty() {
            self.replay_text.clear();
            return;
        }
        self.replay_text = self.replay_chunks[..=self.replay_step].join("");
        if self.replay_follow_end {
            self.replay_scroll = u16::MAX;
        }
    }

    fn seek_relative(&mut self, delta: isize) {
        if self.replay_chunks.is_empty() {
            return;
        }
        let current = self.replay_step as isize;
        let next = min(
            self.replay_chunks.len().saturating_sub(1) as isize,
            max(0, current + delta),
        ) as usize;
        self.rebuild_replay_text(next);
    }

    fn advance_autoplay(&mut self) {
        if !self.autoplay || self.replay_chunks.is_empty() {
            return;
        }
        if self.last_tick.elapsed() < Duration::from_millis(60) {
            return;
        }
        self.last_tick = Instant::now();
        if self.replay_step + 1 < self.replay_chunks.len() {
            self.rebuild_replay_text(self.replay_step + 1);
        } else {
            self.autoplay = false;
        }
    }

    fn move_selection(&mut self, delta: isize) {
        if self.timeline_entries.is_empty() {
            self.timeline_index = 0;
            return;
        }
        let next_visible = (self.timeline_index as isize + delta)
            .clamp(0, self.timeline_entries.len().saturating_sub(1) as isize)
            as usize;
        self.timeline_index = next_visible;
    }

    fn page_selection(&mut self, delta: isize) {
        self.move_selection(delta.saturating_mul(TIMELINE_PAGE_STEP as isize));
    }

    fn selected_event(&self) -> Option<&SessionEvent> {
        self.timeline_entries
            .get(self.timeline_index)
            .and_then(|entry| self.events.get(entry.event_index))
    }

    fn cycle_focus(&mut self) {
        self.focus = match self.focus {
            FocusPane::Timeline => FocusPane::Changes,
            FocusPane::Changes => FocusPane::Replay,
            FocusPane::Replay => FocusPane::Timeline,
        };
    }

    fn scroll_replay(&mut self, delta: isize) {
        self.replay_follow_end = false;
        if delta < 0 {
            self.replay_scroll = self
                .replay_scroll
                .saturating_sub(delta.unsigned_abs() as u16);
        } else {
            self.replay_scroll = self.replay_scroll.saturating_add(delta as u16);
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
    deep_link: bool,
    needs_terminal_clear: bool,
    event_modal_open: bool,
    event_modal_scroll: u16,
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
            deep_link: !args.session_id.trim().is_empty(),
            needs_terminal_clear: true,
            event_modal_open: false,
            event_modal_scroll: 0,
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

        self.detail = Some(DetailState::new(session, events_payload.events, replay));
        self.event_modal_open = false;
        self.event_modal_scroll = 0;
        self.status = format!("Opened session {}", session_id);
        self.needs_terminal_clear = true;
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

    loop {
        if app.needs_terminal_clear {
            terminal
                .clear()
                .map_err(|e| format!("terminal clear failed: {}", e))?;
            app.needs_terminal_clear = false;
        }
        if let Some(detail) = app.detail.as_mut() {
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
    if let Some(detail) = app.detail.as_mut() {
        if app.event_modal_open {
            match key.code {
                KeyCode::Esc | KeyCode::Enter => {
                    app.event_modal_open = false;
                    app.event_modal_scroll = 0;
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
            return Ok(false);
        }

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
            KeyCode::Char(' ') => {
                if detail.autoplay {
                    detail.autoplay = false;
                } else if !detail.replay_chunks.is_empty() {
                    if detail.replay_step + 1 >= detail.replay_chunks.len() {
                        detail.rebuild_replay_text(0);
                    }
                    detail.autoplay = true;
                    detail.focus = FocusPane::Replay;
                    detail.replay_follow_end = true;
                    detail.replay_scroll = u16::MAX;
                    detail.last_tick = Instant::now();
                }
            }
            KeyCode::Enter => {
                if detail.selected_event().is_some() {
                    app.event_modal_open = true;
                    app.event_modal_scroll = 0;
                }
            }
            KeyCode::Up | KeyCode::Char('k') if detail.focus == FocusPane::Timeline => {
                detail.move_selection(-1);
            }
            KeyCode::Down | KeyCode::Char('j') if detail.focus == FocusPane::Timeline => {
                detail.move_selection(1);
            }
            KeyCode::Up | KeyCode::Char('k') if detail.focus == FocusPane::Replay => {
                detail.scroll_replay(-1);
            }
            KeyCode::Down | KeyCode::Char('j') if detail.focus == FocusPane::Replay => {
                detail.scroll_replay(1);
            }
            KeyCode::BackTab if detail.focus == FocusPane::Timeline => detail.page_selection(-1),
            KeyCode::Tab if detail.focus == FocusPane::Timeline => detail.page_selection(1),
            KeyCode::Left | KeyCode::Char('h') if detail.focus == FocusPane::Replay => {
                detail.replay_follow_end = false;
                detail.seek_relative(-1);
            }
            KeyCode::Right | KeyCode::Char('l') if detail.focus == FocusPane::Replay => {
                detail.replay_follow_end = false;
                detail.seek_relative(1);
            }
            _ => {}
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
                Cell::from(truncate(&session.repo_root, 28)),
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
            .title("Agensic Sessions"),
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
            app.status
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
    frame.render_widget(build_header(detail), chunks[0]);

    let body = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(52), Constraint::Percentage(48)])
        .split(chunks[1]);
    let right = Layout::default()
        .direction(Direction::Vertical)
        .constraints(changes_panel_constraints(detail))
        .split(body[1]);
    frame.render_widget(build_timeline(detail, body[0].height), body[0]);
    frame.render_widget(build_changes(detail), right[0]);
    frame.render_widget(build_replay(detail, right[1]), right[1]);
    frame.render_widget(
        Paragraph::new(
            "↑↓ timeline  mouse wheel scrolls timeline  Tab/Shift+Tab jump 500  s switch pane  ←→ seek replay  Enter details  space play/pause  Esc back",
        )
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
        if let Some(event) = detail.selected_event() {
            draw_event_modal(frame, event, detail, app.event_modal_scroll);
        }
    }
}

fn build_header(detail: &DetailState) -> Paragraph<'static> {
    let actor = if detail.session.agent_name.trim().is_empty() {
        detail.session.agent.as_str()
    } else {
        detail.session.agent_name.as_str()
    };
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
        Line::from(format!(
            "session {}    duration {}    started {}",
            detail.session.session_id,
            format_duration(detail.session.started_at, detail.session.ended_at),
            format_ts(detail.session.started_at),
        )),
    ];
    if !detail.session.repo_root.trim().is_empty() {
        lines.push(Line::from(format!(
            "repo {}",
            sanitize_inline_text(&detail.session.repo_root)
        )));
    }
    if !detail.session.branch_start.trim().is_empty()
        || !detail.session.branch_end.trim().is_empty()
        || !detail.session.head_start.trim().is_empty()
        || !detail.session.head_end.trim().is_empty()
    {
        lines.push(Line::from(format!(
            "branch {} -> {}    head {} -> {}",
            fallback_text(&sanitize_inline_text(&detail.session.branch_start)),
            fallback_text(&sanitize_inline_text(&detail.session.branch_end)),
            truncate(&sanitize_inline_text(&detail.session.head_start), 12),
            truncate(&sanitize_inline_text(&detail.session.head_end), 12),
        )));
    }
    Paragraph::new(lines)
        .block(pane_block("Header", false))
        .wrap(Wrap { trim: true })
}

fn build_timeline(detail: &DetailState, height: u16) -> Paragraph<'static> {
    let total = detail.timeline_entries.len();
    let selected = detail.timeline_index.min(total.saturating_sub(1));
    let visible_rows = height.saturating_sub(2).max(1) as usize;
    let start = selected.saturating_sub(visible_rows / 2);
    let end = min(total, start + visible_rows);
    let mut lines: Vec<Line<'static>> = Vec::new();
    for row_index in start..end {
        let entry = &detail.timeline_entries[row_index];
        let marker = if row_index == selected { ">>" } else { "  " };
        let style = if row_index == selected {
            Style::default().fg(Color::Black).bg(Color::LightGreen)
        } else {
            Style::default()
        };
        let line = format!(
            "{} {:>4}  {:<18}  {}",
            marker,
            row_index + 1,
            truncate(&sanitize_inline_text(&entry.event_type), 18),
            entry.summary.clone(),
        );
        lines.push(Line::from(Span::styled(line, style)));
    }
    if lines.is_empty() {
        lines.push(Line::from("(no recorded events)"));
    }
    Paragraph::new(lines)
        .block(pane_block("Timeline", detail.focus == FocusPane::Timeline))
        .wrap(Wrap { trim: true })
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
            push_text_block(&mut lines, &committed_diff);
        }
        if !worktree_diff.is_empty() {
            lines.push(Line::from(""));
            lines.push(Line::from(Span::styled(
                "Worktree diff stat",
                Style::default()
                    .fg(Color::Cyan)
                    .add_modifier(Modifier::BOLD),
            )));
            push_text_block(&mut lines, &worktree_diff);
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
    let title = if detail.autoplay {
        "Replay (playing)"
    } else {
        "Replay (paused)"
    };
    let display = if detail.replay_text.is_empty() {
        "(no terminal stdout recorded)".to_string()
    } else {
        collapse_blank_runs(&detail.replay_text, 2)
    };
    let max_scroll = replay_max_scroll(&display, area);
    let scroll = if detail.replay_follow_end {
        max_scroll
    } else {
        detail.replay_scroll.min(max_scroll)
    };
    Paragraph::new(display)
        .block(pane_block(title, focused))
        .scroll((scroll, 0))
        .wrap(Wrap { trim: false })
}

fn draw_event_modal(
    frame: &mut ratatui::Frame<'_>,
    event: &SessionEvent,
    detail: &DetailState,
    scroll: u16,
) {
    let popup = centered_rect(74, 72, frame.area());
    let lines = build_event_modal_lines(event, detail);
    let panel = Paragraph::new(lines)
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
    format!("{}s", secs)
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
    if !violation_code.trim().is_empty() {
        out.push_str(&format!(" violation={}", violation_code));
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

fn build_event_modal_lines(event: &SessionEvent, detail: &DetailState) -> Vec<Line<'static>> {
    let mut lines = vec![
        Line::from(format!("session: {}", detail.session.session_id)),
        Line::from(format!("seq: {}", event.seq)),
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

    if let Some(command) = event.payload.get("command").and_then(Value::as_str) {
        lines.push(Line::from(""));
        lines.push(Line::from(Span::styled(
            "command",
            Style::default()
                .fg(Color::Cyan)
                .add_modifier(Modifier::BOLD),
        )));
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
    lines
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

fn build_display_model(events: &[SessionEvent]) -> (Vec<TimelineEntry>, Vec<String>) {
    let mut timeline_entries = Vec::new();
    let mut replay_chunks = Vec::new();
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
                    timeline_entries.push(TimelineEntry {
                        event_index: block.event_index,
                        event_type: "terminal.output".to_string(),
                        summary: block.summary,
                    });
                    replay_chunks.push(block.replay_text);
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
                        event_type: "terminal.input".to_string(),
                        summary: block.summary,
                    });
                }
            }
            _ => {
                timeline_entries.push(TimelineEntry {
                    event_index: index,
                    event_type: event.event_type.clone(),
                    summary: event_summary(event),
                });
                index += 1;
            }
        }
    }

    if timeline_entries.is_empty() {
        for (idx, event) in events.iter().enumerate() {
            timeline_entries.push(TimelineEntry {
                event_index: idx,
                event_type: event.event_type.clone(),
                summary: event_summary(event),
            });
        }
    }

    (timeline_entries, replay_chunks)
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

fn push_text_block(lines: &mut Vec<Line<'static>>, text: &str) {
    for line in text.lines() {
        lines.push(Line::from(line.to_string()));
    }
    if text.lines().next().is_none() {
        lines.push(Line::from("-"));
    }
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
    if let Some(detail) = app.detail.as_mut() {
        match mouse.kind {
            MouseEventKind::ScrollDown => {
                if app.event_modal_open {
                    app.event_modal_scroll = app.event_modal_scroll.saturating_add(1);
                } else if mouse_is_in_replay_pane(mouse, detail) {
                    detail.scroll_replay(2);
                } else if mouse_is_in_timeline_pane(mouse, detail) {
                    detail.move_selection(1);
                } else {
                    detail.scroll_replay(2);
                }
            }
            MouseEventKind::ScrollUp => {
                if app.event_modal_open {
                    app.event_modal_scroll = app.event_modal_scroll.saturating_sub(1);
                } else if mouse_is_in_replay_pane(mouse, detail) {
                    detail.scroll_replay(-2);
                } else if mouse_is_in_timeline_pane(mouse, detail) {
                    detail.move_selection(-1);
                } else {
                    detail.scroll_replay(-2);
                }
            }
            _ => {}
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

fn mouse_is_in_timeline_pane(mouse: MouseEvent, detail: &DetailState) -> bool {
    session_detail_layout(detail)
        .map(|layout| rect_contains(layout.timeline, mouse.column, mouse.row))
        .unwrap_or(false)
}

fn mouse_is_in_replay_pane(mouse: MouseEvent, detail: &DetailState) -> bool {
    session_detail_layout(detail)
        .map(|layout| rect_contains(layout.replay, mouse.column, mouse.row))
        .unwrap_or(false)
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
        build_display_model, collect_terminal_lines, format_header_outcome, rendered_text_height,
        replay_max_scroll, sanitize_inline_text, sanitize_terminal_output,
        strip_inline_progress_noise, SessionEvent,
    };
    use ratatui::layout::Rect;
    use serde_json::json;

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

        let (timeline, replay) = build_display_model(&events);

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
    fn strip_inline_progress_noise_removes_working_substring() {
        let input = "Read .env •Working(17s • esc to interrupt) > next";
        assert_eq!(strip_inline_progress_noise(input), "Read .env > next");
    }

    #[test]
    fn format_header_outcome_omits_violation() {
        assert_eq!(format_header_outcome(Some(0), "exited"), "exited exit=0");
    }
}
