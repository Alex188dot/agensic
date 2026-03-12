use clap::Parser;
use chrono::TimeZone;
use crossterm::event::{
    self, DisableMouseCapture, EnableMouseCapture, Event, KeyCode, KeyEvent, KeyEventKind,
    MouseEvent, MouseEventKind,
};
use crossterm::execute;
use crossterm::terminal::{
    disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen,
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

struct DetailState {
    session: SessionSummary,
    events: Vec<SessionEvent>,
    focus: FocusPane,
    event_index: usize,
    replay_indices: Vec<usize>,
    replay_step: usize,
    replay_text: String,
    autoplay: bool,
    last_tick: Instant,
}

impl DetailState {
    fn new(session: SessionSummary, events: Vec<SessionEvent>, autoplay: bool) -> Self {
        let initial_event_index = events
            .iter()
            .position(|event| event.event_type == "command.recorded")
            .unwrap_or(0);
        let replay_indices: Vec<usize> = events
            .iter()
            .enumerate()
            .filter_map(|(idx, event)| {
                if event.event_type == "terminal.stdout" {
                    Some(idx)
                } else {
                    None
                }
            })
            .collect();
        let mut out = Self {
            session,
            events,
            focus: if autoplay { FocusPane::Replay } else { FocusPane::Timeline },
            event_index: initial_event_index,
            replay_indices,
            replay_step: 0,
            replay_text: String::new(),
            autoplay,
            last_tick: Instant::now(),
        };
        if out.autoplay {
            out.rebuild_replay_text(0);
        } else if !out.replay_indices.is_empty() {
            let last = out.replay_indices.len().saturating_sub(1);
            out.rebuild_replay_text(last);
        }
        out
    }

    fn rebuild_replay_text(&mut self, step: usize) {
        self.replay_step = min(step, self.replay_indices.len().saturating_sub(1));
        let mut text = String::new();
        if self.replay_indices.is_empty() {
            self.replay_text.clear();
            return;
        }
        for idx in self.replay_indices.iter().take(self.replay_step + 1) {
            if let Some(event) = self.events.get(*idx) {
                if let Some(chunk) = event.payload.get("data").and_then(Value::as_str) {
                    text.push_str(&sanitize_terminal_output(chunk));
                }
            }
        }
        self.replay_text = text;
    }

    fn seek_relative(&mut self, delta: isize) {
        if self.replay_indices.is_empty() {
            return;
        }
        let current = self.replay_step as isize;
        let next = min(
            self.replay_indices.len().saturating_sub(1) as isize,
            max(0, current + delta),
        ) as usize;
        self.rebuild_replay_text(next);
    }

    fn advance_autoplay(&mut self) {
        if !self.autoplay || self.replay_indices.is_empty() {
            return;
        }
        if self.last_tick.elapsed() < Duration::from_millis(60) {
            return;
        }
        self.last_tick = Instant::now();
        if self.replay_step + 1 < self.replay_indices.len() {
            self.rebuild_replay_text(self.replay_step + 1);
        } else {
            self.autoplay = false;
        }
    }

    fn move_selection(&mut self, delta: isize) {
        if self.events.is_empty() {
            self.event_index = 0;
            return;
        }
        let next = (self.event_index as isize + delta)
            .clamp(0, self.events.len().saturating_sub(1) as isize) as usize;
        self.event_index = next;
    }

    fn page_selection(&mut self, delta: isize) {
        self.move_selection(delta.saturating_mul(TIMELINE_PAGE_STEP as isize));
    }

    fn selected_event(&self) -> Option<&SessionEvent> {
        self.events.get(self.event_index)
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
            .request(&format!("/sessions?limit={}", max(1, min(500, self.args.limit))))
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
            return Err(format!("session detail request failed: {}", session_response.status()));
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
            return Err(format!("session events request failed: {}", events_response.status()));
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
    let mut terminal = Terminal::new(backend).map_err(|e| format!("terminal init failed: {}", e))?;
    let mut app = App::new(client, args)?;

    loop {
        if app.needs_terminal_clear {
            terminal.clear().map_err(|e| format!("terminal clear failed: {}", e))?;
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
    execute!(terminal.backend_mut(), DisableMouseCapture, LeaveAlternateScreen)
        .map_err(|e| format!("leave alt screen failed: {}", e))?;
    terminal.show_cursor().map_err(|e| format!("show cursor failed: {}", e))?;
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
            KeyCode::Char('t') => detail.focus = FocusPane::Replay,
            KeyCode::Char('e') => detail.focus = FocusPane::Timeline,
            KeyCode::Char('d') => detail.focus = FocusPane::Changes,
            KeyCode::Char(' ') => {
                if detail.autoplay {
                    detail.autoplay = false;
                } else if !detail.replay_indices.is_empty() {
                    if detail.replay_step + 1 >= detail.replay_indices.len() {
                        detail.rebuild_replay_text(0);
                    }
                    detail.autoplay = true;
                    detail.focus = FocusPane::Replay;
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
            KeyCode::BackTab if detail.focus == FocusPane::Timeline => detail.page_selection(-1),
            KeyCode::Tab if detail.focus == FocusPane::Timeline => detail.page_selection(1),
            KeyCode::Left | KeyCode::Char('h') => {
                if detail.focus == FocusPane::Replay {
                    detail.seek_relative(-1);
                } else {
                    detail.focus = match detail.focus {
                        FocusPane::Timeline => FocusPane::Replay,
                        FocusPane::Changes => FocusPane::Timeline,
                        FocusPane::Replay => FocusPane::Changes,
                    };
                }
            }
            KeyCode::Right | KeyCode::Char('l') => {
                if detail.focus == FocusPane::Replay {
                    detail.seek_relative(1);
                } else {
                    detail.focus = match detail.focus {
                        FocusPane::Timeline => FocusPane::Changes,
                        FocusPane::Changes => FocusPane::Replay,
                        FocusPane::Replay => FocusPane::Timeline,
                    };
                }
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
        .constraints([Constraint::Min(5), Constraint::Length(2), Constraint::Length(1)])
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
                Cell::from(format_outcome(session.exit_code, &session.status, &session.violation_code)),
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
        Row::new(vec!["session", "started", "agent", "model", "repo", "branch", "duration", "outcome"]).style(
            Style::default().fg(Color::Cyan).add_modifier(Modifier::BOLD),
        ),
    )
    .block(Block::default().borders(Borders::ALL).title("Agensic Sessions"))
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
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(5),
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
        .constraints([Constraint::Percentage(40), Constraint::Percentage(60)])
        .split(body[1]);
    frame.render_widget(build_timeline(detail, body[0].height), body[0]);
    frame.render_widget(build_changes(detail), right[0]);
    frame.render_widget(build_replay(detail), right[1]);
    frame.render_widget(
        Paragraph::new(
            "↑↓ timeline  mouse wheel scrolls timeline  Tab/Shift+Tab jump 500  ←→ switch pane or seek replay  Enter details  space play/pause  t/e/d focus  Esc back",
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
    let lines = vec![
        Line::from(vec![
            Span::styled(actor.to_string(), Style::default().fg(Color::LightGreen).add_modifier(Modifier::BOLD)),
            Span::raw("  "),
            Span::raw(sanitize_inline_text(&detail.session.model)),
            Span::raw("  "),
            Span::styled(
                format_outcome(
                    detail.session.exit_code,
                    &detail.session.status,
                    &detail.session.violation_code,
                ),
                Style::default().fg(Color::Yellow),
            ),
        ]),
        Line::from(format!(
            "session {}    duration {}    started {}",
            detail.session.session_id,
            format_duration(detail.session.started_at, detail.session.ended_at),
            format_ts(detail.session.started_at),
        )),
        Line::from(format!(
            "repo {}",
            fallback_text(&sanitize_inline_text(&detail.session.repo_root))
        )),
        Line::from(format!(
            "branch {} -> {}    head {} -> {}",
            fallback_text(&sanitize_inline_text(&detail.session.branch_start)),
            fallback_text(&sanitize_inline_text(&detail.session.branch_end)),
            truncate(&sanitize_inline_text(&detail.session.head_start), 12),
            truncate(&sanitize_inline_text(&detail.session.head_end), 12),
        )),
    ];
    Paragraph::new(lines)
        .block(pane_block("Header", false))
        .wrap(Wrap { trim: true })
}

fn build_timeline(detail: &DetailState, height: u16) -> Paragraph<'static> {
    let total = detail.events.len();
    let selected = min(detail.event_index, total.saturating_sub(1));
    let visible_rows = height.saturating_sub(2).max(1) as usize;
    let start = selected.saturating_sub(visible_rows / 2);
    let end = min(total, start + visible_rows);
    let mut lines: Vec<Line<'static>> = Vec::new();
    for (idx, event) in detail.events.iter().enumerate().take(end).skip(start) {
        let marker = if idx == selected { ">>" } else { "  " };
        let style = if idx == selected && detail.focus == FocusPane::Timeline {
            Style::default().fg(Color::Black).bg(Color::LightGreen)
        } else if idx == selected {
            Style::default().fg(Color::Yellow).add_modifier(Modifier::BOLD)
        } else {
            Style::default()
        };
        let line = format!(
            "{} {:>4}  {:<18}  {}",
            marker,
            event.seq,
            truncate(&sanitize_inline_text(&event.event_type), 18),
            event_summary(event),
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
    lines.push(Line::from(""));
    lines.push(Line::from(Span::styled(
        "Files changed",
        Style::default().fg(Color::Cyan).add_modifier(Modifier::BOLD),
    )));
    if let Some(files) = detail.session.changes.get("files_changed").and_then(Value::as_array) {
        for value in files.iter().take(12) {
            lines.push(Line::from(format!(
                "- {}",
                sanitize_inline_text(value.as_str().unwrap_or_default())
            )));
        }
    } else {
        lines.push(Line::from("-"));
    }
    lines.push(Line::from(""));
    lines.push(Line::from(Span::styled(
        "Committed diff stat",
        Style::default().fg(Color::Cyan).add_modifier(Modifier::BOLD),
    )));
    lines.push(Line::from(
        sanitize_multiline_text(
            detail
                .session
                .changes
                .get("committed_diff_stat")
                .and_then(Value::as_str)
                .unwrap_or("-"),
        ),
    ));
    lines.push(Line::from(""));
    lines.push(Line::from(Span::styled(
        "Worktree diff stat",
        Style::default().fg(Color::Cyan).add_modifier(Modifier::BOLD),
    )));
    lines.push(Line::from(
        sanitize_multiline_text(
            detail
                .session
                .changes
                .get("worktree_diff_stat")
                .and_then(Value::as_str)
                .unwrap_or("-"),
        ),
    ));
    lines.push(Line::from(""));
    lines.push(Line::from(Span::styled(
        "Commits created",
        Style::default().fg(Color::Cyan).add_modifier(Modifier::BOLD),
    )));
    if let Some(commits) = detail.session.changes.get("commits_created").and_then(Value::as_array) {
        for commit in commits.iter().take(6) {
            let sha = commit.get("sha").and_then(Value::as_str).unwrap_or("-");
            let summary = commit.get("summary").and_then(Value::as_str).unwrap_or("-");
            lines.push(Line::from(format!(
                "{} {}",
                sanitize_inline_text(sha),
                sanitize_inline_text(summary)
            )));
        }
    } else {
        lines.push(Line::from("-"));
    }
    Paragraph::new(lines)
        .block(pane_block("Changes", detail.focus == FocusPane::Changes))
        .wrap(Wrap { trim: true })
}

fn build_replay(detail: &DetailState) -> Paragraph<'static> {
    let focused = detail.focus == FocusPane::Replay;
    let title = if detail.autoplay {
        "Replay (playing)"
    } else {
        "Replay (paused)"
    };
    let display = if detail.replay_text.is_empty() {
        "(no terminal stdout recorded)".to_string()
    } else {
        tail_lines(&detail.replay_text, 160)
    };
    Paragraph::new(display)
        .block(pane_block(title, focused))
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
            Block::default().borders(Borders::ALL).title(Line::from(vec![
                Span::styled("Event details ", Style::default().fg(Color::LightGreen).add_modifier(Modifier::BOLD)),
                Span::styled("(Enter/Esc close, ↑↓ scroll)", Style::default().fg(Color::DarkGray)),
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
        if status.trim().is_empty() { "-" } else { status },
        exit_code.map(|value| value.to_string()).unwrap_or_else(|| "-".to_string())
    );
    if !violation_code.trim().is_empty() {
        out.push_str(&format!(" violation={}", violation_code));
    }
    out
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
    chars.into_iter().take(max_chars.saturating_sub(1)).collect::<String>() + "…"
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
            sanitize_inline_text(event.payload.get("sha").and_then(Value::as_str).unwrap_or("-")),
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
    if let Some(cwd) = event.payload.get("working_directory").and_then(Value::as_str) {
        lines.push(Line::from(format!(
            "cwd: {}",
            sanitize_multiline_text(cwd)
        )));
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
            Style::default().fg(Color::Cyan).add_modifier(Modifier::BOLD),
        )));
        push_text_block(&mut lines, &sanitize_multiline_text(command));
    }

    if let Some(data) = event.payload.get("data").and_then(Value::as_str) {
        lines.push(Line::from(""));
        lines.push(Line::from(Span::styled(
            "data",
            Style::default().fg(Color::Cyan).add_modifier(Modifier::BOLD),
        )));
        push_text_block(&mut lines, &sanitize_multiline_text(data));
    }

    let payload_text = serde_json::to_string_pretty(&event.payload)
        .unwrap_or_else(|_| "{}".to_string());
    lines.push(Line::from(""));
    lines.push(Line::from(Span::styled(
        "payload",
        Style::default().fg(Color::Cyan).add_modifier(Modifier::BOLD),
    )));
    push_text_block(&mut lines, &sanitize_multiline_text(&payload_text));
    lines
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

fn tail_lines(value: &str, max_lines: usize) -> String {
    let lines: Vec<&str> = value.lines().collect();
    if lines.len() <= max_lines {
        return value.to_string();
    }
    lines[lines.len() - max_lines..].join("\n")
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

#[cfg(test)]
mod tests {
    use super::{sanitize_inline_text, sanitize_terminal_output, tail_lines};

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
    fn tail_lines_keeps_recent_lines() {
        let input = "1\n2\n3\n4";
        assert_eq!(tail_lines(input, 2), "3\n4");
    }
}
