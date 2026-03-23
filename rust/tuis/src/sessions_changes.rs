use super::*;

pub(super) fn build_changes_lines(detail: &DetailState) -> Vec<Line<'static>> {
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
            let files: Vec<String> = preferred_files
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
            let files: Vec<String> = preferred_files
                .iter()
                .map(|item| sanitize_inline_text(item))
                .filter(|item| !item.is_empty())
                .collect();
            let normalized_markers = normalize_cumulative_markers_against_session_start(
                detail,
                &files,
                &record.cumulative_file_markers,
            );
            (preferred_stats, files, normalized_markers)
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
        "No repo changes recorded in this checkpoint",
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
        "No repo changes recorded since session start",
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
pub(super) struct GitRangeChangeView {
    stats: ParsedDiffStat,
    files: Vec<String>,
    markers: BTreeMap<String, String>,
}

#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub(super) struct GitRangeChangeViewCacheKey {
    repo_root: String,
    start_head: String,
    end_head: String,
}

#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub(super) struct GitPathExistenceCacheKey {
    repo_root: String,
    head: String,
    path: String,
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

fn normalize_cumulative_markers_against_session_start(
    detail: &DetailState,
    files: &[String],
    markers: &BTreeMap<String, String>,
) -> BTreeMap<String, String> {
    let clean_repo_root = detail.session.repo_root.trim();
    let clean_head_start = detail.session.head_start.trim();
    if clean_repo_root.is_empty() || clean_head_start.is_empty() {
        return markers
            .iter()
            .filter_map(|(path, marker)| {
                let clean_path = sanitize_inline_text(path);
                let clean_marker = sanitize_inline_text(marker);
                if clean_path.is_empty() || clean_marker.is_empty() {
                    None
                } else {
                    Some((clean_path, clean_marker))
                }
            })
            .collect();
    }
    let mut normalized = BTreeMap::new();
    for file in files {
        let existing_marker = markers
            .get(file)
            .map(|value| sanitize_inline_text(value))
            .filter(|value| !value.is_empty())
            .unwrap_or_else(|| "•".to_string());
        let normalized_marker = if existing_marker == "-" {
            "-".to_string()
        } else if git_path_exists_at_head_cached(detail, &detail.session.head_start, file) {
            "~".to_string()
        } else {
            "+".to_string()
        };
        normalized.insert(file.clone(), normalized_marker);
    }
    normalized
}

fn git_path_exists_at_head_cached(detail: &DetailState, head: &str, path: &str) -> bool {
    let key = GitPathExistenceCacheKey {
        repo_root: detail.session.repo_root.trim().to_string(),
        head: head.trim().to_string(),
        path: path.trim().to_string(),
    };
    if key.repo_root.is_empty() || key.head.is_empty() || key.path.is_empty() {
        return false;
    }
    if let Some(cached) = detail.git_path_exists_cache.borrow().get(&key).copied() {
        return cached;
    }
    let spec = format!("{}:{}", key.head, key.path);
    let exists = Command::new("git")
        .arg("-C")
        .arg(&key.repo_root)
        .args(["cat-file", "-e", &spec])
        .output()
        .map(|output| output.status.success())
        .unwrap_or(false);
    detail
        .git_path_exists_cache
        .borrow_mut()
        .insert(key, exists);
    exists
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

pub(super) fn changes_max_scroll(detail: &DetailState, area: Rect) -> u16 {
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

pub(super) fn build_changes(detail: &DetailState, area: Rect) -> Paragraph<'static> {
    Paragraph::new(build_changes_lines(detail))
        .block(pane_block("Changes", detail.focus == FocusPane::Changes))
        .scroll((
            detail.changes_scroll.min(changes_max_scroll(detail, area)),
            0,
        ))
        .wrap(Wrap { trim: true })
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
