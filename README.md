# Agensic

<p align="center">
  <img src="./assets/agensic-logo.png" alt="Agensic logo" width="100%" />
</p>

<p align="center">
  Know what ran. Know who suggested it. Prove what the agent actually executed.
</p>

<p align="center">
  Agensic upgrades your existing terminal with two things: trustworthy observability and genuinely useful autocomplete
</p>

<p align="center">
  <code>signed provenance</code>
  <code>tracked sessions</code>
  <code>semantic search</code>
  <code>vector-first suggestions</code>
  <code>AI fallback</code>
  <code>local-first privacy</code>
</p>

Agensic is a local daemon, shell integration, and CLI for teams that want AI in the terminal without losing attribution, replayability, or control. It keeps your shell intact, adds smart command completion on top, and records enough forensic evidence to answer the uncomfortable question later: "what happened here, and was it the human or the agent?"

It works in your existing Zsh environment on macOS and Linux, with macOS-specific tracking and startup integrations where supported.

## Observability

Agensic records command provenance, signs agent-executed runs, tracks interactive agent sessions, and gives you fast ways to inspect, filter, replay, and export what happened. The result is a terminal workflow you can trust after the fact, not just during the suggestion.

### What you get

- Signed `AI_EXECUTED` proof metadata using local Ed25519 signing, with key and host fingerprints attached to the run record.
- Provenance classification for executed commands, including labels such as `AI_EXECUTED`, `AI_SUGGESTED_HUMAN_RAN`, `AG_SUGGESTED_HUMAN_RAN`, `HUMAN_TYPED`, `INVALID_PROOF`, and `UNKNOWN`.
- Agent inference from proof payloads, provider/model metadata, and process lineage when explicit proof is absent.
- A provenance registry for known agents, with registry listing and agent-detail inspection from the CLI.
- Full provenance history search from the CLI and a full-screen Rust TUI with filters, sorting, semantic search, and one-shot JSON/CSV export.
- Tracked session capture for interactive agent CLIs, including session metadata, event timelines, transcript inspection, and replay.
- Session replay fallbacks when full terminal transcripts have been pruned, so inspection still degrades gracefully.
- Local auth on every localhost API route, with automatic token rotation on daemon startup and setup flows.
- SQLite state, append-only journal events, snapshots, recovery tooling, and repair commands designed for long-lived local use.
- Privacy guardrails before LLM egress, including redaction of secrets, tokens, dotenv content, URL credentials, and other high-entropy values.

### Why it matters

- You can prove that an agent-executed command was really wrapped and signed by Agensic.
- You can distinguish human typing from AI-suggested execution instead of flattening everything into "shell history."
- You can inspect an agent session as a session, not as a pile of unrelated commands.
- You can export evidence when you need to share, debug, review, or document an incident.

### Observability workflow

```bash
bash ./install.sh
agensic setup

agensic provenance --tui
agensic sessions
agensic track codex
agensic track inspect <session_id>

agensic_session_start --agent codex --model gpt-5.3 --agent-name "Planner A" --ttl-minutes 120
agensic_session_status
agensic_session_stop
```

### Observability surfaces

- `agensic provenance --tui` opens the provenance interface and can export the current filtered dataset.
- `agensic sessions` opens the tracked sessions browser.
- `agensic track ...` launches supported agent CLIs under session tracking on macOS.
- `agensic ai-exec --agent ... --model ... -- <command>` wraps one-off executions with deterministic proof metadata.
- `agensic doctor`, `agensic fix --safe`, and `agensic fix --recover` help keep long-running local state healthy.

## Autocomplete

Agensic starts with your real command history, indexes it semantically, keeps suggestions local and fast, learns from your usage patterns (what commands you run most often and where) and only reaches for an LLM when history cannot help. It is built to feel immediate in the shell.

### What you get

- Prefix suggestions that feel instant, with local in-memory filtering as you keep typing
- Semantic recovery when the exact prefix misses but the intent is still obvious
- Word-by-word typo recovery that turns wrong words into suggestions, for example `dokcer` into `Did you mean: docker`
- AI fallback only when needed, with per-line budgeting and explicit manual triggering
- `#` command mode for guardrailed natural-language-to-command generation
- `##` assistant mode for free-text answers directly in the terminal
- Manual command-store curation so you can add commands, remove bad ones, and clean up typo-like variants from the suggestion pool
- Native completion stays in control for path-heavy and script/file contexts, so Agensic does not steal `Tab` where shell completion already wins
- High-risk commands are blocked from suggestion and feedback flows instead of being "helpfully" completed

### Why it feels different

- Most suggestions come from your own history, so they are fast and relevant
- AI is a fallback, not the default tax on every keystroke
- Semantic reranking and typo recovery rescue messy real-world input instead of assuming perfect prefixes
- The shell integration keeps the flow native: ghost text, cycling, partial accept, and explicit triggers all behave like terminal features, not chatbot overlays

### Autocomplete flow

```bash
$ git st
# pause briefly
# instant history-backed suggestions appear

$ dokcer
# first suggestion: Did you mean: docker

$ docker terminate my-app
# semantic recovery can rescue intent when wording shifts away from the exact command in history

# create a command from natural language
# list the 10 largest files in the current directory

## explain why git rebase rewrites commit hashes
```

### Keyboard controls

- `Tab`: accept the current suggestion
- `Option+Right`: accept the next word only
- `Ctrl+N` / `Ctrl+P`: cycle suggestions
- `Ctrl+Space`: manually fetch a new suggestion
- `Esc`: clear visible ghost text
- `Enter`: run the command and log its provenance

## Install

The fastest path is the managed installer:

```bash
bash ./install.sh
```

That installer:

- copies the Zsh integration and helper scripts
- builds or downloads the provenance TUI sidecar
- installs Agensic into an isolated virtual environment
- writes stable launchers into `~/.local/bin`
- wires your shell to source `agensic.zsh`

Then open a new terminal and run:

```bash
agensic setup
```

If you only want the CLI without the shell wiring, you can install it with:

```bash
uv tool install .
# or
pipx install .
```

## Quick Start

```bash
agensic start
agensic auth status
agensic doctor

agensic provenance --tui
agensic sessions
agensic shortcuts
```

## Provider Support

Agensic supports local and hosted models, including:

- OpenAI
- Anthropic
- Gemini
- Groq
- Ollama
- LM Studio
- DashScope
- DeepSeek
- MiniMax
- Mistral
- Moonshot
- OpenRouter
- Xiaomi MiMo
- Z.AI
- AWS SageMaker
- Custom OpenAI-compatible endpoints
- `history_only` mode if you want history-powered autocomplete with no AI calls at all

## Safety And Privacy

- All local API routes are authenticated.
- Secrets are sanitized before LLM egress.
- Outbound LLM calls are rate-limited and timeout-bounded.
- Per-line LLM budgets stop autocomplete from quietly turning into a prompt spammer.
- Destructive commands such as `rm -rf`, `dd`, `mkfs`, `history -c`, and `git reset --hard` are blocked from suggestion flows.

## Architecture

```text
Zsh plugin -> shell client -> local FastAPI daemon -> suggestion engine
                                              |-> provenance + session APIs
                                              |-> SQLite state + journal + snapshots
                                              |-> vector command index
                                              |-> optional LLM provider
```

## License

Apache-2.0
