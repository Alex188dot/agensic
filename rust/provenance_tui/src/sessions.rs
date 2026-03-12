use clap::Parser;
use chrono::TimeZone;
use crossterm::event::{self, Event, KeyCode, KeyEvent, KeyEventKind};
use crossterm::execute;
use crossterm::terminal::{
    disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen,
};
use ratatui::backend::CrosstermBackend;
use ratatui::layout::{Constraint, Direction, Layout, Rect};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, Borders, Cell, Paragraph, Row, Table, TableState, Wrap};
use ratatui::Terminal;
use reqwest::blocking::Client;
use serde::Deserialize;
use serde_json::Value;
use std::cmp::{max, min};
use std::io::{self, Write};
use std::time::{Duration, Instant};

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
            event_index: 0,
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
                    text.push_str(chunk);
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

    fn jump_first_command(&mut self) {
        if let Some((idx, _)) = self
            .events
            .iter()
            .enumerate()
            .find(|(_, event)| event.event_type == "command.recorded")
        {
            self.event_index = idx;
            self.focus = FocusPane::Timeline;
        }
    }

    fn jump_first_change(&mut self) {
        if let Some((idx, _)) = self
            .events
            .iter()
            .enumerate()
            .find(|(_, event)| event.event_type.starts_with("git."))
        {
            self.event_index = idx;
            self.focus = FocusPane::Timeline;
        }
    }

    fn jump_failure(&mut self) {
        if let Some((idx, _)) = self
            .events
            .iter()
            .enumerate()
            .rev()
            .find(|(_, event)| event.event_type == "process.exited" || event.event_type == "marker.session.finished")
        {
            self.event_index = idx;
            self.focus = FocusPane::Timeline;
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
        self.status = format!("Opened session {}", session_id);
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
    execute!(stdout, EnterAlternateScreen).map_err(|e| format!("enter alt screen failed: {}", e))?;
    let backend = CrosstermBackend::new(stdout);
    let mut terminal = Terminal::new(backend).map_err(|e| format!("terminal init failed: {}", e))?;
    let mut app = App::new(client, args)?;

    loop {
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
                _ => {}
            }
        }
    }

    disable_raw_mode().map_err(|e| format!("disable raw mode failed: {}", e))?;
    execute!(terminal.backend_mut(), LeaveAlternateScreen)
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
        match key.code {
            KeyCode::Esc => {
                if app.deep_link {
                    return Ok(true);
                }
                app.detail = None;
                app.status = "Back to sessions".to_string();
            }
            KeyCode::Char('t') => detail.focus = FocusPane::Replay,
            KeyCode::Char('e') => detail.focus = FocusPane::Timeline,
            KeyCode::Char('d') => detail.focus = FocusPane::Changes,
            KeyCode::Char(' ') => {
                detail.autoplay = !detail.autoplay;
                detail.last_tick = Instant::now();
            }
            KeyCode::Char('1') => detail.jump_first_command(),
            KeyCode::Char('2') => detail.jump_first_change(),
            KeyCode::Char('3') => detail.jump_failure(),
            KeyCode::Up | KeyCode::Char('k') => {
                if detail.event_index > 0 {
                    detail.event_index -= 1;
                }
            }
            KeyCode::Down | KeyCode::Char('j') => {
                if detail.event_index + 1 < detail.events.len() {
                    detail.event_index += 1;
                }
            }
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
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Min(5), Constraint::Length(2)])
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
            Constraint::Length(18),
            Constraint::Length(30),
            Constraint::Length(16),
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
        Paragraph::new("↑↓ move  Enter open  r refresh  Esc quit")
            .style(Style::default().fg(Color::White)),
        chunks[1],
    );
}

fn draw_detail(frame: &mut ratatui::Frame<'_>, app: &App, detail: &DetailState) {
    let area = frame.area();
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([Constraint::Length(4), Constraint::Percentage(45), Constraint::Min(10), Constraint::Length(2)])
        .split(area);
    let top = Paragraph::new(vec![
        Line::from(format!(
            "{}  {}  repo={}  duration={}  outcome={}",
            if detail.session.agent_name.trim().is_empty() {
                detail.session.agent.as_str()
            } else {
                detail.session.agent_name.as_str()
            },
            detail.session.model,
            if detail.session.repo_root.trim().is_empty() {
                "-"
            } else {
                detail.session.repo_root.as_str()
            },
            format_duration(detail.session.started_at, detail.session.ended_at),
            format_outcome(detail.session.exit_code, &detail.session.status, &detail.session.violation_code)
        )),
        Line::from(format!(
            "session={}  branch {} -> {}  head {} -> {}",
            detail.session.session_id,
            fallback_text(&detail.session.branch_start),
            fallback_text(&detail.session.branch_end),
            truncate(&detail.session.head_start, 12),
            truncate(&detail.session.head_end, 12),
        )),
    ])
    .block(Block::default().borders(Borders::ALL).title("Header"))
    .wrap(Wrap { trim: true });
    frame.render_widget(top, chunks[0]);

    let upper = Layout::default()
        .direction(Direction::Horizontal)
        .constraints([Constraint::Percentage(52), Constraint::Percentage(48)])
        .split(chunks[1]);
    frame.render_widget(build_timeline(detail), upper[0]);
    frame.render_widget(build_changes(detail), upper[1]);

    frame.render_widget(build_replay(detail), chunks[2]);
    frame.render_widget(
        Paragraph::new(
            "↑↓ timeline  ←→ switch pane or seek replay  j/k move  h/l seek  space play/pause  1/2/3 jump  t/e/d focus  Esc back",
        )
        .style(Style::default().fg(Color::White)),
        chunks[3],
    );

    if app.deep_link && detail.session.session_id.is_empty() {
        frame.render_widget(
            Paragraph::new("Session unavailable").block(Block::default().borders(Borders::ALL)),
            centered_rect(50, 20, area),
        );
    }
}

fn build_timeline(detail: &DetailState) -> Paragraph<'static> {
    let total = detail.events.len();
    let selected = min(detail.event_index, total.saturating_sub(1));
    let start = selected.saturating_sub(8);
    let end = min(total, start + 18);
    let mut lines: Vec<Line<'static>> = Vec::new();
    for (idx, event) in detail.events.iter().enumerate().take(end).skip(start) {
        let marker = if idx == selected { ">>" } else { "  " };
        let style = if idx == selected && detail.focus == FocusPane::Timeline {
            Style::default().fg(Color::Black).bg(Color::LightGreen)
        } else {
            Style::default()
        };
        lines.push(Line::from(Span::styled(
            format!(
                "{} {:>4} {:<20} {}",
                marker,
                event.seq,
                truncate(&event.event_type, 20),
                event_summary(event),
            ),
            style,
        )));
    }
    Paragraph::new(lines)
        .block(Block::default().borders(Borders::ALL).title("Timeline"))
        .wrap(Wrap { trim: true })
}

fn build_changes(detail: &DetailState) -> Paragraph<'static> {
    let focused = detail.focus == FocusPane::Changes;
    let title_style = if focused {
        Style::default().fg(Color::LightGreen).add_modifier(Modifier::BOLD)
    } else {
        Style::default()
    };
    let mut lines = vec![
        Line::from(Span::styled("Files changed", title_style)),
    ];
    if let Some(files) = detail.session.changes.get("files_changed").and_then(Value::as_array) {
        for value in files.iter().take(12) {
            lines.push(Line::from(format!("- {}", value.as_str().unwrap_or_default())));
        }
    }
    lines.push(Line::from(""));
    lines.push(Line::from("Committed diff stat:"));
    lines.push(Line::from(
        detail
            .session
            .changes
            .get("committed_diff_stat")
            .and_then(Value::as_str)
            .unwrap_or("-")
            .to_string(),
    ));
    lines.push(Line::from(""));
    lines.push(Line::from("Worktree diff stat:"));
    lines.push(Line::from(
        detail
            .session
            .changes
            .get("worktree_diff_stat")
            .and_then(Value::as_str)
            .unwrap_or("-")
            .to_string(),
    ));
    lines.push(Line::from(""));
    lines.push(Line::from("Commits created:"));
    if let Some(commits) = detail.session.changes.get("commits_created").and_then(Value::as_array) {
        for commit in commits.iter().take(6) {
            let sha = commit.get("sha").and_then(Value::as_str).unwrap_or("-");
            let summary = commit.get("summary").and_then(Value::as_str).unwrap_or("-");
            lines.push(Line::from(format!("{} {}", sha, summary)));
        }
    }
    Paragraph::new(lines)
        .block(Block::default().borders(Borders::ALL).title("Changes"))
        .wrap(Wrap { trim: true })
}

fn build_replay(detail: &DetailState) -> Paragraph<'static> {
    let focused = detail.focus == FocusPane::Replay;
    let title = if focused {
        Line::from(vec![
            Span::styled("Replay ", Style::default().fg(Color::LightGreen).add_modifier(Modifier::BOLD)),
            Span::raw(if detail.autoplay { "(playing)" } else { "(paused)" }),
        ])
    } else {
        Line::from("Replay")
    };
    let display = if detail.replay_text.is_empty() {
        "(no terminal stdout recorded)".to_string()
    } else {
        detail.replay_text.clone()
    };
    Paragraph::new(display)
        .block(Block::default().borders(Borders::ALL).title(title))
        .wrap(Wrap { trim: false })
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
            .map(|value| truncate(value, 48))
            .unwrap_or_else(|| "-".to_string()),
        "violation.noted" => event
            .payload
            .get("code")
            .and_then(Value::as_str)
            .unwrap_or("-")
            .to_string(),
        "git.commit.created" => format!(
            "{} {}",
            event.payload.get("sha").and_then(Value::as_str).unwrap_or("-"),
            event.payload.get("summary").and_then(Value::as_str).unwrap_or("-"),
        ),
        "terminal.stdin" | "terminal.stdout" => event
            .payload
            .get("data")
            .and_then(Value::as_str)
            .map(|value| truncate(value.replace('\n', "\\n").replace('\r', "\\r").as_str(), 48))
            .unwrap_or_else(|| "-".to_string()),
        _ => event
            .payload
            .as_object()
            .map(|obj| truncate(&format!("{:?}", obj), 48))
            .unwrap_or_else(|| "-".to_string()),
    }
}
