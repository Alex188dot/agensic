use clap::Parser;
use crossterm::event::{self, Event, KeyCode, KeyEvent, KeyEventKind, KeyModifiers};
use crossterm::execute;
use crossterm::terminal::{
    disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen,
};
use ratatui::backend::CrosstermBackend;
use ratatui::layout::{Alignment, Constraint, Direction, Layout, Rect};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, Borders, Cell, Clear, Paragraph, Row, Table, TableState, Wrap};
use ratatui::Terminal;
use reqwest::blocking::Client;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};
use std::collections::BTreeSet;
use std::fs::{self, File};
use std::io::{self, Stdout, Write};
use std::path::Path;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

#[derive(Debug, Parser, Clone)]
#[command(name = "ghostshell-provenance-tui")]
#[command(about = "Full-screen provenance viewer for GhostShell")]
struct Args {
    #[arg(long, default_value = "http://127.0.0.1:22000")]
    daemon_url: String,

    #[arg(long, default_value = "")]
    auth_token: String,

    #[arg(long, default_value_t = 50)]
    limit: usize,

    #[arg(long, default_value = "")]
    label: String,

    #[arg(long, default_value = "")]
    contains: String,

    #[arg(long, default_value_t = 0)]
    since_ts: i64,

    #[arg(long, default_value = "")]
    tier: String,

    #[arg(long, default_value = "")]
    agent: String,

    #[arg(long, default_value = "")]
    agent_name: String,

    #[arg(long, default_value = "")]
    provider: String,

    #[arg(long, default_value = "")]
    export: String,

    #[arg(long, default_value = "")]
    out: String,
}

#[derive(Clone, Debug, Default, Deserialize, Serialize)]
struct RunEntry {
    run_id: String,
    ts: i64,
    command: String,
    label: String,
    confidence: f64,
    agent: String,
    agent_name: String,
    provider: String,
    model: String,
    raw_model: String,
    normalized_model: String,
    model_fingerprint: String,
    evidence_tier: String,
    agent_source: String,
    registry_version: String,
    registry_status: String,
    source: String,
    working_directory: String,
    exit_code: Option<i64>,
    duration_ms: Option<i64>,
    shell_pid: Option<i64>,
    #[serde(default)]
    evidence: Vec<String>,
    #[serde(default)]
    payload: Value,
}

#[derive(Debug, Deserialize)]
struct RunsResponse {
    runs: Vec<RunEntry>,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum SortMode {
    TimeDesc,
    ActorAsc,
    DurationDesc,
    ExitAsc,
}

impl SortMode {
    fn next(self) -> Self {
        match self {
            Self::TimeDesc => Self::ActorAsc,
            Self::ActorAsc => Self::DurationDesc,
            Self::DurationDesc => Self::ExitAsc,
            Self::ExitAsc => Self::TimeDesc,
        }
    }

    fn label(self) -> &'static str {
        match self {
            Self::TimeDesc => "time",
            Self::ActorAsc => "actor",
            Self::DurationDesc => "duration",
            Self::ExitAsc => "exit",
        }
    }
}

#[derive(Clone, Debug, Default)]
struct Filters {
    label: String,
    tier: String,
    agent: String,
    provider: String,
    exit: String,
}

struct App {
    client: Client,
    args: Args,
    auth_token: String,
    base_rows: Vec<RunEntry>,
    semantic_rows: Option<Vec<RunEntry>>,
    view_rows: Vec<RunEntry>,
    search: String,
    input_mode: bool,
    details_open: bool,
    filter_menu: bool,
    filter_cursor: usize,
    selected: usize,
    status: String,
    last_edit: Instant,
    semantic_dirty: bool,
    sort_mode: SortMode,
    filters: Filters,
}

impl App {
    fn new(client: Client, args: Args) -> Self {
        let filters = Filters {
            label: args.label.clone(),
            tier: args.tier.clone(),
            agent: args.agent.clone(),
            provider: args.provider.clone(),
            exit: String::new(),
        };
        Self {
            client,
            auth_token: args.auth_token.clone(),
            args,
            base_rows: Vec::new(),
            semantic_rows: None,
            view_rows: Vec::new(),
            search: String::new(),
            input_mode: false,
            details_open: false,
            filter_menu: false,
            filter_cursor: 0,
            selected: 0,
            status: "Ready".to_string(),
            last_edit: Instant::now(),
            semantic_dirty: false,
            sort_mode: SortMode::TimeDesc,
            filters,
        }
    }

    fn actor_of(row: &RunEntry) -> String {
        if !row.agent_name.trim().is_empty() {
            return row.agent_name.clone();
        }
        if !row.agent.trim().is_empty() {
            return row.agent.clone();
        }
        if row.label.starts_with("HUMAN") {
            return "user".to_string();
        }
        "-".to_string()
    }

    fn format_duration(ms: Option<i64>) -> String {
        match ms {
            Some(v) if v >= 1000 => format!("{:.2}s", (v as f64) / 1000.0),
            Some(v) if v >= 0 => format!("{}ms", v),
            _ => "-".to_string(),
        }
    }

    fn format_exit(code: Option<i64>) -> String {
        match code {
            Some(v) => v.to_string(),
            None => "-".to_string(),
        }
    }

    fn search_hit(row: &RunEntry, query: &str) -> bool {
        if query.is_empty() {
            return true;
        }
        let q = query.to_lowercase();
        if row.command.to_lowercase().contains(&q) {
            return true;
        }
        if Self::actor_of(row).to_lowercase().contains(&q) {
            return true;
        }
        row.label.to_lowercase().contains(&q)
    }

    fn row_passes_filters(&self, row: &RunEntry) -> bool {
        if !self.filters.label.is_empty() && row.label != self.filters.label {
            return false;
        }
        if !self.filters.tier.is_empty() && row.evidence_tier != self.filters.tier {
            return false;
        }
        if !self.filters.agent.is_empty() && row.agent != self.filters.agent {
            return false;
        }
        if !self.filters.provider.is_empty() && row.provider != self.filters.provider {
            return false;
        }
        match self.filters.exit.as_str() {
            "0" => row.exit_code == Some(0),
            "nonzero" => row.exit_code.map(|v| v != 0).unwrap_or(false),
            _ => true,
        }
    }

    fn apply_view(&mut self) {
        let source: Vec<RunEntry> = if self.search.trim().is_empty() {
            self.base_rows.clone()
        } else if let Some(rows) = &self.semantic_rows {
            rows.clone()
        } else {
            self.base_rows.clone()
        };

        let query = self.search.trim().to_string();
        let mut out: Vec<RunEntry> = source
            .into_iter()
            .filter(|row| Self::search_hit(row, &query))
            .filter(|row| self.row_passes_filters(row))
            .collect();

        out.sort_by(|left, right| match self.sort_mode {
            SortMode::TimeDesc => right
                .ts
                .cmp(&left.ts)
                .then_with(|| right.run_id.cmp(&left.run_id)),
            SortMode::ActorAsc => Self::actor_of(left)
                .to_lowercase()
                .cmp(&Self::actor_of(right).to_lowercase())
                .then_with(|| right.ts.cmp(&left.ts)),
            SortMode::DurationDesc => right
                .duration_ms
                .unwrap_or(-1)
                .cmp(&left.duration_ms.unwrap_or(-1))
                .then_with(|| right.ts.cmp(&left.ts)),
            SortMode::ExitAsc => left
                .exit_code
                .unwrap_or(9_999)
                .cmp(&right.exit_code.unwrap_or(9_999))
                .then_with(|| right.ts.cmp(&left.ts)),
        });

        self.view_rows = out;
        if self.view_rows.is_empty() {
            self.selected = 0;
        } else if self.selected >= self.view_rows.len() {
            self.selected = self.view_rows.len() - 1;
        }
    }

    fn set_search(&mut self, value: String) {
        self.search = value;
        self.last_edit = Instant::now();
        self.semantic_dirty = !self.search.trim().is_empty();
        if self.search.trim().is_empty() {
            self.semantic_rows = None;
        }
        self.apply_view();
    }

    fn fetch_runs_page(
        &self,
        before_ts: i64,
        before_run_id: &str,
    ) -> Result<Vec<RunEntry>, String> {
        let mut params: Vec<(String, String)> = vec![
            ("limit".to_string(), self.args.limit.to_string()),
            ("since_ts".to_string(), self.args.since_ts.to_string()),
        ];
        if before_ts > 0 {
            params.push(("before_ts".to_string(), before_ts.to_string()));
        }
        if !before_run_id.trim().is_empty() {
            params.push(("before_run_id".to_string(), before_run_id.to_string()));
        }
        if !self.filters.label.is_empty() {
            params.push(("label".to_string(), self.filters.label.clone()));
        }
        if !self.filters.tier.is_empty() {
            params.push(("tier".to_string(), self.filters.tier.clone()));
        }
        if !self.filters.agent.is_empty() {
            params.push(("agent".to_string(), self.filters.agent.clone()));
        }
        if !self.args.agent_name.trim().is_empty() {
            params.push(("agent_name".to_string(), self.args.agent_name.clone()));
        }
        if !self.filters.provider.is_empty() {
            params.push(("provider".to_string(), self.filters.provider.clone()));
        }

        let url = format!(
            "{}/provenance/runs",
            self.args.daemon_url.trim_end_matches('/')
        );
        let mut req = self.client.get(url).query(&params);
        if !self.auth_token.trim().is_empty() {
            req = req.header("Authorization", format!("Bearer {}", self.auth_token));
        }
        let response = req.send().map_err(|e| format!("request failed: {}", e))?;
        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().unwrap_or_default();
            return Err(format!("runs endpoint {}: {}", status, body));
        }
        let payload: RunsResponse = response
            .json()
            .map_err(|e| format!("invalid /provenance/runs response: {}", e))?;
        Ok(payload.runs)
    }

    fn fetch_semantic(&self, query: &str) -> Result<Vec<RunEntry>, String> {
        let mut params: Vec<(String, String)> = vec![
            ("query".to_string(), query.to_string()),
            ("limit".to_string(), self.args.limit.to_string()),
            ("since_ts".to_string(), self.args.since_ts.to_string()),
        ];
        if !self.filters.label.is_empty() {
            params.push(("label".to_string(), self.filters.label.clone()));
        }
        if !self.filters.tier.is_empty() {
            params.push(("tier".to_string(), self.filters.tier.clone()));
        }
        if !self.filters.agent.is_empty() {
            params.push(("agent".to_string(), self.filters.agent.clone()));
        }
        if !self.args.agent_name.trim().is_empty() {
            params.push(("agent_name".to_string(), self.args.agent_name.clone()));
        }
        if !self.filters.provider.is_empty() {
            params.push(("provider".to_string(), self.filters.provider.clone()));
        }

        let url = format!(
            "{}/provenance/runs/semantic",
            self.args.daemon_url.trim_end_matches('/')
        );
        let mut req = self.client.get(url).query(&params);
        if !self.auth_token.trim().is_empty() {
            req = req.header("Authorization", format!("Bearer {}", self.auth_token));
        }
        let response = req
            .send()
            .map_err(|e| format!("semantic request failed: {}", e))?;
        if !response.status().is_success() {
            let status = response.status();
            let body = response.text().unwrap_or_default();
            return Err(format!("semantic endpoint {}: {}", status, body));
        }
        let payload: RunsResponse = response
            .json()
            .map_err(|e| format!("invalid /provenance/runs/semantic response: {}", e))?;
        Ok(payload.runs)
    }

    fn refresh_base(&mut self) {
        match self.fetch_runs_page(0, "") {
            Ok(rows) => {
                self.base_rows = rows;
                if self.search.trim().is_empty() {
                    self.semantic_rows = None;
                }
                self.status = format!("Loaded {} rows", self.base_rows.len());
            }
            Err(err) => {
                self.status = format!("Load failed: {}", err);
            }
        }
        self.apply_view();
    }

    fn load_older(&mut self) {
        let Some(last) = self.base_rows.last() else {
            return;
        };
        match self.fetch_runs_page(last.ts, &last.run_id) {
            Ok(rows) => {
                if rows.is_empty() {
                    self.status = "No older rows".to_string();
                    return;
                }
                let mut seen: BTreeSet<String> =
                    self.base_rows.iter().map(|r| r.run_id.clone()).collect();
                let mut added = 0usize;
                for row in rows {
                    if seen.contains(&row.run_id) {
                        continue;
                    }
                    seen.insert(row.run_id.clone());
                    self.base_rows.push(row);
                    added += 1;
                }
                self.status = format!("Loaded {} older rows", added);
            }
            Err(err) => {
                self.status = format!("Load older failed: {}", err);
            }
        }
        self.apply_view();
    }

    fn tick(&mut self) {
        if self.search.trim().is_empty() || !self.semantic_dirty {
            return;
        }
        if self.last_edit.elapsed() < Duration::from_millis(180) {
            return;
        }
        let query = self.search.trim().to_string();
        match self.fetch_semantic(&query) {
            Ok(rows) => {
                self.semantic_rows = Some(rows);
                self.status = format!("Semantic results for '{}'", query);
            }
            Err(err) => {
                self.status = format!("Semantic fallback to local filter: {}", err);
            }
        }
        self.semantic_dirty = false;
        self.apply_view();
    }

    fn export_current(&mut self, format: &str, out_path: &str) {
        match export_rows(&self.view_rows, format, out_path) {
            Ok(()) => {
                self.status = format!("Exported {} rows to {}", self.view_rows.len(), out_path)
            }
            Err(err) => self.status = format!("Export failed: {}", err),
        }
    }

    fn select_down(&mut self) {
        if self.view_rows.is_empty() {
            return;
        }
        self.selected = (self.selected + 1).min(self.view_rows.len() - 1);
    }

    fn select_up(&mut self) {
        if self.view_rows.is_empty() {
            return;
        }
        if self.selected > 0 {
            self.selected -= 1;
        }
    }

    fn select_by(&mut self, delta: isize) {
        if self.view_rows.is_empty() {
            self.selected = 0;
            return;
        }
        let current = self.selected as isize;
        let next = (current + delta).clamp(0, (self.view_rows.len() as isize) - 1);
        self.selected = next as usize;
    }

    fn filter_fields() -> [&'static str; 5] {
        ["label", "tier", "agent", "provider", "exit"]
    }

    fn field_values(&self, field: &str) -> Vec<String> {
        match field {
            "label" => unique_values(self.base_rows.iter().map(|r| r.label.clone())),
            "tier" => unique_values(self.base_rows.iter().map(|r| r.evidence_tier.clone())),
            "agent" => unique_values(self.base_rows.iter().map(|r| r.agent.clone())),
            "provider" => unique_values(self.base_rows.iter().map(|r| r.provider.clone())),
            "exit" => vec!["".to_string(), "0".to_string(), "nonzero".to_string()],
            _ => vec!["".to_string()],
        }
    }

    fn field_current(&self, field: &str) -> String {
        match field {
            "label" => self.filters.label.clone(),
            "tier" => self.filters.tier.clone(),
            "agent" => self.filters.agent.clone(),
            "provider" => self.filters.provider.clone(),
            "exit" => self.filters.exit.clone(),
            _ => String::new(),
        }
    }

    fn set_field_current(&mut self, field: &str, value: String) {
        match field {
            "label" => self.filters.label = value,
            "tier" => self.filters.tier = value,
            "agent" => self.filters.agent = value,
            "provider" => self.filters.provider = value,
            "exit" => self.filters.exit = value,
            _ => {}
        }
        self.semantic_dirty = !self.search.trim().is_empty();
        self.refresh_base();
        self.apply_view();
    }

    fn cycle_filter_value(&mut self, step: isize) {
        let field = Self::filter_fields()[self.filter_cursor];
        let values = self.field_values(field);
        if values.is_empty() {
            return;
        }
        let current = self.field_current(field);
        let pos = values.iter().position(|v| v == &current).unwrap_or(0) as isize;
        let len = values.len() as isize;
        let next = (pos + step).rem_euclid(len) as usize;
        self.set_field_current(field, values[next].clone());
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

fn now_epoch_seconds() -> i64 {
    let Ok(duration) = SystemTime::now().duration_since(UNIX_EPOCH) else {
        return 0;
    };
    duration.as_secs() as i64
}

fn truncate_cell(value: &str, max: usize) -> String {
    let chars: Vec<char> = value.chars().collect();
    if chars.len() <= max {
        return value.to_string();
    }
    if max <= 1 {
        return "…".to_string();
    }
    let mut out: String = chars.into_iter().take(max - 1).collect();
    out.push('…');
    out
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

fn draw_ui(terminal: &mut Terminal<CrosstermBackend<Stdout>>, app: &App) -> io::Result<()> {
    terminal.draw(|frame| {
        let area = frame.area();
        let chunks = Layout::default()
            .direction(Direction::Vertical)
            .constraints([
                Constraint::Length(3),
                Constraint::Min(8),
                Constraint::Length(2),
            ])
            .split(area);

        let mode = if app.search.trim().is_empty() {
            "LOCAL"
        } else if app.semantic_rows.is_some() {
            "SEMANTIC"
        } else {
            "LOCAL->SEMANTIC"
        };
        let top = Paragraph::new(Line::from(vec![
            Span::styled(
                "Search ",
                Style::default()
                    .fg(Color::Cyan)
                    .add_modifier(Modifier::BOLD),
            ),
            Span::raw(if app.search.is_empty() {
                "(type /)"
            } else {
                app.search.as_str()
            }),
            Span::raw("    "),
            Span::styled(format!("mode={} ", mode), Style::default().fg(Color::Yellow)),
            Span::raw(format!(
                "filters[label={}, tier={}, agent={}, provider={}, exit={}] rows={}",
                if app.filters.label.is_empty() {
                    "*"
                } else {
                    app.filters.label.as_str()
                },
                if app.filters.tier.is_empty() {
                    "*"
                } else {
                    app.filters.tier.as_str()
                },
                if app.filters.agent.is_empty() {
                    "*"
                } else {
                    app.filters.agent.as_str()
                },
                if app.filters.provider.is_empty() {
                    "*"
                } else {
                    app.filters.provider.as_str()
                },
                if app.filters.exit.is_empty() {
                    "*"
                } else {
                    app.filters.exit.as_str()
                },
                app.view_rows.len(),
            )),
        ]))
        .block(Block::default().borders(Borders::ALL).title("GhostShell Provenance"));
        frame.render_widget(top, chunks[0]);

        let compact = area.width < 120;
        let header_style = Style::default()
            .fg(Color::White)
            .add_modifier(Modifier::BOLD);
        let rows: Vec<Row> = app
            .view_rows
            .iter()
            .enumerate()
            .map(|(idx, row)| {
                let base_style = if idx % 2 == 0 {
                    Style::default().fg(Color::Gray)
                } else {
                    Style::default().fg(Color::White)
                };
                let mut cells = vec![
                    Cell::from(row.ts.to_string()),
                    Cell::from(truncate_cell(&App::actor_of(row), 18)),
                    Cell::from(if compact {
                        truncate_cell(&row.command, 40)
                    } else {
                        truncate_cell(&row.command, 80)
                    }),
                    Cell::from(App::format_exit(row.exit_code)),
                    Cell::from(App::format_duration(row.duration_ms)),
                ];
                if !compact {
                    cells.push(Cell::from(truncate_cell(&row.label, 16)));
                    cells.push(Cell::from(truncate_cell(&row.provider, 12)));
                }
                Row::new(cells).style(base_style)
            })
            .collect();

        let constraints = if compact {
            vec![
                Constraint::Length(12),
                Constraint::Length(16),
                Constraint::Min(30),
                Constraint::Length(6),
                Constraint::Length(10),
            ]
        } else {
            vec![
                Constraint::Length(12),
                Constraint::Length(18),
                Constraint::Min(48),
                Constraint::Length(6),
                Constraint::Length(10),
                Constraint::Length(16),
                Constraint::Length(12),
            ]
        };

        let header_cells = if compact {
            vec!["time", "actor", "command", "exit", "duration"]
        } else {
            vec!["time", "actor", "command", "exit", "duration", "label", "provider"]
        };

        let table = Table::new(rows, constraints)
            .header(Row::new(header_cells).style(header_style))
            .block(Block::default().borders(Borders::ALL).title("Runs"))
            .row_highlight_style(
                Style::default()
                    .fg(Color::Black)
                    .bg(Color::LightGreen)
                    .add_modifier(Modifier::BOLD),
            )
            .highlight_symbol(">> ");

        let mut table_state = TableState::default();
        if !app.view_rows.is_empty() {
            table_state.select(Some(app.selected));
        }
        frame.render_stateful_widget(table, chunks[1], &mut table_state);

        let status = Paragraph::new(format!(
            "{}  |  ↑↓ PgUp/PgDn Home/End  / search  f filters  s sort={}  Enter details  r refresh  e export(json)  E export(csv)  q quit",
            app.status,
            app.sort_mode.label(),
        ))
        .alignment(Alignment::Left)
        .style(Style::default().fg(Color::Yellow));
        frame.render_widget(status, chunks[2]);

        if app.filter_menu {
            let popup = centered_rect(58, 46, area);
            let fields = App::filter_fields();
            let mut lines: Vec<Line> = Vec::new();
            lines.push(Line::from(
                "Filter panel (h/l change, j/k move, Enter/Esc close)",
            ));
            lines.push(Line::from(""));
            for (idx, field) in fields.iter().enumerate() {
                let value = app.field_current(field);
                let shown = if value.is_empty() { "*" } else { value.as_str() };
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

        if app.details_open {
            let popup = centered_rect(80, 65, area);
            if let Some(row) = app.view_rows.get(app.selected) {
                let payload_summary = match &row.payload {
                    Value::Object(_) | Value::Array(_) => {
                        let text = serde_json::to_string_pretty(&row.payload)
                            .unwrap_or_else(|_| "{}".to_string());
                        truncate_cell(&text, 1200)
                    }
                    other => other.to_string(),
                };
                let details = vec![
                    Line::from(format!("run_id: {}", row.run_id)),
                    Line::from(format!("ts: {}", row.ts)),
                    Line::from(format!("actor: {}", App::actor_of(row))),
                    Line::from(format!("label: {}", row.label)),
                    Line::from(format!("tier: {}", row.evidence_tier)),
                    Line::from(format!("provider: {}", row.provider)),
                    Line::from(format!("model: {}", row.model)),
                    Line::from(format!("exit: {}", App::format_exit(row.exit_code))),
                    Line::from(format!("duration: {}", App::format_duration(row.duration_ms))),
                    Line::from(format!("cwd: {}", row.working_directory)),
                    Line::from(""),
                    Line::from("command:"),
                    Line::from(row.command.clone()),
                    Line::from(""),
                    Line::from("payload:"),
                    Line::from(payload_summary),
                ];
                let panel = Paragraph::new(details)
                    .block(
                        Block::default()
                            .borders(Borders::ALL)
                            .title("Run details (Enter/Esc close)"),
                    )
                    .wrap(Wrap { trim: true });
                frame.render_widget(Clear, popup);
                frame.render_widget(panel, popup);
            }
        }
    })?;
    Ok(())
}

fn handle_key(app: &mut App, key: KeyEvent) -> bool {
    if key.kind != KeyEventKind::Press {
        return false;
    }

    if app.filter_menu {
        match key.code {
            KeyCode::Esc | KeyCode::Enter => {
                app.filter_menu = false;
            }
            KeyCode::Char('j') | KeyCode::Down => {
                let max = App::filter_fields().len().saturating_sub(1);
                app.filter_cursor = (app.filter_cursor + 1).min(max);
            }
            KeyCode::Char('k') | KeyCode::Up => {
                if app.filter_cursor > 0 {
                    app.filter_cursor -= 1;
                }
            }
            KeyCode::Char('h') | KeyCode::Left => app.cycle_filter_value(-1),
            KeyCode::Char('l') | KeyCode::Right => app.cycle_filter_value(1),
            _ => {}
        }
        return false;
    }

    if app.details_open {
        match key.code {
            KeyCode::Esc | KeyCode::Enter => app.details_open = false,
            _ => {}
        }
        return false;
    }

    if app.input_mode {
        match key.code {
            KeyCode::Esc | KeyCode::Enter => app.input_mode = false,
            KeyCode::Backspace => {
                let mut search = app.search.clone();
                search.pop();
                app.set_search(search);
            }
            KeyCode::Char(c) => {
                if !key.modifiers.contains(KeyModifiers::CONTROL) {
                    let mut search = app.search.clone();
                    search.push(c);
                    app.set_search(search);
                }
            }
            _ => {}
        }
        return false;
    }

    match key.code {
        KeyCode::Char('q') => return true,
        KeyCode::Char('/') => app.input_mode = true,
        KeyCode::Up => app.select_up(),
        KeyCode::Down => app.select_down(),
        KeyCode::PageUp => app.select_by(-10),
        KeyCode::PageDown => {
            let prev = app.selected;
            app.select_by(10);
            if app.selected == prev && !app.base_rows.is_empty() {
                app.load_older();
            }
        }
        KeyCode::Home => app.selected = 0,
        KeyCode::End => {
            if !app.view_rows.is_empty() {
                app.selected = app.view_rows.len() - 1;
            }
        }
        KeyCode::Enter => app.details_open = true,
        KeyCode::Char('s') => {
            app.sort_mode = app.sort_mode.next();
            app.apply_view();
        }
        KeyCode::Char('r') => app.refresh_base(),
        KeyCode::Char('f') => app.filter_menu = true,
        KeyCode::Char('e') => {
            let out = format!("provenance_export_{}.json", now_epoch_seconds());
            app.export_current("json", &out);
        }
        KeyCode::Char('E') => {
            let out = format!("provenance_export_{}.csv", now_epoch_seconds());
            app.export_current("csv", &out);
        }
        _ => {}
    }
    false
}

fn export_rows(rows: &[RunEntry], export_format: &str, out_path: &str) -> Result<(), String> {
    let format = export_format.trim().to_lowercase();
    if format != "json" && format != "csv" {
        return Err("unsupported export format".to_string());
    }
    if out_path.trim().is_empty() {
        return Err("missing output path".to_string());
    }

    let output = Path::new(out_path);
    if let Some(parent) = output.parent() {
        if !parent.as_os_str().is_empty() {
            fs::create_dir_all(parent).map_err(|e| format!("create parent failed: {}", e))?;
        }
    }

    if format == "json" {
        let payload = json!({"runs": rows, "total": rows.len()});
        let mut file = File::create(output).map_err(|e| format!("create output failed: {}", e))?;
        serde_json::to_writer_pretty(&mut file, &payload)
            .map_err(|e| format!("write json failed: {}", e))?;
        file.write_all(b"\n")
            .map_err(|e| format!("write json newline failed: {}", e))?;
        return Ok(());
    }

    let mut writer =
        csv::Writer::from_path(output).map_err(|e| format!("create csv failed: {}", e))?;
    writer
        .write_record([
            "run_id",
            "ts",
            "command",
            "label",
            "confidence",
            "actor",
            "agent",
            "agent_name",
            "provider",
            "model",
            "evidence_tier",
            "source",
            "working_directory",
            "exit_code",
            "duration_ms",
            "shell_pid",
        ])
        .map_err(|e| format!("write csv header failed: {}", e))?;

    for row in rows {
        writer
            .write_record([
                row.run_id.clone(),
                row.ts.to_string(),
                row.command.clone(),
                row.label.clone(),
                format!("{:.2}", row.confidence),
                App::actor_of(row),
                row.agent.clone(),
                row.agent_name.clone(),
                row.provider.clone(),
                row.model.clone(),
                row.evidence_tier.clone(),
                row.source.clone(),
                row.working_directory.clone(),
                row.exit_code.map(|v| v.to_string()).unwrap_or_default(),
                row.duration_ms.map(|v| v.to_string()).unwrap_or_default(),
                row.shell_pid.map(|v| v.to_string()).unwrap_or_default(),
            ])
            .map_err(|e| format!("write csv row failed: {}", e))?;
    }
    writer
        .flush()
        .map_err(|e| format!("flush csv failed: {}", e))?;
    Ok(())
}

fn run_export_mode(client: &Client, args: &Args) -> Result<(), String> {
    let mut app = App::new(client.clone(), args.clone());
    app.search = args.contains.clone();
    app.refresh_base();
    let rows = if !args.contains.trim().is_empty() {
        app.fetch_semantic(args.contains.trim())?
    } else {
        app.base_rows.clone()
    };
    export_rows(&rows, &args.export, &args.out)?;
    println!("exported {} rows to {}", rows.len(), args.out);
    Ok(())
}

fn run_interactive(client: &Client, args: &Args) -> Result<(), String> {
    let mut app = App::new(client.clone(), args.clone());
    app.refresh_base();
    if !args.contains.trim().is_empty() {
        app.set_search(args.contains.clone());
    }

    enable_raw_mode().map_err(|e| format!("enable raw mode failed: {}", e))?;
    let mut stdout = io::stdout();
    execute!(stdout, EnterAlternateScreen)
        .map_err(|e| format!("enter alt screen failed: {}", e))?;
    let backend = CrosstermBackend::new(stdout);
    let mut terminal =
        Terminal::new(backend).map_err(|e| format!("terminal init failed: {}", e))?;

    let mut exit_requested = false;
    while !exit_requested {
        draw_ui(&mut terminal, &app).map_err(|e| format!("draw failed: {}", e))?;

        if event::poll(Duration::from_millis(50)).map_err(|e| format!("poll failed: {}", e))? {
            let ev = event::read().map_err(|e| format!("read event failed: {}", e))?;
            if let Event::Key(key) = ev {
                exit_requested = handle_key(&mut app, key);
            }
        }

        app.tick();
    }

    disable_raw_mode().map_err(|e| format!("disable raw mode failed: {}", e))?;
    execute!(terminal.backend_mut(), LeaveAlternateScreen)
        .map_err(|e| format!("leave alt screen failed: {}", e))?;
    terminal
        .show_cursor()
        .map_err(|e| format!("show cursor failed: {}", e))?;
    Ok(())
}

fn cleanup_terminal() {
    let _ = disable_raw_mode();
    let mut stdout = io::stdout();
    let _ = execute!(stdout, LeaveAlternateScreen);
}

fn main() {
    let args = Args::parse();

    let client = match Client::builder().timeout(Duration::from_secs(8)).build() {
        Ok(c) => c,
        Err(err) => {
            eprintln!("failed to build HTTP client: {}", err);
            std::process::exit(1);
        }
    };

    let result = if !args.export.trim().is_empty() {
        run_export_mode(&client, &args)
    } else {
        run_interactive(&client, &args)
    };

    if let Err(err) = result {
        cleanup_terminal();
        eprintln!("{}", err);
        std::process::exit(1);
    }
}
