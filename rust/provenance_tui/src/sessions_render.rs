use ratatui::layout::Rect;
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, Borders};

const TERMINAL_REPLAY_END_PADDING_ROWS: u16 = 20;
const TERMINAL_REPLAY_CURSOR_BOTTOM_PADDING_ROWS: u16 = 2;

pub(crate) fn pane_block(title: &str, focused: bool) -> Block<'static> {
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

pub(crate) fn pane_block_title(title: Line<'static>, focused: bool) -> Block<'static> {
    let border_style = if focused {
        Style::default().fg(Color::LightGreen)
    } else {
        Style::default().fg(Color::DarkGray)
    };
    Block::default()
        .borders(Borders::ALL)
        .border_style(border_style)
        .title(title)
}

pub(crate) fn replay_toggle_style(hovered: bool) -> Style {
    if hovered {
        Style::default()
            .fg(Color::LightCyan)
            .add_modifier(Modifier::UNDERLINED | Modifier::BOLD)
    } else {
        Style::default()
            .fg(Color::Cyan)
            .add_modifier(Modifier::BOLD)
    }
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

pub(crate) fn flush_terminal_span(
    spans: &mut Vec<Span<'static>>,
    buffer: &mut String,
    style: Option<Style>,
) {
    if buffer.is_empty() {
        return;
    }
    let text = std::mem::take(buffer);
    match style {
        Some(style) => spans.push(Span::styled(text, style)),
        None => spans.push(Span::raw(text)),
    }
}

pub(crate) fn terminal_cell_style(cell: &vt100::Cell) -> Style {
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

pub(crate) fn vt100_color_to_ratatui(color: vt100::Color) -> Color {
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

pub(crate) fn summarize_terminal_lines(lines: &[String]) -> String {
    let first = truncate(lines.first().map(String::as_str).unwrap_or("-"), 52);
    if lines.len() <= 1 {
        first
    } else {
        format!("{} (+{} lines)", first, lines.len() - 1)
    }
}

pub(crate) fn push_text_block(lines: &mut Vec<Line<'static>>, text: &str) {
    for line in text.lines() {
        lines.push(Line::from(line.to_string()));
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

pub(crate) fn diff_stat_line(line: &str) -> Line<'static> {
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

pub(crate) fn collapse_blank_runs(value: &str, max_blank_lines: usize) -> String {
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

pub(crate) fn replay_max_scroll(value: &str, area: Rect) -> u16 {
    let content_lines = rendered_text_height(value, area.width.saturating_sub(2).max(1) as usize);
    let visible_lines = area.height.saturating_sub(2).max(1) as usize;
    content_lines
        .saturating_sub(visible_lines)
        .min(u16::MAX as usize) as u16
}

pub(crate) fn terminal_replay_end_padding(frame_index: usize, total_frames: usize) -> u16 {
    if total_frames > 0 && frame_index + 1 >= total_frames {
        TERMINAL_REPLAY_END_PADDING_ROWS
    } else {
        0
    }
}

pub(crate) fn terminal_replay_scroll(
    line_count: usize,
    row_count: u16,
    cursor_row: u16,
    last_content_row: u16,
    area: Rect,
    padding_rows: u16,
) -> u16 {
    let visible_lines = area.height.saturating_sub(2).max(1);
    let content_rows = line_count.max(row_count as usize).min(u16::MAX as usize) as u16;
    let total_rows = content_rows.saturating_add(padding_rows);
    let cursor_anchor = cursor_row
        .min(content_rows.saturating_sub(1))
        .saturating_add(TERMINAL_REPLAY_CURSOR_BOTTOM_PADDING_ROWS);
    let content_anchor = last_content_row
        .min(content_rows.saturating_sub(1))
        .saturating_add(TERMINAL_REPLAY_CURSOR_BOTTOM_PADDING_ROWS)
        .min(total_rows.saturating_sub(1));
    let anchor_row = cursor_anchor
        .max(content_anchor)
        .min(total_rows.saturating_sub(1));
    anchor_row
        .saturating_add(1)
        .max(visible_lines)
        .saturating_sub(visible_lines)
}

pub(crate) fn terminal_replay_max_scroll_x(cols: u16, area: Rect) -> u16 {
    let visible_cols = area.width.saturating_sub(2).max(1);
    cols.saturating_sub(visible_cols)
}

pub(crate) fn rendered_text_height(value: &str, width: usize) -> usize {
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

pub(crate) fn strip_inline_progress_noise(value: &str) -> String {
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

fn truncate(value: &str, max_chars: usize) -> String {
    let value_chars = value.chars().count();
    if value_chars <= max_chars {
        return value.to_string();
    }
    if max_chars <= 1 {
        return "…".to_string();
    }
    let keep = max_chars.saturating_sub(1);
    let mut out = String::new();
    for ch in value.chars().take(keep) {
        out.push(ch);
    }
    out.push('…');
    out
}
