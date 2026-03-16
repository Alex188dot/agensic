use clap::Parser;
use crossterm::event::{
    self, DisableMouseCapture, EnableMouseCapture, Event, KeyCode, KeyEvent, KeyEventKind,
};
use crossterm::execute;
use crossterm::terminal::{
    disable_raw_mode, enable_raw_mode, EnterAlternateScreen, LeaveAlternateScreen,
};
use ratatui::backend::CrosstermBackend;
use ratatui::layout::{Constraint, Direction, Layout};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span, Text};
use ratatui::widgets::{Block, Borders, Clear, List, ListItem, ListState, Paragraph, Wrap};
use ratatui::Terminal;
use serde::Deserialize;
use std::fs;
use std::io::{self, Stdout, Write};
use std::time::Duration;

#[derive(Debug, Parser, Clone)]
#[command(name = "agensic-provenance-tui agents")]
pub struct AgentsArgs {
    #[arg(long, default_value = "")]
    input: String,
}

#[derive(Clone, Debug, Default, Deserialize)]
struct AgentEntry {
    #[serde(default)]
    agent_id: String,
    #[serde(default)]
    display_name: String,
    #[serde(default)]
    source: String,
    #[serde(default)]
    status: String,
    #[serde(default)]
    executables: Vec<String>,
    #[serde(default)]
    aliases: Vec<String>,
}

#[derive(Debug, Default, Deserialize)]
struct AgentsPayload {
    #[serde(default)]
    agents: Vec<AgentEntry>,
}

struct App {
    agents: Vec<AgentEntry>,
    selected: usize,
    flash_status: Option<crate::FlashStatus>,
}

impl App {
    fn new(mut agents: Vec<AgentEntry>) -> Self {
        agents.retain(|agent| !agent.agent_id.trim().is_empty());
        Self {
            agents,
            selected: 0,
            flash_status: None,
        }
    }

    fn selected_agent(&self) -> Option<&AgentEntry> {
        self.agents.get(self.selected)
    }

    fn next(&mut self) {
        if self.selected + 1 < self.agents.len() {
            self.selected += 1;
        }
    }

    fn previous(&mut self) {
        if self.selected > 0 {
            self.selected -= 1;
        }
    }

    fn first(&mut self) {
        self.selected = 0;
    }

    fn last(&mut self) {
        if !self.agents.is_empty() {
            self.selected = self.agents.len() - 1;
        }
    }

    fn copy_selected_agent_id(&mut self) {
        let Some(agent) = self.selected_agent() else {
            self.flash_status = Some(crate::FlashStatus::new("No agent selected"));
            return;
        };
        match crate::copy_to_clipboard(&agent.agent_id) {
            Ok(()) => {
                self.flash_status = Some(crate::FlashStatus::new(format!(
                    "Copied `{}` to clipboard",
                    agent.agent_id
                )));
            }
            Err(err) => {
                self.flash_status = Some(crate::FlashStatus::new(err));
            }
        }
    }
}

pub fn run_from_env(raw_args: &[String]) -> Result<(), String> {
    let mut argv = vec!["agensic-provenance-tui agents".to_string()];
    argv.extend(raw_args.iter().cloned());
    let args = AgentsArgs::parse_from(argv);
    let payload = load_payload(&args)?;
    let app = App::new(payload.agents);
    if app.agents.is_empty() {
        return Err("no agents found".to_string());
    }
    run_interactive(app)
}

fn load_payload(args: &AgentsArgs) -> Result<AgentsPayload, String> {
    let input_path = args.input.trim();
    if input_path.is_empty() {
        return Err("agents input path is required".to_string());
    }
    let raw = fs::read_to_string(input_path)
        .map_err(|err| format!("failed to read agents payload: {}", err))?;
    serde_json::from_str::<AgentsPayload>(&raw)
        .map_err(|err| format!("invalid agents payload: {}", err))
}

fn run_interactive(mut app: App) -> Result<(), String> {
    let mut terminal = setup_terminal()?;
    let result = run_app(&mut terminal, &mut app);
    restore_terminal(&mut terminal)?;
    result
}

fn setup_terminal() -> Result<Terminal<CrosstermBackend<Stdout>>, String> {
    enable_raw_mode().map_err(|err| format!("enable raw mode failed: {}", err))?;
    let mut stdout = io::stdout();
    execute!(stdout, EnterAlternateScreen, EnableMouseCapture)
        .map_err(|err| format!("enter alt screen failed: {}", err))?;
    let backend = CrosstermBackend::new(stdout);
    Terminal::new(backend).map_err(|err| format!("terminal init failed: {}", err))
}

fn restore_terminal(terminal: &mut Terminal<CrosstermBackend<Stdout>>) -> Result<(), String> {
    disable_raw_mode().map_err(|err| format!("disable raw mode failed: {}", err))?;
    execute!(
        terminal.backend_mut(),
        LeaveAlternateScreen,
        DisableMouseCapture
    )
    .map_err(|err| format!("leave alt screen failed: {}", err))?;
    terminal
        .show_cursor()
        .map_err(|err| format!("show cursor failed: {}", err))?;
    io::stdout()
        .flush()
        .map_err(|err| format!("stdout flush failed: {}", err))?;
    Ok(())
}

fn run_app(terminal: &mut Terminal<CrosstermBackend<Stdout>>, app: &mut App) -> Result<(), String> {
    loop {
        terminal
            .draw(|frame| draw_ui(frame, app))
            .map_err(|err| format!("draw failed: {}", err))?;
        if !event::poll(Duration::from_millis(50)).map_err(|err| format!("poll failed: {}", err))? {
            continue;
        }
        match event::read().map_err(|err| format!("read event failed: {}", err))? {
            Event::Key(key) if key.kind == KeyEventKind::Press => {
                if handle_key(app, key) {
                    return Ok(());
                }
            }
            Event::Resize(_, _) => {}
            _ => {}
        }
    }
}

fn handle_key(app: &mut App, key: KeyEvent) -> bool {
    match key.code {
        KeyCode::Esc | KeyCode::Char('q') => return true,
        KeyCode::Up | KeyCode::Char('k') => app.previous(),
        KeyCode::Down | KeyCode::Char('j') => app.next(),
        KeyCode::Home | KeyCode::Char('g') => app.first(),
        KeyCode::End | KeyCode::Char('G') => app.last(),
        KeyCode::Char('c') => app.copy_selected_agent_id(),
        _ => {}
    }
    false
}

fn draw_ui(frame: &mut ratatui::Frame<'_>, app: &App) {
    let area = frame.area();
    frame.render_widget(Clear, area);
    let root = Layout::default()
        .direction(Direction::Vertical)
        .constraints([
            Constraint::Length(3),
            Constraint::Min(8),
            Constraint::Length(2),
        ])
        .split(area);

    let split = if root[1].width >= 92 {
        Layout::default()
            .direction(Direction::Horizontal)
            .constraints([Constraint::Percentage(46), Constraint::Percentage(54)])
            .split(root[1])
    } else {
        Layout::default()
            .direction(Direction::Vertical)
            .constraints([Constraint::Percentage(45), Constraint::Percentage(55)])
            .split(root[1])
    };

    let header_message = app
        .flash_status
        .as_ref()
        .and_then(crate::FlashStatus::active_message)
        .unwrap_or("Responsive agent browser. Resize freely; the layout reflows instead of dropping the view.");
    let header = Paragraph::new(Text::from(vec![
        Line::from(vec![
            Span::styled("Show All Supported Agents", crate::agensic_title_style()),
            Span::raw(format!(
                "  {} known mappings",
                crate::format_compact_count(app.agents.len())
            )),
        ]),
        Line::from(Span::styled(
            header_message,
            Style::default().fg(Color::Gray),
        )),
    ]))
    .block(Block::default().borders(Borders::ALL));
    frame.render_widget(header, root[0]);

    draw_agent_list(frame, split[0], app);
    draw_agent_detail(frame, split[1], app);

    let footer = Paragraph::new(Text::from(Line::from(vec![
        Span::styled(
            "q",
            Style::default()
                .fg(Color::LightCyan)
                .add_modifier(Modifier::BOLD),
        ),
        Span::raw(" quit  "),
        Span::styled(
            "j/k",
            Style::default()
                .fg(Color::LightCyan)
                .add_modifier(Modifier::BOLD),
        ),
        Span::raw(" move  "),
        Span::styled(
            "g/G",
            Style::default()
                .fg(Color::LightCyan)
                .add_modifier(Modifier::BOLD),
        ),
        Span::raw(" first/last  "),
        Span::styled(
            "c",
            Style::default()
                .fg(Color::LightCyan)
                .add_modifier(Modifier::BOLD),
        ),
        Span::raw(" copy agent id"),
    ])))
    .block(Block::default().borders(Borders::ALL));
    frame.render_widget(footer, root[2]);
}

fn draw_agent_list(frame: &mut ratatui::Frame<'_>, area: ratatui::layout::Rect, app: &App) {
    let compact = area.width < 54;
    let items: Vec<ListItem> = app
        .agents
        .iter()
        .map(|agent| {
            let title =
                if agent.display_name.trim().is_empty() || agent.display_name == agent.agent_id {
                    agent.agent_id.clone()
                } else if compact {
                    format!("{} ({})", agent.display_name, agent.agent_id)
                } else {
                    format!("{}  [{}]", agent.display_name, agent.agent_id)
                };
            let mut meta_parts = Vec::new();
            if !agent.source.trim().is_empty() {
                meta_parts.push(agent.source.clone());
            }
            if !agent.status.trim().is_empty() {
                meta_parts.push(agent.status.clone());
            }
            meta_parts.push(format!("{} exec", agent.executables.len()));
            meta_parts.push(format!("{} alias", agent.aliases.len()));
            ListItem::new(Text::from(vec![
                Line::from(Span::styled(
                    title,
                    Style::default().add_modifier(Modifier::BOLD),
                )),
                Line::from(Span::styled(
                    meta_parts.join("  •  "),
                    Style::default().fg(Color::Gray),
                )),
            ]))
        })
        .collect();
    let list = List::new(items)
        .block(Block::default().borders(Borders::ALL).title("Known Agents"))
        .highlight_symbol("› ")
        .highlight_style(
            Style::default()
                .fg(Color::Black)
                .bg(Color::LightGreen)
                .add_modifier(Modifier::BOLD),
        );
    let mut state = ListState::default();
    if !app.agents.is_empty() {
        state.select(Some(app.selected));
    }
    frame.render_stateful_widget(list, area, &mut state);
}

fn draw_agent_detail(frame: &mut ratatui::Frame<'_>, area: ratatui::layout::Rect, app: &App) {
    let text = if let Some(agent) = app.selected_agent() {
        let display_name = if agent.display_name.trim().is_empty() {
            agent.agent_id.clone()
        } else {
            agent.display_name.clone()
        };
        let executables = if agent.executables.is_empty() {
            "-".to_string()
        } else {
            agent.executables.join(", ")
        };
        let aliases = if agent.aliases.is_empty() {
            "-".to_string()
        } else {
            agent.aliases.join(", ")
        };
        Text::from(vec![
            Line::from(Span::styled(
                display_name,
                Style::default()
                    .fg(Color::LightCyan)
                    .add_modifier(Modifier::BOLD),
            )),
            Line::from(""),
            detail_line("Agent ID", &agent.agent_id),
            detail_line("Source", empty_dash(&agent.source)),
            detail_line("Status", empty_dash(&agent.status)),
            Line::from(""),
            detail_line("Executables", &executables),
            Line::from(""),
            detail_line("Aliases", &aliases),
        ])
    } else {
        Text::from("No agent selected")
    };
    let panel = Paragraph::new(text)
        .block(Block::default().borders(Borders::ALL).title("Details"))
        .wrap(Wrap { trim: true });
    frame.render_widget(panel, area);
}

fn detail_line(label: &str, value: &str) -> Line<'static> {
    Line::from(vec![
        Span::styled(
            format!("{label}: "),
            Style::default()
                .fg(Color::LightYellow)
                .add_modifier(Modifier::BOLD),
        ),
        Span::raw(value.to_string()),
    ])
}

fn empty_dash(value: &str) -> &str {
    if value.trim().is_empty() {
        "-"
    } else {
        value
    }
}
