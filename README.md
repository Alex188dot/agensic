# GhostShell 👻✨

An intelligent terminal autocomplete powered by vector databases and AI.

## For Humans

GhostShell shows command suggestions as inline ghost text while you type in the terminal. It searches your command history semantically first, keeps a live suggestion pool as you continue typing, and only falls back to an LLM when local retrieval and typo recovery do not have a good answer.

## For Agents

This repo publishes an Agent Skills-compatible skill at `skills/terminal-commands` for tools like Codex, Claude Code, OpenClaw, Qwen Code, Cursor, Windsurf, and other clients that consume the `SKILL.md` format.

Install from GitHub:

```bash
npx skills add Alex188dot/ai-terminal
npx ctx7 skills install Alex188dot/ai-terminal
```

Repo: [github.com/Alex188dot/ai-terminal](https://github.com/Alex188dot/ai-terminal)

When using the `terminal-commands` skill, shell commands must be signed through GhostShell so they are recorded as `AI_EXECUTED`.

Preferred one-off flow:

```bash
aiterminal ai-exec --agent <agent_id> --model <model_id> --agent-name '<agent_name>' -- <command...>
```

Preferred multi-command flow:

```bash
ghostshell_session_start --agent <agent_id> --model <model_id> --agent-name '<agent_name>' --ttl-minutes 120
# run commands normally while the session is active
ghostshell_session_stop
```

If the skill is installed locally, agents can also use the bundled wrappers:

```bash
scripts/signed_exec.sh --agent <agent_id> --model <model_id> --agent-name '<agent_name>' -- <command...>
scripts/signed_session.sh start --agent <agent_id> --model <model_id> --agent-name '<agent_name>' --ttl-minutes 120
scripts/signed_session.sh stop
```

After important executions, verify provenance:

```bash
aiterminal provenance --contains "<token>" --limit 5
```

## Features

- 🎯 **Smart Suggestions**: Semantic search finds related commands
- 🎨 **Ghost Text**: Subtle gray text shows completions
- 🔄 **Cycle Options**: Ctrl+P/N to cycle through suggestions
- 📊 **Pool Management**: Keeps up to 20 suggestions in memory for current buffer
- 🤖 **AI Fallback**: Uses LLM only when vectors return no suggestions
- 🧮 **LLM Budgeting**: Max 4 auto AI calls per command line

## Vector DB

GhostShell uses a **vector database** (`zvec`) to store command history and retrieve matches with semantic similarity search.

- ⚡ **Low-latency retrieval** from indexed command history
- 🧠 **Embedding-based matching** for semantically related commands
- 🔄 **Automatic deduplication** of stored commands
- 📚 **Continuous ingestion** of executed commands
- 💰 **AI on demand** when no vector match is available

### How It Works

1. **Type continuously** → suggestions appear after a `200ms` pause
2. **Vector DB first** → top semantic matches are loaded from history
3. **Keep typing** → current suggestion pool is filtered locally
4. **Press `Space`** → fetches the next-segment pool using the updated buffer
5. **Prefix miss** → vector top candidates are re-ranked with RapidFuzz
6. **Word-by-word typo recovery** (e.g. `dokcer`, `nxp --help`) runs before LLM fallback
7. **Typo hit** → first suggestion is `Did you mean: <corrected words up to current word>`
8. **No vector suggestions and no typo recovery** → AI fallback is used automatically
9. **Press `Ctrl+Space`** → explicitly request a manual suggestion fetch (within per-line LLM budget)

### Example Flow

```bash
$ dock█
# pause ~200ms
# suggestions appear from the current pool: docker ps, docker stop, docker logs
$ docker st█
# pause ~200ms
# pool is filtered locally: docker start, docker stop, docker stats
$ docker start █container-id
```

```bash
$ qztool █
# Space triggers segment fetch for buffer "qztool" (no vector hits)
# AI fallback returns a suggestion for this unrecognized command
$ qztool --help█

# Need AI immediately for the current buffer?
$ qztool --ver█
# press Ctrl+Space to request a manual suggestion fetch
```

```bash
$ dokcer █
# first suggestion: Did you mean: docker

$ nxp --help█
# first suggestion: Did you mean: npx --help
```

## Installation

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

This will install:
- `zvec` - Vector database
- `sentence-transformers` - For embeddings
- `torch` - ML framework
- `rapidfuzz` - Fast typo + command similarity reranking
- `fastapi`, `uvicorn` - Server
- `litellm` - Multi-provider AI support

### 2. Start the Server

```bash
python server.py
```

The server runs on `http://127.0.0.1:22000`

All local API routes require auth via one of:
- `Authorization: Bearer <auth_token>`
- `X-GhostShell-Auth: <auth_token>`

The token is stored at `~/.ghostshell/auth.json` and is created automatically on daemon startup.
Token rotation happens automatically on:
- daemon startup (including reboot/startup LaunchAgent boot)
- `aiterminal start`
- `aiterminal setup`

Authenticated curl example:

```bash
TOKEN="$(python3 -c 'import json, pathlib; p=pathlib.Path.home()/".ghostshell"/"auth.json"; print(json.loads(p.read_text()).get("auth_token",""))')"
curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:22000/status
```

### Project Structure

Runtime entrypoints remain at the project root:

- `cli.py`
- `server.py`

Implementation code is organized under:

- `ghostshell/cli/`
- `ghostshell/server/`
- `ghostshell/engine/`
- `ghostshell/vector_db/`
- `ghostshell/privacy/`
- `ghostshell/utils/`

### 3. Load the Shell Plugin

Add to your `~/.zshrc`:

```bash
source /path/to/ghostshell.zsh
```

Then reload:

```bash
source ~/.zshrc
```

### Signed Execution Sessions

For deterministic `AI_EXECUTED` provenance in active shells, prefer the native session helpers:

```bash
ghostshell_session_start --agent codex --model gpt-5.3 --agent-name "Planner A" --ttl-minutes 120
# run commands
ghostshell_session_status
ghostshell_session_stop
```

One-off signed execution stays on `ai-exec`:

```bash
aiterminal ai-exec --agent codex --model gpt-5.3 -- echo ok
```

If agent/model are omitted, GhostShell defaults to `agent=unknown` and `model=unknown-model` and prints a warning.

### Provenance TUI

Open the full-screen provenance interface:

```bash
aiterminal provenance --tui
```

Inside the TUI filter panel (`f`), `time` now supports:
- `last_7d` (default)
- `last_30d`
- `custom` (start/end `YYYY-MM-DD`, within last 365 days, max 30 days inclusive)

Footer behavior:
- line 1 always shows key shortcuts/command bar
- line 2 always shows current status/error text (including semantic fallback errors)

Export the current filtered dataset in one shot:

```bash
aiterminal provenance --tui --export json --out ./provenance.json
aiterminal provenance --tui --export csv --out ./provenance.csv
```

Without `--out`, exports default to `~/Downloads/provenance_export_<timestamp>.<ext>`.

Local development before publishing releases:

```bash
cargo build --manifest-path rust/provenance_tui/Cargo.toml --release
aiterminal provenance --tui
```

Optional overrides:
- `GHOSTSHELL_PROVENANCE_TUI_MANIFEST_URL` to point to a custom manifest URL
- `GHOSTSHELL_PROVENANCE_TUI_LOCAL_BIN` to force a specific local sidecar binary

Release publish helper (build + manifest + GitHub upload):

```bash
./scripts/publish_provenance_tui_release.sh <tag>
```

Environment override:
- `GITHUB_REPO` defaults to `Alex188dot/ai-terminal`

### 4. First Run Initialization

The first time you use GhostShell, it will:
- Create a vector database at `~/.ghostshell/zvec_commands`
- Load all commands from your `.zsh_history`
- Generate embeddings (this may take a few seconds)
- Create an HNSW index for fast search

## Configuration

Create `~/.ghostshell/config.json`:

```json
{
  "provider": "openai",
  "model": "gpt-4o-mini",
  "api_key": "your-api-key-here",
  "llm_calls_per_line": 4,
  "llm_budget_unlimited": false,
  "disabled_command_patterns": ["ros2", "kubectl"]
}
```

`disabled_command_patterns` is optional. When set, GhostShell is fully suppressed for matching command families (including in-progress prefixes like `ro` for `ros2`).
`llm_calls_per_line` is optional and must be `0-99`. `0` disables LLM calls on that line; any number `> 0` sets the max LLM calls per command line (auto + manual `Ctrl+Space`).
`llm_budget_unlimited` is optional. When `true`, GhostShell ignores `llm_calls_per_line` for that line.

### Setup Menu

Run:

```bash
aiterminal setup
```

Local auth utilities:

```bash
aiterminal auth status
aiterminal auth rotate
```

Diagnostics:

```bash
aiterminal doctor
```

First screen:
- `Choose AI provider`
- `Customize LLM budget`
- `Manage GhostShell command patterns`
- `Manage command store (add/remove commands)`

Navigation:
- `Esc` goes back to the previous setup screen.
- Every setup prompt shows an orange `Esc = back` instruction.

Pattern controls:
- `Disable GhostShell for a specific pattern`
- `Re-enable GhostShell for a specific pattern`

Command store:
- `Add commands` (comma-separated input; accidental spaces are trimmed)
- `Remove commands` (multi-select with arrow keys + `Space`, then `Enter` to confirm)
- Remove screen categories:
  - `Potential wrong commands` (conservative typo-like low-usage variants)
  - `Commands` (all other commands)
- Deletion is exact-command only and removes selected entries from both shell history and vector store.
- Removed commands are tracked to prevent automatic re-ingestion until manually re-added.

LM Studio example:

```json
{
  "provider": "lm_studio",
  "model": "qwen3-8b",
  "base_url": "http://localhost:1234/v1"
}
```

Custom OpenAI-compatible endpoint example:

```json
{
  "provider": "custom",
  "model": "openai/my-model",
  "api_key": "your-api-key-here",
  "base_url": "https://your-endpoint.example.com/v1",
  "headers": {
    "X-Custom-Header": "value"
  },
  "timeout": 30,
  "api_version": "2024-10-01-preview",
  "extra_body": {
    "reasoning_effort": "medium"
  }
}
```

### Supported Providers

- **OpenAI**
- **Groq**
- **Anthropic**
- **Ollama**
- **LM Studio**
- **Custom (OpenAI-compatible endpoint)**
- **Gemini**
- **DashScope (Qwen)**: use `model=dashscope/<model>`
- **MiniMax**: use `model=minimax/<model>`
- **DeepSeek**: use `model=deepseek/<model>`
- **Moonshot AI**: use `model=moonshot/<model>`
- **Mistral**: use `model=mistral/<model>`
- **OpenRouter**: use `model=openrouter/<model>`
- **Xiaomi MiMo**: use `model=xiaomi_mimo/<model>`
- **Z.AI (Zhipu AI)**: use `model=zai/<model>`
- **AWS SageMaker**: use `model=sagemaker/<endpoint-or-model>`
- **Use without AI (history only)**: setup option `use without AI (will just use your history)`

Provider notes:
- For DashScope, MiniMax, Moonshot, and OpenRouter, setup pre-fills a default base URL (editable).
- AWS SageMaker uses AWS credentials from env:
  - `AWS_ACCESS_KEY_ID`
  - `AWS_SECRET_ACCESS_KEY`
  - `AWS_REGION_NAME`
- Qwen on Alibaba Cloud is supported through DashScope with `dashscope/` model prefix.
- All providers above are prefix-based: any provider model is accepted if it uses the correct prefix.

### Reasoning Controls

- **Ollama**: GhostShell sets `think: false` on requests.
- **Ollama**: Thinking traces (`<think>...</think>`) are stripped before parsing/displaying output.
- **Ollama**: API key is optional in `aiterminal setup`.
- **LM Studio**: GhostShell uses LM Studio REST (`/api/v1/chat`) with `reasoning: "off"`.

## Usage

### Basic Usage

Just start typing and press `Space` after each command segment:

```bash
$ git█
$ git 
$ git status█  # ← ghost text appears
```

### Natural-language modes: `#` and `##`

GhostShell supports two explicit NL modes:

- `# ...` command mode (guardrailed):
  - Use this when you want a terminal command from natural language.
  - The model is instructed to answer only terminal/CLI requests.
  - GhostShell sends environment context (`os_name`, `os_version`, `shell`, `terminal`, `cwd`).
  - On `Enter`: GhostShell inserts the generated command into your prompt and does **not** execute it.
  - On `Tab`: GhostShell resolves and inserts the command (same non-executing behavior).
  - Output includes a short explanation, optional alternatives, and a copy-ready plain block.

- `## ...` assistant mode (free text):
  - System prompt is exactly: `You are a helpful assistant.`
  - Use this for any general question, not just terminal topics.
  - On `Enter`: GhostShell prints the assistant text reply and does **not** execute any command.
  - `Tab` keeps default terminal completion behavior.

When provider is set to `use without AI (will just use your history)`:
- LLM fallback is disabled for autocomplete.
- `#` and `##` modes are disabled and return an AI-disabled message.

Examples:

```bash
# tell me how to start a container in docker
```

GhostShell inserts something like:

```bash
docker run -d --name myapp -p 8080:80 nginx:latest
```

```bash
## explain recursion simply
```

GhostShell prints a normal text explanation in the terminal.

### Keyboard Shortcuts

- **Tab** - Accept current suggestion
- **Ctrl+P** - Previous suggestion
- **Ctrl+N** - Next suggestion  
- **Ctrl+Space** - Manual trigger (still limited by per-line LLM budget)
- **Esc** - Clear visible ghost suggestion and keep your typed buffer
- **Option+→** - Accept first word only
- **Enter** - Execute command (logs to vector DB)

### How Suggestions Work

1. **Vector DB First**: Searches your history for similar commands
2. **Space Triggering**: Auto requests only on `Space`
3. **Real-time Filtering**: As you type, filters the 20 suggestions
4. **Typo Recovery**: Word-by-word typo checks run before LLM fallback and return `Did you mean: ...`
5. **AI Fallback**: Only invoked when vectors return no suggestions and no typo correction is found
6. **Per-Command Budget**: Configurable in `aiterminal setup` (`Customize LLM budget`): `0-99` calls per command line, or explicit `No budget limit`

When typo recovery is active, the first ghost suggestion is shown as:

```text
Did you mean: docker
Did you mean: npx --help
```

In native-completion contexts, GhostShell suppresses automatic fetches and does not steal `Tab`:
- path-heavy commands (`cd`, `ls`, `cat`, `vim`, etc.)
- script/file argument contexts (`python script.py`, `node app.js`, `bash foo.sh`)
- tokens that look like paths/files (`/`, `./`, `../`, `~`, or extension-like tokens such as `.py`, `.sh`, `.json`)

In those contexts:
- automatic fetches (`space_auto`, `pause_timer`) are skipped
- `Tab` always runs native completion (`expand-or-complete`)
- `Ctrl+Space` is an explicit manual fetch (still subject to configured per-line LLM budget)

Semantic intent examples (same executable scope):

```text
aiterminal records  ->  aiterminal logs
aiterminal halt     ->  aiterminal stop
```

This means:
- ✅ Most suggestions are instant (from vector DB)
- ✅ No repeated API calls while typing
- ✅ AI only for truly new commands

## Architecture

```
┌─────────────────┐
│  ghostshell.zsh │  ← Shell plugin (space-trigger, filtering)
└────────┬────────┘
         │ HTTP
         ▼
┌─────────────────┐
│   server.py     │  ← FastAPI server
└────────┬────────┘
         │
    ┌────┴────┐
    ▼         ▼
┌─────────┐ ┌──────────┐
│engine.py│ │vector_db │  ← Vector database (zvec)
└─────────┘ └──────────┘
    │
    ▼
┌─────────┐
│   AI    │  ← LiteLLM (multi-provider)
└─────────┘
```

### Key Components

- **vector_db.py**: Manages zvec database, embeddings, search
- **engine.py**: Suggestion logic, AI integration
- **server.py**: HTTP API endpoints
- **privacy_guard.py**: Sanitization and fail-closed privacy checks before LLM egress
- **ghostshell.zsh**: Shell integration, UI, keyboard handling
- **shell_client.py**: Shell helper for `/predict`, `/intent`, and `/assist`

### Shell Helper Contract

`shell_client.py` supports:

- `--op predict|intent|assist` (default `predict`)
- `--format json|shell_lines_v1` (default `json`)
- shared context args: `--working-directory`, `--shell`, `--terminal`, `--platform`
- mode-specific args: `--intent-text` (`intent`), `--prompt-text` (`assist`)
- input precedence: CLI args first, stdin JSON fallback for missing values

Compatibility behavior:

- Existing `predict` stdin JSON input and JSON output remain the default.
- `intent` and `assist` can use `shell_lines_v1` for shell-safe parsing without `eval`.

`shell_lines_v1` layout:

1. line 1: `ghostshell_shell_lines_v1`
2. line 2: operation (`intent` or `assist`)
3. line 3: helper `ok` (`1` or `0`)
4. line 4: `error_code` (empty when `ok=1`)

`intent` payload lines:

1. line 5: `status`
2. line 6: `primary_command`
3. line 7: `explanation`
4. line 8: `alternatives_blob` (`|||` separator, max 2 alternatives)
5. line 9: `copy_block`
6. line 10: `ai_agent`
7. line 11: `ai_provider`
8. line 12: `ai_model`

`assist` payload lines:

1. line 5: `answer_line_count` (integer)
2. line 6..N: raw `answer` lines (multiline markdown preserved)

`intent` fields are single-line sanitized values (`\r`/`\n` replaced by spaces, trimmed). `assist` preserves multiline markdown by emitting a line count plus raw lines. This transport is intended to be reused by future bash/Linux and PowerShell adapters.

## Technical Details

### Vector Database

- **Storage**: `~/.ghostshell/zvec_commands`
- **Model**: all-MiniLM-L6-v2 (384 dimensions)
- **Index**: HNSW (m=16, ef_construction=200)
- **Deduplication**: Deterministic command IDs (SHA-256)
- **Max query**: 1024 results (zvec limitation)

### Triggering Model

- **Auto trigger**: `Space` key only
- **First AI fallback**: Requires at least one typed `Space`
- **LLM budget**: Configurable per command line (`llm_calls_per_line`, default `4`)
- **Budget semantics**: `0-99` for explicit budget, or `llm_budget_unlimited=true` for no budget limit
- **Overflow hint**: `LLM budget reached for this command line`

### LLM Safeguards

- **Rate limiting**: per-client LLM request cap over a 60-second window (`llm_requests_per_minute`, default `120`)
- **Timeout cap**: outbound LLM timeout is bounded (`timeout`, clamped to `1-30s`, default `20s`)
- **Input size validation**: server-side max lengths for prompt/intent/command payloads

### Suggestion Pool

- **Size**: Up to 20 suggestions from vector DB
- **Filtering**: Client-side (zsh) as you type
- **Refresh**: Only when pool is exhausted

### Privacy Boundary (LLM + Logs)

- **Hard boundary before LLM**: every outbound `messages` payload is sanitized in `engine.py` via `privacy_guard.py`
- **Redaction scope**: env assignments, known secret names (`*_KEY`, `*_TOKEN`, `AWS_*`, provider keys), high-entropy tokens (JWT/long hex/base64-like), dotenv lines, URL credentials
- **Value policy**: raw secret values are replaced with placeholders like `<REDACTED_SECRET>`
- **Bounded history to LLM**: command history is capped (last 12 lines + length limits), sanitized, and never sent as full history
- **Fail-closed behavior**: if sanitization/check fails, the LLM call is blocked and GhostShell returns a safe fallback
- **Log safety**: request buffer and error text are sanitized before logging, and logs include redaction metadata (`redactions=N`)


## Performance

- **Vector DB Query**: ~10-50ms
- **AI Query**: ~500-2000ms (only for new commands)
- **Filtering**: <1ms (local, in-memory)

## Troubleshooting

### Server won't start

```bash
# Check if port 22000 is in use
lsof -i :22000

# Kill existing process
kill -9 <PID>
```

### No suggestions appearing

1. Check server is running with auth:
   `TOKEN="$(python3 -c 'import json, pathlib; p=pathlib.Path.home()/".ghostshell"/"auth.json"; print(json.loads(p.read_text()).get("auth_token",""))')"; curl -H "Authorization: Bearer $TOKEN" http://127.0.0.1:22000/status`
2. Check logs: Server prints to stdout
3. Verify config: `cat ~/.ghostshell/config.json`

### 401 unauthorized from localhost API

1. Run `aiterminal setup` to rotate/regenerate `~/.ghostshell/auth.json`
2. Reload shell config (for `ghostshell.zsh`): `source ~/.zshrc`
3. Retry the command

### Vector DB initialization slow

First run loads your entire `.zsh_history` and generates embeddings. This is normal and only happens once. Subsequent runs are instant.

### "Too many docs" error

The vector DB batches inserts in groups of 100. If you see this error, your history file may be very large. The system will handle it automatically.

## Development

### Run Tests

```bash
python3 -m unittest test_learning_migration.py
```



## License

Apache-2.0

## Credits

Built with:
- [zvec](https://github.com/zvecr/zvec) - Vector database
- [sentence-transformers](https://www.sbert.net/) - Embeddings
- [LiteLLM](https://github.com/BerriAI/litellm) - Multi-provider AI
- [FastAPI](https://fastapi.tiangolo.com/) - Web framework

---

**GhostShell** - Your terminal's new best friend 👻✨
