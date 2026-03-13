mod sessions;

use chrono::{Duration as ChronoDuration, Local, NaiveDate, TimeZone};
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
use std::env;
use std::fs::{self, File};
use std::io::{self, Stdout, Write};
use std::panic;
use std::path::Path;
use std::process::{Command, Stdio};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

pub(crate) const COPY_FLASH_DURATION: Duration = Duration::from_secs(3);
const COPY_ICON: &str = "⧉";

#[derive(Clone)]
pub(crate) struct FlashStatus {
    message: String,
    expires_at: Instant,
}

impl FlashStatus {
    pub(crate) fn new(message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
            expires_at: Instant::now() + COPY_FLASH_DURATION,
        }
    }

    pub(crate) fn active_message(&self) -> Option<&str> {
        (Instant::now() <= self.expires_at).then_some(self.message.as_str())
    }
}

pub(crate) fn agensic_title_style() -> Style {
    Style::default()
        .fg(Color::Rgb(240, 120, 220))
        .add_modifier(Modifier::BOLD)
}

pub(crate) fn copy_to_clipboard(text: &str) -> Result<(), String> {
    let text = text.trim_end_matches('\n');
    if text.is_empty() {
        return Err("Nothing to copy".to_string());
    }
    #[cfg(target_os = "macos")]
    let program = ("pbcopy", Vec::<&str>::new());
    #[cfg(target_os = "linux")]
    let program = if Command::new("wl-copy")
        .arg("--version")
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .is_ok()
    {
        ("wl-copy", Vec::<&str>::new())
    } else if Command::new("xclip")
        .arg("-version")
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .status()
        .is_ok()
    {
        ("xclip", vec!["-selection", "clipboard"])
    } else {
        return Err("No clipboard utility found".to_string());
    };
    #[cfg(target_os = "windows")]
    let program = ("clip", Vec::<&str>::new());

    let mut child = Command::new(program.0)
        .args(program.1)
        .stdin(Stdio::piped())
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .map_err(|err| format!("Clipboard copy failed: {}", err))?;
    let Some(mut stdin) = child.stdin.take() else {
        return Err("Clipboard pipe unavailable".to_string());
    };
    stdin
        .write_all(text.as_bytes())
        .map_err(|err| format!("Clipboard write failed: {}", err))?;
    drop(stdin);
    let status = child
        .wait()
        .map_err(|err| format!("Clipboard copy failed: {}", err))?;
    if status.success() {
        Ok(())
    } else {
        Err("Clipboard copy command failed".to_string())
    }
}

#[derive(Debug, Parser, Clone)]
#[command(name = "agensic-provenance-tui")]
#[command(about = "Full-screen provenance viewer for Agensic")]
struct Args {
    #[arg(long, default_value = "http://127.0.0.1:22000")]
    daemon_url: String,

    #[arg(long, default_value = "")]
    auth_token: String,

    #[arg(long, default_value_t = 500)]
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
    #[serde(default)]
    runs: Vec<RunEntry>,
    #[serde(default)]
    total: usize,
    #[serde(default)]
    total_matching: usize,
}

struct RunsPage {
    rows: Vec<RunEntry>,
    total_matching: usize,
}

fn collect_all_runs_pages<F>(page_limit: usize, mut fetch_page: F) -> Result<RunsPage, String>
where
    F: FnMut(i64, &str) -> Result<RunsPage, String>,
{
    let limit = page_limit.max(1);
    let mut rows: Vec<RunEntry> = Vec::new();
    let mut seen: BTreeSet<String> = BTreeSet::new();
    let mut total_matching = 0usize;
    let mut before_ts = 0i64;
    let mut before_run_id = String::new();

    loop {
        let page = fetch_page(before_ts, &before_run_id)?;
        total_matching = total_matching.max(page.total_matching);
        let page_len = page.rows.len();
        if page_len == 0 {
            break;
        }

        let next_cursor = page.rows.last().map(|row| (row.ts, row.run_id.clone()));

        for row in page.rows {
            if seen.insert(row.run_id.clone()) {
                rows.push(row);
            }
        }

        if total_matching > 0 && rows.len() >= total_matching {
            break;
        }
        if page_len < limit {
            break;
        }

        let Some((next_ts, next_run_id)) = next_cursor else {
            break;
        };
        if next_ts == before_ts && next_run_id == before_run_id {
            break;
        }
        before_ts = next_ts;
        before_run_id = next_run_id;
    }

    if total_matching == 0 {
        total_matching = rows.len();
    }

    Ok(RunsPage {
        rows,
        total_matching,
    })
}

const SECONDS_PER_DAY: i64 = 24 * 60 * 60;
const PROVENANCE_MAX_LOOKBACK_DAYS: i64 = 365;
const CUSTOM_TIME_RANGE_MAX_DAYS: i64 = 30;
const MAX_COMMAND_DURATION_MS: i64 = 86_400_000;

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

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum TimeFilterMode {
    Last7d,
    Last30d,
    Last365d,
    Custom,
}

impl TimeFilterMode {
    fn as_str(self) -> &'static str {
        match self {
            Self::Last7d => "last_7d",
            Self::Last30d => "last_30d",
            Self::Last365d => "last_365d",
            Self::Custom => "custom",
        }
    }

    fn from_str(value: &str) -> Option<Self> {
        match value {
            "last_7d" => Some(Self::Last7d),
            "last_30d" => Some(Self::Last30d),
            "last_365d" => Some(Self::Last365d),
            "custom" => Some(Self::Custom),
            _ => None,
        }
    }
}

impl Default for TimeFilterMode {
    fn default() -> Self {
        Self::Last365d
    }
}

#[derive(Clone, Debug)]
struct Filters {
    label: String,
    tier: String,
    agent: String,
    model: String,
    exit: String,
    time_mode: TimeFilterMode,
    custom_start: String,
    custom_end: String,
    custom_since_ts: Option<i64>,
    custom_before_ts: Option<i64>,
}

impl Default for Filters {
    fn default() -> Self {
        Self {
            label: String::new(),
            tier: String::new(),
            agent: String::new(),
            model: String::new(),
            exit: String::new(),
            time_mode: TimeFilterMode::Last365d,
            custom_start: String::new(),
            custom_end: String::new(),
            custom_since_ts: None,
            custom_before_ts: None,
        }
    }
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
    details_scroll: u16,
    filter_menu: bool,
    filter_cursor: usize,
    selected: usize,
    page: usize,
    has_more_rows: bool,
    total_matching: usize,
    status: String,
    flash_status: Option<FlashStatus>,
    last_edit: Instant,
    semantic_dirty: bool,
    sort_mode: SortMode,
    filters: Filters,
    time_popup_open: bool,
    time_popup_start_input: String,
    time_popup_end_input: String,
    time_popup_focus: usize,
    time_popup_error: String,
    time_popup_start_replace_on_type: bool,
    time_popup_end_replace_on_type: bool,
}

impl App {
    fn new(client: Client, args: Args) -> Self {
        let mut filters = Filters::default();
        filters.label = args.label.clone();
        filters.tier = args.tier.clone();
        filters.agent = args.agent.clone();
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
            details_scroll: 0,
            filter_menu: false,
            filter_cursor: 0,
            selected: 0,
            page: 0,
            has_more_rows: true,
            total_matching: 0,
            status: "Ready".to_string(),
            flash_status: None,
            last_edit: Instant::now(),
            semantic_dirty: false,
            sort_mode: SortMode::TimeDesc,
            filters,
            time_popup_open: false,
            time_popup_start_input: String::new(),
            time_popup_end_input: String::new(),
            time_popup_focus: 0,
            time_popup_error: String::new(),
            time_popup_start_replace_on_type: false,
            time_popup_end_replace_on_type: false,
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
            Some(v) if v >= MAX_COMMAND_DURATION_MS => ">24h".to_string(),
            Some(v) if v >= 1000 => format!("{:.2}s", (v as f64) / 1000.0),
            Some(v) if v >= 0 => format!("{}ms", v),
            _ => "-".to_string(),
        }
    }

    fn format_time(ts: i64) -> String {
        Local
            .timestamp_opt(ts, 0)
            .single()
            .map(|d| d.format("%m/%d/%y %H:%M:%S").to_string())
            .unwrap_or_else(|| ts.to_string())
    }

    fn format_exit(code: Option<i64>) -> String {
        match code {
            Some(v) => v.to_string(),
            None => "-".to_string(),
        }
    }

    fn label_style(label: &str) -> Style {
        match label {
            "HUMAN_TYPED" => Style::default().fg(Color::Green),
            "AI_EXECUTED" => Style::default().fg(Color::Rgb(0, 191, 255)),
            "INVALID_PROOF" => Style::default().fg(Color::Red),
            "AG_SUGGESTED_HUMAN_RAN" => Style::default().fg(Color::Cyan),
            "AI_SUGGESTED_HUMAN_RAN" => Style::default().fg(Color::Rgb(125, 249, 255)),
            "UNKNOWN" => Style::default().fg(Color::Rgb(211, 211, 211)),
            _ => Style::default().fg(Color::Rgb(211, 211, 211)),
        }
    }

    fn command_style() -> Style {
        Style::default().fg(Color::Rgb(255, 165, 0))
    }

    fn key_hint_style() -> Style {
        Style::default().fg(Color::Yellow)
    }

    fn copy_icon_style() -> Style {
        Style::default()
            .fg(Color::LightGreen)
            .add_modifier(Modifier::BOLD)
    }

    fn header_style() -> Style {
        Style::default()
            .fg(Color::Cyan)
            .add_modifier(Modifier::BOLD)
    }

    fn status_text(&self) -> &str {
        self.flash_status
            .as_ref()
            .and_then(FlashStatus::active_message)
            .unwrap_or(self.status.as_str())
    }

    fn set_flash(&mut self, message: impl Into<String>) {
        self.flash_status = Some(FlashStatus::new(message));
    }

    fn search_hit(row: &RunEntry, query: &str) -> bool {
        if query.is_empty() {
            return true;
        }
        let q = query.to_lowercase();
        row.command.to_lowercase().contains(&q)
    }

    fn effective_since_ts(&self, now_ts: i64) -> i64 {
        self.args.since_ts.max(time_filter_since_ts(
            self.filters.time_mode,
            self.filters.custom_since_ts,
            now_ts,
        ))
    }

    fn effective_before_ts(&self) -> Option<i64> {
        if self.filters.time_mode == TimeFilterMode::Custom {
            self.filters.custom_before_ts
        } else {
            None
        }
    }

    fn row_passes_filters(&self, row: &RunEntry, now_ts: i64) -> bool {
        let since_ts = self.effective_since_ts(now_ts);
        if !ts_within_time_bounds(row.ts, since_ts, self.effective_before_ts()) {
            return false;
        }
        if !self.filters.label.is_empty() && row.label != self.filters.label {
            return false;
        }
        if !self.filters.tier.is_empty() && row.evidence_tier != self.filters.tier {
            return false;
        }
        if !self.filters.agent.is_empty() && row.agent != self.filters.agent {
            return false;
        }
        if !self.args.agent_name.trim().is_empty() && row.agent_name != self.args.agent_name {
            return false;
        }
        if !self.args.provider.trim().is_empty() && row.provider != self.args.provider {
            return false;
        }
        if !self.filters.model.is_empty() && row.model != self.filters.model {
            return false;
        }
        match self.filters.exit.as_str() {
            "0" => row.exit_code == Some(0),
            "nonzero" => row.exit_code.map(|v| v != 0).unwrap_or(false),
            _ => true,
        }
    }

    fn build_view_rows(&self, source: &[RunEntry]) -> Vec<RunEntry> {
        let query = self.search.trim().to_string();
        let now_ts = now_epoch_seconds();
        let mut out: Vec<RunEntry> = source
            .iter()
            .cloned()
            .filter(|row| Self::search_hit(row, &query))
            .filter(|row| self.row_passes_filters(row, now_ts))
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

        out
    }

    fn apply_view(&mut self) {
        let out = self.build_view_rows(&self.base_rows);

        self.view_rows = out;
        let page_count = self.page_count_loaded();
        if page_count == 0 {
            self.page = 0;
            self.selected = 0;
            return;
        }
        if self.page >= page_count {
            self.page = page_count - 1;
        }
        let page_len = self.current_page_len();
        if page_len == 0 {
            self.selected = 0;
        } else if self.selected >= page_len {
            self.selected = page_len - 1;
        }
    }

    fn set_search(&mut self, value: String) {
        self.search = value;
        self.last_edit = Instant::now();
        self.semantic_dirty = true;
        self.page = 0;
        self.selected = 0;
        if self.search.trim().is_empty() {
            self.semantic_rows = None;
        }
        self.apply_view();
    }

    fn fetch_runs_page(&self, before_ts: i64, before_run_id: &str) -> Result<RunsPage, String> {
        let now_ts = now_epoch_seconds();
        let effective_since_ts = self.effective_since_ts(now_ts);
        let effective_before_ts = if before_ts > 0 { Some(before_ts) } else { None };
        let effective_before_run_id = before_run_id.trim().to_string();

        let mut params: Vec<(String, String)> = vec![
            ("limit".to_string(), self.args.limit.to_string()),
            ("since_ts".to_string(), effective_since_ts.to_string()),
        ];
        if !self.search.trim().is_empty() {
            params.push((
                "command_contains".to_string(),
                self.search.trim().to_string(),
            ));
        }
        if let Some(value) = effective_before_ts {
            params.push(("before_ts".to_string(), value.to_string()));
        }
        if !effective_before_run_id.is_empty() {
            params.push(("before_run_id".to_string(), effective_before_run_id));
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
        if !self.args.provider.trim().is_empty() {
            params.push(("provider".to_string(), self.args.provider.clone()));
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
        let total_matching = if payload.total_matching > 0 {
            payload.total_matching
        } else if payload.total > 0 {
            payload.total
        } else {
            payload.runs.len()
        };
        Ok(RunsPage {
            rows: payload.runs,
            total_matching,
        })
    }

    fn fetch_all_runs(&self) -> Result<RunsPage, String> {
        collect_all_runs_pages(self.args.limit, |before_ts, before_run_id| {
            self.fetch_runs_page(before_ts, before_run_id)
        })
    }

    fn refresh_base(&mut self) {
        match self.fetch_runs_page(0, "") {
            Ok(page) => {
                let page_rows_len = page.rows.len();
                self.base_rows = page.rows;
                self.total_matching = page.total_matching;
                self.has_more_rows =
                    page_rows_len >= self.args.limit || self.base_rows.len() < self.total_matching;
                self.page = 0;
                self.selected = 0;
                if self.search.trim().is_empty() {
                    self.semantic_rows = None;
                }
                self.status = format!(
                    "Loaded {} rows (showing {} total matches)",
                    self.base_rows.len(),
                    self.total_matching
                );
            }
            Err(err) => {
                self.status = format!("Load failed: {}", err);
                self.total_matching = 0;
                self.has_more_rows = false;
            }
        }
        self.apply_view();
        self.hydrate_custom_range_if_empty();
    }

    fn hydrate_custom_range_if_empty(&mut self) {
        if self.filters.time_mode != TimeFilterMode::Custom {
            return;
        }
        if !self.view_rows.is_empty() {
            return;
        }
        if self.base_rows.is_empty() {
            return;
        }

        let since_ts = self.effective_since_ts(now_epoch_seconds());
        let mut attempts = 0usize;
        while self.view_rows.is_empty() && attempts < 12 {
            let Some(last) = self.base_rows.last() else {
                break;
            };
            if last.ts < since_ts {
                break;
            }
            let before_len = self.base_rows.len();
            self.load_older();
            if self.base_rows.len() == before_len {
                break;
            }
            attempts += 1;
        }
        if self.view_rows.is_empty()
            && !self.filters.custom_start.is_empty()
            && !self.filters.custom_end.is_empty()
        {
            self.status = format!(
                "No rows in custom range {}..{}",
                self.filters.custom_start, self.filters.custom_end
            );
        }
    }

    fn load_older(&mut self) {
        let Some(last) = self.base_rows.last() else {
            return;
        };
        match self.fetch_runs_page(last.ts, &last.run_id) {
            Ok(page) => {
                let page_rows_len = page.rows.len();
                self.total_matching = page.total_matching;
                if page.rows.is_empty() {
                    self.has_more_rows = false;
                    self.status = "No older rows".to_string();
                    return;
                }
                let mut seen: BTreeSet<String> =
                    self.base_rows.iter().map(|r| r.run_id.clone()).collect();
                let mut added = 0usize;
                for row in page.rows {
                    if seen.contains(&row.run_id) {
                        continue;
                    }
                    seen.insert(row.run_id.clone());
                    self.base_rows.push(row);
                    added += 1;
                }
                self.has_more_rows =
                    page_rows_len >= self.args.limit || self.base_rows.len() < self.total_matching;
                self.status = format!("Loaded {} older rows", added);
            }
            Err(err) => {
                self.status = format!("Load older failed: {}", err);
            }
        }
        self.apply_view();
    }

    fn tick(&mut self) {
        if !self.semantic_dirty {
            return;
        }
        if self.last_edit.elapsed() < Duration::from_millis(180) {
            return;
        }
        self.refresh_base();
        self.semantic_dirty = false;
    }

    fn export_dataset_rows(&self) -> Result<Vec<RunEntry>, String> {
        let all_rows = self.fetch_all_runs()?;
        Ok(self.build_view_rows(&all_rows.rows))
    }

    fn export_current(&mut self, format: &str, out_path: &str) {
        match self.export_dataset_rows().and_then(|rows| {
            let count = rows.len();
            export_rows(&rows, format, out_path)?;
            Ok(count)
        }) {
            Ok(count) => self.status = format!("Exported {} matching rows to {}", count, out_path),
            Err(err) => self.status = format!("Export failed: {}", err),
        }
    }

    fn select_down(&mut self) {
        if self.at_last_row() {
            return;
        }
        let page_len = self.current_page_len();
        if page_len == 0 {
            return;
        }
        if self.selected + 1 < page_len {
            self.selected += 1;
            return;
        }
        let page_before = self.page;
        self.next_page();
        if self.page == page_before {
            self.status = "Already on last row".to_string();
        }
    }

    fn select_up(&mut self) {
        if self.at_first_row() {
            return;
        }
        let page_len = self.current_page_len();
        if page_len == 0 {
            return;
        }
        if self.selected > 0 {
            self.selected -= 1;
            return;
        }
        if self.page == 0 {
            self.status = "Already on first row".to_string();
            return;
        }
        self.previous_page();
        let prev_page_len = self.current_page_len();
        if prev_page_len > 0 {
            self.selected = prev_page_len - 1;
        }
    }

    fn page_size(&self) -> usize {
        self.args.limit.max(1)
    }

    fn page_count_loaded(&self) -> usize {
        if self.view_rows.is_empty() {
            0
        } else {
            self.view_rows.len().div_ceil(self.page_size())
        }
    }

    fn page_count_total(&self) -> usize {
        if self.total_matching == 0 {
            self.page_count_loaded().max(1)
        } else {
            self.total_matching.div_ceil(self.page_size()).max(1)
        }
    }

    fn current_page_bounds(&self) -> (usize, usize) {
        let start = self.page * self.page_size();
        let end = (start + self.page_size()).min(self.view_rows.len());
        (start, end)
    }

    fn current_page_len(&self) -> usize {
        let (start, end) = self.current_page_bounds();
        end.saturating_sub(start)
    }

    fn next_page(&mut self) {
        let loaded_pages = self.page_count_loaded();
        if loaded_pages > 0 && self.page + 1 < loaded_pages {
            self.page += 1;
            self.selected = 0;
            return;
        }
        if self.has_more_rows {
            let before_len = self.base_rows.len();
            self.load_older();
            if self.base_rows.len() > before_len {
                let refreshed_pages = self.page_count_loaded();
                if self.page + 1 < refreshed_pages {
                    self.page += 1;
                    self.selected = 0;
                }
            }
            return;
        }
        self.status = "Already on last page".to_string();
    }

    fn previous_page(&mut self) {
        if self.page == 0 {
            self.status = "Already on first page".to_string();
            return;
        }
        self.page -= 1;
        self.selected = 0;
    }

    fn selected_global_index(&self) -> Option<usize> {
        let (start, end) = self.current_page_bounds();
        if start >= end {
            return None;
        }
        Some((start + self.selected).min(end - 1))
    }

    fn at_first_row(&self) -> bool {
        matches!(self.selected_global_index(), Some(0))
    }

    fn at_last_loaded_row(&self) -> bool {
        matches!(
            self.selected_global_index(),
            Some(idx) if idx + 1 >= self.view_rows.len() && !self.view_rows.is_empty()
        )
    }

    fn at_last_row(&self) -> bool {
        self.at_last_loaded_row() && !self.has_more_rows
    }

    fn filter_fields() -> [&'static str; 7] {
        [
            "label",
            "tier",
            "agent",
            "model",
            "exit",
            "time",
            "[Reset All]",
        ]
    }

    fn field_values(&self, field: &str) -> Vec<String> {
        match field {
            "label" => unique_values(self.base_rows.iter().map(|r| r.label.clone())),
            "tier" => unique_values(self.base_rows.iter().map(|r| r.evidence_tier.clone())),
            "agent" => unique_values(self.base_rows.iter().map(|r| r.agent.clone())),
            "model" => unique_values(self.base_rows.iter().map(|r| r.model.clone())),
            "exit" => vec!["".to_string(), "0".to_string(), "nonzero".to_string()],
            "time" => vec![
                TimeFilterMode::Last7d.as_str().to_string(),
                TimeFilterMode::Last30d.as_str().to_string(),
                TimeFilterMode::Last365d.as_str().to_string(),
                TimeFilterMode::Custom.as_str().to_string(),
            ],
            "[Reset All]" => vec!["<Press Left/Right/Enter to Reset>".to_string()],
            _ => vec!["".to_string()],
        }
    }

    fn time_filter_display_value(&self) -> String {
        match self.filters.time_mode {
            TimeFilterMode::Last7d => TimeFilterMode::Last7d.as_str().to_string(),
            TimeFilterMode::Last30d => TimeFilterMode::Last30d.as_str().to_string(),
            TimeFilterMode::Last365d => TimeFilterMode::Last365d.as_str().to_string(),
            TimeFilterMode::Custom => {
                if self.filters.custom_start.is_empty() || self.filters.custom_end.is_empty() {
                    TimeFilterMode::Custom.as_str().to_string()
                } else {
                    format!(
                        "{}({}..{})",
                        TimeFilterMode::Custom.as_str(),
                        self.filters.custom_start,
                        self.filters.custom_end
                    )
                }
            }
        }
    }

    fn field_current(&self, field: &str) -> String {
        match field {
            "label" => self.filters.label.clone(),
            "tier" => self.filters.tier.clone(),
            "agent" => self.filters.agent.clone(),
            "model" => self.filters.model.clone(),
            "exit" => self.filters.exit.clone(),
            "time" => self.time_filter_display_value(),
            "[Reset All]" => "<Press Left/Right/Enter to Reset>".to_string(),
            _ => String::new(),
        }
    }

    fn apply_time_preset(&mut self, mode: TimeFilterMode) {
        self.filters.time_mode = mode;
        self.semantic_dirty = !self.search.trim().is_empty();
        self.refresh_base();
        self.apply_view();
    }

    fn open_custom_time_popup(&mut self) {
        self.time_popup_open = true;
        self.filter_menu = false;
        self.time_popup_focus = 0;
        self.time_popup_error.clear();
        self.time_popup_start_replace_on_type = true;
        self.time_popup_end_replace_on_type = true;
        if !self.filters.custom_start.is_empty() && !self.filters.custom_end.is_empty() {
            self.time_popup_start_input = self.filters.custom_start.clone();
            self.time_popup_end_input = self.filters.custom_end.clone();
            return;
        }
        let today = Local::now().date_naive();
        let start = today - ChronoDuration::days(6);
        self.time_popup_start_input = start.format("%Y-%m-%d").to_string();
        self.time_popup_end_input = today.format("%Y-%m-%d").to_string();
    }

    fn submit_custom_time_popup(&mut self) {
        let today = Local::now().date_naive();
        match validate_custom_time_range(
            &self.time_popup_start_input,
            &self.time_popup_end_input,
            today,
        ) {
            Ok((start_date, end_date)) => {
                let since_ts = match local_date_midnight_ts(start_date) {
                    Ok(value) => value,
                    Err(err) => {
                        self.time_popup_error = err;
                        return;
                    }
                };
                let next_day = match end_date.checked_add_signed(ChronoDuration::days(1)) {
                    Some(value) => value,
                    None => {
                        self.time_popup_error =
                            "Could not compute custom range end date".to_string();
                        return;
                    }
                };
                let before_ts = match local_date_midnight_ts(next_day) {
                    Ok(value) => value,
                    Err(err) => {
                        self.time_popup_error = err;
                        return;
                    }
                };
                self.filters.time_mode = TimeFilterMode::Custom;
                self.filters.custom_start = start_date.format("%Y-%m-%d").to_string();
                self.filters.custom_end = end_date.format("%Y-%m-%d").to_string();
                self.filters.custom_since_ts = Some(since_ts);
                self.filters.custom_before_ts = Some(before_ts);
                self.time_popup_open = false;
                self.time_popup_error.clear();
                self.time_popup_start_replace_on_type = false;
                self.time_popup_end_replace_on_type = false;
                self.status = format!(
                    "Time filter set to custom {}..{}",
                    self.filters.custom_start, self.filters.custom_end
                );
                self.semantic_dirty = !self.search.trim().is_empty();
                self.refresh_base();
                self.apply_view();
            }
            Err(err) => {
                self.time_popup_error = err;
            }
        }
    }

    fn set_field_current(&mut self, field: &str, value: String) {
        let mut should_refresh = true;
        match field {
            "label" => self.filters.label = value,
            "tier" => self.filters.tier = value,
            "agent" => self.filters.agent = value,
            "model" => self.filters.model = value,
            "exit" => self.filters.exit = value,
            "time" => match TimeFilterMode::from_str(value.as_str()) {
                Some(TimeFilterMode::Last7d) => self.apply_time_preset(TimeFilterMode::Last7d),
                Some(TimeFilterMode::Last30d) => self.apply_time_preset(TimeFilterMode::Last30d),
                Some(TimeFilterMode::Last365d) => self.apply_time_preset(TimeFilterMode::Last365d),
                Some(TimeFilterMode::Custom) => {
                    should_refresh = false;
                    self.open_custom_time_popup();
                }
                None => {
                    should_refresh = false;
                }
            },
            "[Reset All]" => {
                self.filters = Filters::default();
            }
            _ => {}
        }
        if field == "time" {
            return;
        }
        if !should_refresh {
            return;
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
        let current = if field == "time" {
            self.filters.time_mode.as_str().to_string()
        } else {
            self.field_current(field)
        };
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

fn time_filter_since_ts(mode: TimeFilterMode, custom_since_ts: Option<i64>, now_ts: i64) -> i64 {
    let bounded_now_ts = now_ts.max(0);
    match mode {
        TimeFilterMode::Last7d => bounded_now_ts.saturating_sub(7 * SECONDS_PER_DAY),
        TimeFilterMode::Last30d => bounded_now_ts.saturating_sub(30 * SECONDS_PER_DAY),
        TimeFilterMode::Last365d => {
            bounded_now_ts.saturating_sub(PROVENANCE_MAX_LOOKBACK_DAYS * SECONDS_PER_DAY)
        }
        TimeFilterMode::Custom => {
            custom_since_ts.unwrap_or_else(|| bounded_now_ts.saturating_sub(7 * SECONDS_PER_DAY))
        }
    }
}

fn local_date_midnight_ts(date: NaiveDate) -> Result<i64, String> {
    let Some(midnight) = date.and_hms_opt(0, 0, 0) else {
        return Err(format!(
            "Could not parse midnight timestamp for {}",
            date.format("%Y-%m-%d")
        ));
    };
    let Some(local_dt) = Local.from_local_datetime(&midnight).single() else {
        return Err(format!(
            "Could not map {} to local timezone midnight",
            date.format("%Y-%m-%d")
        ));
    };
    Ok(local_dt.timestamp())
}

fn validate_custom_time_range(
    start_input: &str,
    end_input: &str,
    today: NaiveDate,
) -> Result<(NaiveDate, NaiveDate), String> {
    let start_trimmed = start_input.trim();
    let end_trimmed = end_input.trim();
    let start_date = parse_custom_date(start_trimmed).ok_or_else(|| {
        "Start date must be YYYY-MM-DD, YYYY/MM/DD, DD-MM-YYYY, or DD/MM/YYYY".to_string()
    })?;
    let end_date = parse_custom_date(end_trimmed).ok_or_else(|| {
        "End date must be YYYY-MM-DD, YYYY/MM/DD, DD-MM-YYYY, or DD/MM/YYYY".to_string()
    })?;
    if start_date > end_date {
        return Err("Start date must be before or equal to end date".to_string());
    }
    let earliest_allowed = today - ChronoDuration::days(PROVENANCE_MAX_LOOKBACK_DAYS);
    if start_date < earliest_allowed || end_date < earliest_allowed {
        return Err(format!(
            "Dates must be within the last {} days",
            PROVENANCE_MAX_LOOKBACK_DAYS
        ));
    }
    if start_date > today || end_date > today {
        return Err("Dates cannot be in the future".to_string());
    }
    let span_days = end_date.signed_duration_since(start_date).num_days() + 1;
    if span_days > CUSTOM_TIME_RANGE_MAX_DAYS {
        return Err(format!(
            "Custom range cannot exceed {} days",
            CUSTOM_TIME_RANGE_MAX_DAYS
        ));
    }
    Ok((start_date, end_date))
}

fn parse_custom_date(input: &str) -> Option<NaiveDate> {
    let formats = ["%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y"];
    for format in formats {
        if let Ok(value) = NaiveDate::parse_from_str(input, format) {
            return Some(value);
        }
    }
    None
}

fn ts_within_time_bounds(ts: i64, since_ts: i64, before_ts: Option<i64>) -> bool {
    if ts < since_ts {
        return false;
    }
    if let Some(bound) = before_ts {
        if ts >= bound {
            return false;
        }
    }
    true
}

fn now_epoch_seconds() -> i64 {
    let Ok(duration) = SystemTime::now().duration_since(UNIX_EPOCH) else {
        return 0;
    };
    duration.as_secs() as i64
}

fn blink_cursor() -> &'static str {
    let Ok(duration) = SystemTime::now().duration_since(UNIX_EPOCH) else {
        return "|";
    };
    if (duration.as_millis() / 500) % 2 == 0 {
        "|"
    } else {
        " "
    }
}

fn default_export_dir() -> String {
    let home = env::var("HOME").unwrap_or_else(|_| ".".to_string());
    let downloads = format!("{}/Downloads", home);
    if Path::new(&downloads).is_dir() {
        return downloads;
    }
    home
}

fn default_export_path(ext: &str) -> String {
    format!(
        "{}/provenance_export_{}.{}",
        default_export_dir(),
        now_epoch_seconds(),
        ext
    )
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

fn push_text_block(lines: &mut Vec<Line<'static>>, text: &str) {
    if text.is_empty() {
        lines.push(Line::from("-"));
        return;
    }
    for part in text.split('\n') {
        lines.push(Line::from(part.to_string()));
    }
}

fn rendered_line_height(line: &Line<'_>, width: usize) -> usize {
    if width == 0 {
        return 1;
    }
    let text = line
        .spans
        .iter()
        .map(|span| span.content.as_ref())
        .collect::<String>();
    let len = text.chars().count();
    if len == 0 {
        1
    } else {
        ((len - 1) / width) + 1
    }
}

fn rendered_content_height(lines: &[Line<'_>], width: usize) -> usize {
    lines
        .iter()
        .map(|line| rendered_line_height(line, width))
        .sum()
}

fn run_table_constraints(compact: bool) -> Vec<Constraint> {
    if compact {
        vec![
            Constraint::Length(5),
            Constraint::Length(18),
            Constraint::Length(16),
            Constraint::Min(30),
            Constraint::Length(4),
            Constraint::Length(6),
            Constraint::Length(10),
        ]
    } else {
        vec![
            Constraint::Length(5),
            Constraint::Length(18),
            Constraint::Length(18),
            Constraint::Min(48),
            Constraint::Length(4),
            Constraint::Length(6),
            Constraint::Length(10),
            Constraint::Length(16),
            Constraint::Length(20),
            Constraint::Length(18),
        ]
    }
}

struct RunsTableHitLayout {
    copy_column: Rect,
    data_start_y: u16,
    page_start: usize,
    row_count: usize,
}

fn runs_table_hit_layout(app: &App) -> Option<RunsTableHitLayout> {
    let (width, height) = terminal_size().ok()?;
    let area = Rect::new(0, 0, width, height);
    let chunks = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3),
            Constraint::Min(8),
            Constraint::Length(2),
        ])
        .split(area);
    let compact = area.width < 120;
    let inner = Rect::new(
        chunks[1].x.saturating_add(1),
        chunks[1].y.saturating_add(1),
        chunks[1].width.saturating_sub(2),
        chunks[1].height.saturating_sub(2),
    );
    let columns = Layout::default()
        .direction(Direction::Horizontal)
        .constraints(run_table_constraints(compact))
        .split(inner);
    let (page_start, page_end) = app.current_page_bounds();
    Some(RunsTableHitLayout {
        copy_column: columns[4],
        data_start_y: chunks[1].y.saturating_add(2),
        page_start,
        row_count: page_end.saturating_sub(page_start),
    })
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
        } else {
            "DB_FILTERED"
        };
        let shown_rows = app.current_page_len();
        let total_rows = app.total_matching.max(app.view_rows.len());
        let current_page = if app.page_count_loaded() == 0 {
            1
        } else {
            app.page + 1
        };
        let total_pages = app.page_count_total();
        let top = Paragraph::new(Line::from(vec![
            Span::styled(
                "Search ",
                Style::default()
                    .fg(Color::Cyan)
                    .add_modifier(Modifier::BOLD),
            ),
            Span::raw(if app.input_mode {
                format!("{}{}", app.search, blink_cursor())
            } else if app.search.is_empty() {
                "(type Ctrl+F)".to_string()
            } else {
                app.search.clone()
            }),
            Span::raw("    "),
            Span::styled(format!("mode={} ", mode), Style::default().fg(Color::Yellow)),
            Span::raw(format!(
                "filters[label={}, tier={}, agent={}, model={}, exit={}, time={}] rows={}/{} (page {}/{})",
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
                if app.filters.model.is_empty() {
                    "*"
                } else {
                    app.filters.model.as_str()
                },
                if app.filters.exit.is_empty() {
                    "*"
                } else {
                    app.filters.exit.as_str()
                },
                app.time_filter_display_value(),
                shown_rows,
                total_rows,
                current_page,
                total_pages,
            )),
        ]))
        .block(
            Block::default().borders(Borders::ALL).title(Line::from(Span::styled(
                "Agensic Provenance",
                agensic_title_style(),
            ))),
        );
        frame.render_widget(top, chunks[0]);

        let compact = area.width < 120;
        let header_style = App::header_style();
        let (page_start, page_end) = app.current_page_bounds();
        let page_rows = if page_start < page_end {
            &app.view_rows[page_start..page_end]
        } else {
            &[]
        };
        let rows: Vec<Row> = page_rows
            .iter()
            .enumerate()
            .map(|(page_idx, row)| {
                let global_idx = page_start + page_idx;
                let dt = Local.timestamp_opt(row.ts, 0).single();
                let time_str = match dt {
                    Some(date) => date.format("%m/%d/%y %H:%M:%S").to_string(),
                    None => row.ts.to_string(),
                };
                let command_text = if compact {
                    truncate_cell(&row.command, 40)
                } else {
                    truncate_cell(&row.command, 80)
                };
                let mut cells = vec![
                    Cell::from((global_idx + 1).to_string()),
                    Cell::from(time_str),
                    Cell::from(truncate_cell(&App::actor_of(row), 18)),
                    Cell::from(command_text).style(App::command_style()),
                    Cell::from(Span::styled(COPY_ICON, App::copy_icon_style())),
                    Cell::from(App::format_exit(row.exit_code)),
                    Cell::from(App::format_duration(row.duration_ms)),
                ];
                if !compact {
                    cells.push(
                        Cell::from(truncate_cell(&row.label, 16))
                            .style(App::label_style(&row.label)),
                    );
                    cells.push(Cell::from(truncate_cell(&row.model, 20)));
                    cells.push(Cell::from(truncate_cell(
                        if row.agent_name.trim().is_empty() {
                            "-"
                        } else {
                            row.agent_name.as_str()
                        },
                        18,
                    )));
                }
                Row::new(cells)
            })
            .collect();

        let constraints = run_table_constraints(compact);

        let header_cells = if compact {
            vec!["n.", "time", "actor", "command", COPY_ICON, "exit", "duration"]
        } else {
            vec![
                "n.",
                "time",
                "actor",
                "command",
                COPY_ICON,
                "exit",
                "duration",
                "label",
                "model",
                "agent_name",
            ]
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
        if !page_rows.is_empty() {
            table_state.select(Some(app.selected));
        }
        frame.render_stateful_widget(table, chunks[1], &mut table_state);

        let footer = Paragraph::new(vec![
            Line::from(Span::styled(
                format!(
                "↑↓ select  Tab/Shift+Tab page  Ctrl+F search  f filters  s sort={}  Enter details  r refresh  e export(json)  E export(csv)  Esc quit",
                app.sort_mode.label(),
                ),
                App::key_hint_style(),
            )),
            Line::from(Span::styled(
                app.status_text().to_string(),
                Style::default().fg(Color::White),
            )),
        ])
        .alignment(Alignment::Left);
        frame.render_widget(footer, chunks[2]);

        if app.filter_menu {
            let popup = centered_rect(58, 46, area);
            let fields = App::filter_fields();
            let mut lines: Vec<Line> = Vec::new();
            lines.push(Line::from(
                "Filter panel (Left/Right change, Up/Down move, Enter/Esc close)",
            ));
            lines.push(Line::from(
                "Use time=custom to set start/end dates (YYYY-MM-DD)",
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

        if app.time_popup_open {
            let popup = centered_rect(58, 40, area);
            let start_style = if app.time_popup_focus == 0 {
                Style::default().fg(Color::Black).bg(Color::Cyan)
            } else {
                Style::default()
            };
            let end_style = if app.time_popup_focus == 1 {
                Style::default().fg(Color::Black).bg(Color::Cyan)
            } else {
                Style::default()
            };
            let mut lines = vec![
                Line::from(
                    "Custom time range (YYYY-MM-DD, last 365 days, max 30 days inclusive)",
                ),
                Line::from("Tab/Up/Down move  Enter apply  Esc cancel"),
                Line::from("Typing overwrites current field value"),
                Line::from(""),
                Line::from(vec![Span::styled(
                    format!("start: {}", app.time_popup_start_input),
                    start_style,
                )]),
                Line::from(vec![Span::styled(
                    format!("end:   {}", app.time_popup_end_input),
                    end_style,
                )]),
            ];
            if !app.time_popup_error.is_empty() {
                lines.push(Line::from(""));
                lines.push(Line::from(vec![Span::styled(
                    app.time_popup_error.clone(),
                    Style::default().fg(Color::LightRed),
                )]));
            }
            let panel = Paragraph::new(lines)
                .block(Block::default().borders(Borders::ALL).title("Time Filter: Custom"))
                .wrap(Wrap { trim: true });
            frame.render_widget(Clear, popup);
            frame.render_widget(panel, popup);
        }

        if app.details_open {
            let popup = centered_rect(90, 80, area);
            if let Some(global_idx) = app.selected_global_index() {
                if let Some(row) = app.view_rows.get(global_idx) {
                    let payload_without_output = match &row.payload {
                        Value::Object(map) => {
                            let mut filtered = map.clone();
                            filtered.remove("captured_stdout_tail");
                            filtered.remove("captured_stderr_tail");
                            filtered.remove("captured_output_truncated");
                            Value::Object(filtered)
                        }
                        other => other.clone(),
                    };
                    let payload_summary = match &payload_without_output {
                        Value::Object(_) | Value::Array(_) => {
                            let text = serde_json::to_string_pretty(&payload_without_output)
                                .unwrap_or_else(|_| "{}".to_string());
                            truncate_cell(&text, 1200)
                        }
                        other => other.to_string(),
                    };
                    let mut details: Vec<Line<'static>> = vec![
                        Line::from(format!("run_id: {}", row.run_id)),
                        Line::from(format!("time: {}", App::format_time(row.ts))),
                        Line::from(format!("actor: {}", App::actor_of(row))),
                        Line::from(format!(
                            "agent_name: {}",
                            if row.agent_name.trim().is_empty() {
                                "-"
                            } else {
                                row.agent_name.as_str()
                            }
                        )),
                        Line::from(vec![
                            Span::raw("label: "),
                            Span::styled(row.label.clone(), App::label_style(&row.label)),
                        ]),
                        Line::from(format!("tier: {}", row.evidence_tier)),
                        Line::from(format!("model: {}", row.model)),
                        Line::from(format!("exit: {}", App::format_exit(row.exit_code))),
                        Line::from(format!("duration: {}", App::format_duration(row.duration_ms))),
                        Line::from(format!("cwd: {}", row.working_directory)),
                        Line::from(""),
                        Line::from(vec![
                            Span::raw("command:  "),
                            Span::styled(COPY_ICON, App::copy_icon_style()),
                        ]),
                        Line::from(Span::styled(row.command.clone(), App::command_style())),
                    ];
                    details.push(Line::from(""));
                    details.push(Line::from("payload:"));
                    push_text_block(&mut details, &payload_summary);
                    let content_width = popup.width.saturating_sub(2) as usize;
                    let content_height = popup.height.saturating_sub(2) as usize;
                    let total_height = rendered_content_height(&details, content_width);
                    let has_overflow = total_height > content_height;
                    let max_scroll =
                        total_height.saturating_sub(content_height).min(u16::MAX as usize) as u16;
                    let scroll_offset = if has_overflow {
                        app.details_scroll.min(max_scroll)
                    } else {
                        0
                    };
                    let mut title_spans = vec![
                        Span::styled("Run details ", App::header_style()),
                        Span::styled("(Enter/Esc close)", App::key_hint_style()),
                    ];
                    if has_overflow {
                        title_spans.push(Span::raw("  "));
                        title_spans.push(Span::styled("↑↓ scroll", App::key_hint_style()));
                    }
                    let panel = Paragraph::new(details)
                        .block(
                            Block::default()
                                .borders(Borders::ALL)
                                .title(Line::from(title_spans)),
                        )
                        .scroll((scroll_offset, 0))
                        .wrap(Wrap { trim: true });
                    frame.render_widget(Clear, popup);
                    frame.render_widget(panel, popup);
                }
            }
        }
    })?;
    Ok(())
}

fn handle_key(app: &mut App, key: KeyEvent) -> bool {
    if key.kind == KeyEventKind::Release {
        return false;
    }

    if app.time_popup_open {
        match key.code {
            KeyCode::Esc => {
                app.time_popup_open = false;
                app.time_popup_error.clear();
                app.time_popup_start_replace_on_type = false;
                app.time_popup_end_replace_on_type = false;
            }
            KeyCode::Enter => app.submit_custom_time_popup(),
            KeyCode::Tab | KeyCode::Down => {
                app.time_popup_focus = (app.time_popup_focus + 1) % 2;
                if app.time_popup_focus == 0 {
                    app.time_popup_start_replace_on_type = true;
                } else {
                    app.time_popup_end_replace_on_type = true;
                }
                app.time_popup_error.clear();
            }
            KeyCode::Up | KeyCode::BackTab => {
                app.time_popup_focus = if app.time_popup_focus == 0 { 1 } else { 0 };
                if app.time_popup_focus == 0 {
                    app.time_popup_start_replace_on_type = true;
                } else {
                    app.time_popup_end_replace_on_type = true;
                }
                app.time_popup_error.clear();
            }
            KeyCode::Backspace => {
                let (active, replace_on_type) = if app.time_popup_focus == 0 {
                    (
                        &mut app.time_popup_start_input,
                        &mut app.time_popup_start_replace_on_type,
                    )
                } else {
                    (
                        &mut app.time_popup_end_input,
                        &mut app.time_popup_end_replace_on_type,
                    )
                };
                *replace_on_type = false;
                active.pop();
                app.time_popup_error.clear();
            }
            KeyCode::Char(c) => {
                if key.modifiers.contains(KeyModifiers::CONTROL) {
                    return false;
                }
                if !(c.is_ascii_digit() || c == '-') {
                    return false;
                }
                let (active, replace_on_type) = if app.time_popup_focus == 0 {
                    (
                        &mut app.time_popup_start_input,
                        &mut app.time_popup_start_replace_on_type,
                    )
                } else {
                    (
                        &mut app.time_popup_end_input,
                        &mut app.time_popup_end_replace_on_type,
                    )
                };
                if *replace_on_type {
                    active.clear();
                    *replace_on_type = false;
                }
                if active.chars().count() < 10 {
                    active.push(c);
                    app.time_popup_error.clear();
                }
            }
            _ => {}
        }
        return false;
    }

    if app.filter_menu {
        match key.code {
            KeyCode::Esc => {
                app.filter_menu = false;
            }
            KeyCode::Enter => {
                if App::filter_fields()[app.filter_cursor] == "[Reset All]" {
                    app.filters = Filters::default();
                    app.semantic_dirty = !app.search.trim().is_empty();
                    app.refresh_base();
                    app.apply_view();
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
        return false;
    }

    if app.details_open {
        match key.code {
            KeyCode::Esc | KeyCode::Enter => {
                app.details_open = false;
                app.details_scroll = 0;
            }
            KeyCode::Up | KeyCode::Char('k') => {
                app.details_scroll = app.details_scroll.saturating_sub(1);
            }
            KeyCode::Down | KeyCode::Char('j') => {
                app.details_scroll = app.details_scroll.saturating_add(1);
            }
            KeyCode::PageUp => {
                app.details_scroll = app.details_scroll.saturating_sub(10);
            }
            KeyCode::PageDown => {
                app.details_scroll = app.details_scroll.saturating_add(10);
            }
            KeyCode::Home => app.details_scroll = 0,
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
        KeyCode::Esc => {
            // Ignore escape bytes that are likely part of an unparsed terminal control sequence
            // (for example malformed mouse wheel input), so they don't force-close the TUI.
            if is_standalone_escape() {
                return true;
            }
            flush_stdin_input_buffer();
        }
        KeyCode::Char('c') if key.modifiers.contains(KeyModifiers::CONTROL) => return true,
        KeyCode::Char('f') if key.modifiers.contains(KeyModifiers::CONTROL) => {
            app.input_mode = true
        }
        KeyCode::Up => app.select_up(),
        KeyCode::Down => app.select_down(),
        KeyCode::Char('k') => app.select_up(),
        KeyCode::Char('j') => app.select_down(),
        KeyCode::PageUp => app.previous_page(),
        KeyCode::PageDown => app.next_page(),
        KeyCode::BackTab => app.previous_page(),
        KeyCode::Tab => app.next_page(),
        KeyCode::Enter => {
            if app.selected_global_index().is_some() {
                app.details_open = true;
                app.details_scroll = 0;
            }
        }
        KeyCode::Char('s') => {
            app.sort_mode = app.sort_mode.next();
            app.apply_view();
        }
        KeyCode::Char('r') => app.refresh_base(),
        KeyCode::Char('f') => app.filter_menu = true,
        KeyCode::Char('e') => {
            let out = default_export_path("json");
            app.export_current("json", &out);
        }
        KeyCode::Char('E') => {
            let out = default_export_path("csv");
            app.export_current("csv", &out);
        }
        _ => {}
    }
    false
}

fn is_standalone_escape() -> bool {
    match event::poll(Duration::from_millis(0)) {
        Ok(false) => true,
        Ok(true) => false,
        Err(_) => true,
    }
}

fn handle_mouse(app: &mut App, mouse: MouseEvent) {
    if app.time_popup_open || app.filter_menu || app.input_mode {
        return;
    }
    if app.details_open {
        if let MouseEventKind::Down(MouseButton::Left) = mouse.kind {
            if let Some(global_idx) = app.selected_global_index() {
                if let Some(row) = app.view_rows.get(global_idx) {
                    if provenance_details_copy_hit(mouse) {
                        match copy_to_clipboard(&row.command) {
                            Ok(()) => app.set_flash("✓ Copied command"),
                            Err(err) => app.set_flash(err),
                        }
                    }
                }
            }
        }
        return;
    }
    if let MouseEventKind::Down(MouseButton::Left) = mouse.kind {
        if let Some(layout) = runs_table_hit_layout(app) {
            let row_offset = mouse.row.saturating_sub(layout.data_start_y) as usize;
            if mouse.column >= layout.copy_column.x
                && mouse.column
                    < layout
                        .copy_column
                        .x
                        .saturating_add(layout.copy_column.width)
                && row_offset < layout.row_count
            {
                let global_idx = layout.page_start + row_offset;
                if let Some(row) = app.view_rows.get(global_idx) {
                    app.selected = row_offset;
                    match copy_to_clipboard(&row.command) {
                        Ok(()) => app.set_flash("✓ Copied command"),
                        Err(err) => app.set_flash(err),
                    }
                    return;
                }
            }
        }
    }
    match mouse.kind {
        MouseEventKind::ScrollDown => app.select_down(),
        MouseEventKind::ScrollUp => app.select_up(),
        _ => {}
    }
}

fn provenance_details_copy_hit(mouse: MouseEvent) -> bool {
    let Ok((width, height)) = terminal_size() else {
        return false;
    };
    let popup = centered_rect(90, 80, Rect::new(0, 0, width, height));
    let command_line_row = popup.y.saturating_add(1 + 11);
    let icon_x = popup.x.saturating_add(11);
    mouse.row == command_line_row
        && mouse.column >= icon_x
        && mouse.column <= icon_x.saturating_add(1)
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
        let export_rows: Vec<Value> = rows
            .iter()
            .map(export_row_json_value)
            .collect::<Result<Vec<_>, _>>()?;
        let payload = json!({"runs": export_rows, "total": rows.len()});
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
            "time",
            "command",
            "label",
            "confidence",
            "actor",
            "agent",
            "agent_name",
            "provider",
            "model",
            "raw_model",
            "normalized_model",
            "model_fingerprint",
            "evidence_tier",
            "agent_source",
            "registry_version",
            "registry_status",
            "source",
            "working_directory",
            "exit_code",
            "exit",
            "duration_ms",
            "duration",
            "shell_pid",
            "evidence",
            "payload",
        ])
        .map_err(|e| format!("write csv header failed: {}", e))?;

    for row in rows {
        let evidence_json = serde_json::to_string(&row.evidence)
            .map_err(|e| format!("serialize evidence failed: {}", e))?;
        let payload_json = serde_json::to_string(&row.payload)
            .map_err(|e| format!("serialize payload failed: {}", e))?;
        writer
            .write_record([
                row.run_id.clone(),
                row.ts.to_string(),
                App::format_time(row.ts),
                row.command.clone(),
                row.label.clone(),
                format!("{:.2}", row.confidence),
                App::actor_of(row),
                row.agent.clone(),
                row.agent_name.clone(),
                row.provider.clone(),
                row.model.clone(),
                row.raw_model.clone(),
                row.normalized_model.clone(),
                row.model_fingerprint.clone(),
                row.evidence_tier.clone(),
                row.agent_source.clone(),
                row.registry_version.clone(),
                row.registry_status.clone(),
                row.source.clone(),
                row.working_directory.clone(),
                row.exit_code.map(|v| v.to_string()).unwrap_or_default(),
                App::format_exit(row.exit_code),
                row.duration_ms.map(|v| v.to_string()).unwrap_or_default(),
                App::format_duration(row.duration_ms),
                row.shell_pid.map(|v| v.to_string()).unwrap_or_default(),
                evidence_json,
                payload_json,
            ])
            .map_err(|e| format!("write csv row failed: {}", e))?;
    }
    writer
        .flush()
        .map_err(|e| format!("flush csv failed: {}", e))?;
    Ok(())
}

fn export_row_json_value(row: &RunEntry) -> Result<Value, String> {
    let mut value =
        serde_json::to_value(row).map_err(|e| format!("serialize export row failed: {}", e))?;
    if let Value::Object(map) = &mut value {
        map.insert("time".to_string(), Value::String(App::format_time(row.ts)));
        map.insert("actor".to_string(), Value::String(App::actor_of(row)));
        map.insert(
            "exit".to_string(),
            Value::String(App::format_exit(row.exit_code)),
        );
        map.insert(
            "duration".to_string(),
            Value::String(App::format_duration(row.duration_ms)),
        );
    }
    Ok(value)
}

fn run_export_mode(client: &Client, args: &Args) -> Result<(), String> {
    let mut app = App::new(client.clone(), args.clone());
    app.search = args.contains.trim().to_string();
    let rows = app.export_dataset_rows()?;
    let out_path = if args.out.trim().is_empty() {
        default_export_path(&args.export)
    } else {
        args.out.clone()
    };
    export_rows(&rows, &args.export, &out_path)?;
    println!("exported {} rows to {}", rows.len(), out_path);
    Ok(())
}

fn run_interactive(client: &Client, args: &Args) -> Result<(), String> {
    // Reset terminal in case a previous run was interrupted and left capture modes enabled.
    cleanup_terminal();

    let mut app = App::new(client.clone(), args.clone());
    if !args.contains.trim().is_empty() {
        app.set_search(args.contains.clone());
        app.refresh_base();
        app.semantic_dirty = false;
    } else {
        app.refresh_base();
    }

    enable_raw_mode().map_err(|e| format!("enable raw mode failed: {}", e))?;
    let mut stdout = io::stdout();
    execute!(stdout, EnterAlternateScreen, EnableMouseCapture)
        .map_err(|e| format!("enter alt screen failed: {}", e))?;
    let backend = CrosstermBackend::new(stdout);
    let mut terminal =
        Terminal::new(backend).map_err(|e| format!("terminal init failed: {}", e))?;

    let mut exit_requested = false;
    while !exit_requested {
        draw_ui(&mut terminal, &app).map_err(|e| format!("draw failed: {}", e))?;

        match event::poll(Duration::from_millis(50)) {
            Ok(true) => match event::read() {
                Ok(ev) => match ev {
                    Event::Key(key) => {
                        exit_requested = handle_key(&mut app, key);
                    }
                    Event::Mouse(mouse) => handle_mouse(&mut app, mouse),
                    _ => {}
                },
                Err(err) => {
                    app.status = format!("Input read error (recovered): {}", err);
                    flush_stdin_input_buffer();
                }
            },
            Ok(false) => {}
            Err(err) => {
                app.status = format!("Input poll error (recovered): {}", err);
                flush_stdin_input_buffer();
            }
        }

        app.tick();
    }

    disable_raw_mode().map_err(|e| format!("disable raw mode failed: {}", e))?;
    execute!(
        terminal.backend_mut(),
        LeaveAlternateScreen,
        DisableMouseCapture
    )
    .map_err(|e| format!("leave alt screen failed: {}", e))?;
    terminal
        .show_cursor()
        .map_err(|e| format!("show cursor failed: {}", e))?;
    io::stdout()
        .flush()
        .map_err(|e| format!("stdout flush failed: {}", e))?;
    flush_stdin_input_buffer();
    Ok(())
}

fn cleanup_terminal() {
    let _ = disable_raw_mode();
    let mut stdout = io::stdout();
    let _ = execute!(stdout, LeaveAlternateScreen, DisableMouseCapture);
    let _ = stdout.flush();
    flush_stdin_input_buffer();
}

fn install_terminal_panic_hook() {
    let default_hook = panic::take_hook();
    panic::set_hook(Box::new(move |info| {
        cleanup_terminal();
        default_hook(info);
    }));
}

fn main() {
    let raw_args: Vec<String> = env::args().collect();
    if matches!(raw_args.get(1).map(String::as_str), Some("sessions")) {
        install_terminal_panic_hook();
        if let Err(err) = sessions::run_from_env(&raw_args[2..]) {
            cleanup_terminal();
            eprintln!("{}", err);
            std::process::exit(1);
        }
        return;
    }

    let args = Args::parse();
    install_terminal_panic_hook();

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

fn flush_stdin_input_buffer() {
    #[cfg(unix)]
    unsafe {
        let _ = libc::tcflush(libc::STDIN_FILENO, libc::TCIFLUSH);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    #[test]
    fn last_7d_since_ts_is_correct() {
        let now_ts = 40 * SECONDS_PER_DAY;
        let since_ts = time_filter_since_ts(TimeFilterMode::Last7d, None, now_ts);
        assert_eq!(since_ts, 33 * SECONDS_PER_DAY);
    }

    #[test]
    fn last_30d_since_ts_is_correct() {
        let now_ts = 75 * SECONDS_PER_DAY;
        let since_ts = time_filter_since_ts(TimeFilterMode::Last30d, None, now_ts);
        assert_eq!(since_ts, 45 * SECONDS_PER_DAY);
    }

    #[test]
    fn last_365d_since_ts_is_correct() {
        let now_ts = 500 * SECONDS_PER_DAY;
        let since_ts = time_filter_since_ts(TimeFilterMode::Last365d, None, now_ts);
        assert_eq!(since_ts, 135 * SECONDS_PER_DAY);
    }

    #[test]
    fn custom_range_parse_success() {
        let today = NaiveDate::from_ymd_opt(2026, 3, 4).expect("valid fixed test date");
        let (start, end) =
            validate_custom_time_range("2026-02-10", "2026-02-20", today).expect("valid range");
        assert_eq!(
            start,
            NaiveDate::from_ymd_opt(2026, 2, 10).expect("valid start")
        );
        assert_eq!(
            end,
            NaiveDate::from_ymd_opt(2026, 2, 20).expect("valid end")
        );
        assert!(local_date_midnight_ts(start).is_ok());
    }

    #[test]
    fn custom_range_rejects_invalid_format() {
        let today = NaiveDate::from_ymd_opt(2026, 3, 4).expect("valid fixed test date");
        let out = validate_custom_time_range("2026.02.10", "2026-02-20", today);
        assert!(out.is_err());
    }

    #[test]
    fn custom_range_accepts_day_first_format() {
        let today = NaiveDate::from_ymd_opt(2026, 3, 4).expect("valid fixed test date");
        let (start, end) =
            validate_custom_time_range("01-03-2026", "04-03-2026", today).expect("valid range");
        assert_eq!(
            start,
            NaiveDate::from_ymd_opt(2026, 3, 1).expect("valid start")
        );
        assert_eq!(end, NaiveDate::from_ymd_opt(2026, 3, 4).expect("valid end"));
    }

    #[test]
    fn custom_range_rejects_more_than_30_days() {
        let today = NaiveDate::from_ymd_opt(2026, 3, 4).expect("valid fixed test date");
        let out = validate_custom_time_range("2026-01-01", "2026-02-01", today);
        assert!(out.is_err());
    }

    #[test]
    fn custom_range_rejects_start_after_end() {
        let today = NaiveDate::from_ymd_opt(2026, 3, 4).expect("valid fixed test date");
        let out = validate_custom_time_range("2026-02-11", "2026-02-10", today);
        assert!(out.is_err());
    }

    #[test]
    fn custom_range_rejects_dates_older_than_365_days() {
        let today = NaiveDate::from_ymd_opt(2026, 3, 4).expect("valid fixed test date");
        let out = validate_custom_time_range("2025-03-03", "2025-03-04", today);
        assert!(out.is_err());
    }

    #[test]
    fn custom_range_rejects_future_dates() {
        let today = NaiveDate::from_ymd_opt(2026, 3, 4).expect("valid fixed test date");
        let out = validate_custom_time_range("2026-03-03", "2026-03-05", today);
        assert!(out.is_err());
    }

    #[test]
    fn row_time_bounds_use_since_and_before() {
        assert!(ts_within_time_bounds(150, 100, Some(200)));
        assert!(!ts_within_time_bounds(99, 100, Some(200)));
        assert!(!ts_within_time_bounds(200, 100, Some(200)));
        assert!(!ts_within_time_bounds(250, 100, Some(200)));
        assert!(ts_within_time_bounds(100, 100, None));
    }

    #[test]
    fn collect_all_runs_pages_fetches_beyond_first_page() {
        let mut call_count = 0usize;
        let page = collect_all_runs_pages(2, |before_ts, before_run_id| {
            call_count += 1;
            match (before_ts, before_run_id) {
                (0, "") => Ok(RunsPage {
                    rows: vec![
                        RunEntry {
                            run_id: "run-3".to_string(),
                            ts: 30,
                            command: "third".to_string(),
                            ..RunEntry::default()
                        },
                        RunEntry {
                            run_id: "run-2".to_string(),
                            ts: 20,
                            command: "second".to_string(),
                            ..RunEntry::default()
                        },
                    ],
                    total_matching: 3,
                }),
                (20, "run-2") => Ok(RunsPage {
                    rows: vec![
                        RunEntry {
                            run_id: "run-2".to_string(),
                            ts: 20,
                            command: "second".to_string(),
                            ..RunEntry::default()
                        },
                        RunEntry {
                            run_id: "run-1".to_string(),
                            ts: 10,
                            command: "first".to_string(),
                            ..RunEntry::default()
                        },
                    ],
                    total_matching: 3,
                }),
                other => panic!("unexpected cursor: {:?}", other),
            }
        })
        .expect("collect paginated rows");

        assert_eq!(call_count, 2);
        assert_eq!(page.total_matching, 3);
        assert_eq!(page.rows.len(), 3);
        assert_eq!(page.rows[0].run_id, "run-3");
        assert_eq!(page.rows[1].run_id, "run-2");
        assert_eq!(page.rows[2].run_id, "run-1");
    }

    fn sample_run_entry() -> RunEntry {
        RunEntry {
            run_id: "run-1".to_string(),
            ts: 1,
            command: "git status".to_string(),
            label: "AI_EXECUTED".to_string(),
            confidence: 1.0,
            agent: "codex".to_string(),
            agent_name: "Codex".to_string(),
            provider: "openai".to_string(),
            model: "gpt-5".to_string(),
            raw_model: "gpt-5-raw".to_string(),
            normalized_model: "gpt-5".to_string(),
            model_fingerprint: "fp-123".to_string(),
            evidence_tier: "proof".to_string(),
            agent_source: "registry".to_string(),
            registry_version: "2026.03".to_string(),
            registry_status: "active".to_string(),
            source: "runtime".to_string(),
            working_directory: "/tmp".to_string(),
            exit_code: Some(1),
            duration_ms: Some(42),
            shell_pid: Some(123),
            evidence: vec!["sig:ok".to_string(), "host:ok".to_string()],
            payload: json!({
                "example": "value"
            }),
        }
    }

    fn temp_export_path(ext: &str) -> std::path::PathBuf {
        let unique = SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .expect("time should be monotonic")
            .as_nanos();
        std::env::temp_dir().join(format!("agensic-provenance-export-{}.{}", unique, ext))
    }

    #[test]
    fn export_json_omits_stderr_and_truncation_fields() {
        let out = temp_export_path("json");
        let rows = vec![sample_run_entry()];

        export_rows(&rows, "json", out.to_str().expect("valid temp path")).expect("json export");

        let text = fs::read_to_string(&out).expect("read json export");
        let payload: Value = serde_json::from_str(&text).expect("parse json export");
        assert_eq!(payload["runs"][0]["time"], App::format_time(1));
        assert_eq!(payload["runs"][0]["actor"], "Codex");
        assert_eq!(payload["runs"][0]["exit"], "1");
        assert_eq!(payload["runs"][0]["duration"], "42ms");
        assert!(payload["runs"][0].get("stderr").is_none());
        assert!(payload["runs"][0].get("output_truncated").is_none());
        assert_eq!(payload["runs"][0]["raw_model"], "gpt-5-raw");
        assert_eq!(payload["runs"][0]["payload"]["example"], "value");
        assert_eq!(payload["runs"][0]["evidence"][0], "sig:ok");

        let _ = fs::remove_file(out);
    }

    #[test]
    fn export_csv_omits_stderr_and_truncation_columns() {
        let out = temp_export_path("csv");
        let rows = vec![sample_run_entry()];

        export_rows(&rows, "csv", out.to_str().expect("valid temp path")).expect("csv export");

        let mut reader = csv::Reader::from_path(&out).expect("open csv export");
        let headers = reader.headers().expect("csv headers").clone();
        assert_eq!(
            headers.iter().collect::<Vec<_>>(),
            vec![
                "run_id",
                "ts",
                "time",
                "command",
                "label",
                "confidence",
                "actor",
                "agent",
                "agent_name",
                "provider",
                "model",
                "raw_model",
                "normalized_model",
                "model_fingerprint",
                "evidence_tier",
                "agent_source",
                "registry_version",
                "registry_status",
                "source",
                "working_directory",
                "exit_code",
                "exit",
                "duration_ms",
                "duration",
                "shell_pid",
                "evidence",
                "payload",
            ]
        );
        let record = reader
            .records()
            .next()
            .expect("csv row present")
            .expect("csv row valid");
        assert_eq!(record.get(2), Some(App::format_time(1).as_str()));
        assert_eq!(record.get(11), Some("gpt-5-raw"));
        assert_eq!(record.get(21), Some("1"));
        assert_eq!(record.get(23), Some("42ms"));
        let evidence: Value =
            serde_json::from_str(record.get(25).expect("evidence field")).expect("parse evidence");
        let payload: Value =
            serde_json::from_str(record.get(26).expect("payload field")).expect("parse payload");
        assert_eq!(evidence[0], "sig:ok");
        assert_eq!(payload["example"], "value");

        let _ = fs::remove_file(out);
    }
}
