# Search Feature Plan for Agensic Sessions TUI

## Overview
Implement search in the **session list view** (browser mode) via key binding 's' to filter sessions by conversation text content.

## Current State
- **'s' key** is **FREE** in browser mode (not used)
- Browser mode keys: `f` (filter), `R` (rename), `D` (delete), `r` (refresh), `Enter` (open detail)
- Session events are stored in JSONL files (`event_stream_path`), not in SQLite
- Backend API: `/sessions` returns metadata only, `/sessions/{id}/events` returns full events

## Search Flow
1. Press `s` in session list → search bar overlay appears
2. Type query (e.g., "model") → stored in search buffer
3. Press `Enter` → execute search via backend API
4. Results replace filtered session list
5. Press `Esc` to clear search and restore full list

## Implementation Plan

### 1. Backend: Add Search API Endpoint
**File**: `agensic/server/routes_sessions.py`
```
GET /sessions/search?q=<query>&status=<status>&agent=<agent>&model=<model>&repo=<repo>&branch=<branch>
```
- Search through session event files for query string
- Respect existing filters (AND logic)
- Return matching session IDs/summaries

### 2. Backend: Search Implementation
**File**: `agensic/cli/track.py`
- Add `search_sessions(query, filters)` function
- Load events from `event_stream_path` files
- Case-insensitive substring match on event payload text
- Return matching `session_id`s

### 3. TUI: Search State
**File**: `rust/tuis/src/sessions.rs`
```rust
struct App {
    // ... existing fields ...
    search_active: bool,
    search_query: String,
    search_results: Vec<SessionSummary>,  // or just session_ids
}
```

### 4. TUI: Key Handling (browser mode)
```rust
KeyCode::Char('s') => app.start_search(),
// In search mode:
KeyCode::Esc => app.cancel_search(),
KeyCode::Enter => app.execute_search(),
KeyCode::Char(c) => app.search_query.push(c),
KeyCode::Backspace => app.search_query.pop(),
```

### 5. TUI: Search Bar UI
- Overlay search bar at bottom of screen
- Show query string and match count
- Render in `draw_browser()`

### 6. Filter Integration
- Search combines with existing `SessionFilters` (AND logic)
- Filters still applied first, then search refines results

## Search Scope
- Searches event `payload` text content (terminal output, commands)
- Event types: `terminal.stdout`, `command.recorded`, etc.
- Case-insensitive substring match

## Files to Modify
1. `agensic/server/routes_sessions.py` - Add search endpoint
2. `agensic/cli/track.py` - Add search implementation
3. `rust/tuis/src/sessions.rs` - Add search state, key handling, UI
