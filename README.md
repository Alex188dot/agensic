# GhostShell 👻✨

An intelligent terminal autocomplete powered by vector databases and AI.

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
6. **Typo recovery** (e.g. `dokcer`) → RapidFuzz can trigger `Maybe you meant: ...`
7. **No vector suggestions at all** → AI fallback is used automatically
8. **Press `Ctrl+Space`** → explicitly request an AI suggestion

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
# press Ctrl+Space to force an AI suggestion
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

### 3. Load the Shell Plugin

Add to your `~/.zshrc`:

```bash
source /path/to/ghostshell.zsh
```

Then reload:

```bash
source ~/.zshrc
```

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
  "disabled_command_patterns": ["ros2", "kubectl"]
}
```

`disabled_command_patterns` is optional. When set, GhostShell is fully suppressed for matching command families (including in-progress prefixes like `ro` for `ros2`).

### Setup Menu

Run:

```bash
aiterminal setup
```

First screen:
- `Manage GhostShell command patterns`
- `Choose AI provider`

Pattern controls:
- `Disable GhostShell for a specific pattern`
- `Re-enable GhostShell for a specific pattern`

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
- **Ctrl+Space** - Manual trigger (bypasses auto LLM budget cap)
- **Esc** - Clear visible ghost suggestion and keep your typed buffer
- **Option+→** - Accept first word only
- **Enter** - Execute command (logs to vector DB)

### How Suggestions Work

1. **Vector DB First**: Searches your history for similar commands
2. **Space Triggering**: Auto requests only on `Space`
3. **Real-time Filtering**: As you type, filters the 20 suggestions
4. **Retrieve & Re-rank**: Prefix miss uses vector top-N recall, then RapidFuzz reranking
5. **Typo Recovery**: RapidFuzz executable similarity enables `Maybe you meant` replacement
6. **AI Fallback**: Only invoked when vectors return no suggestions
7. **Per-Command Budget**: At most 4 automatic AI fallbacks; use `Ctrl+Space` for manual fetch after cap

When typo recovery is active, the first ghost suggestion is shown as:

```text
Maybe you meant:  docker start 7b567d2835e3
```

Pressing `Tab` accepts the full command and replaces the full line, except in native-completion contexts where GhostShell intentionally does not steal `Tab`:
- path-heavy commands (`cd`, `ls`, `cat`, `vim`, etc.)
- script/file argument contexts (`python script.py`, `node app.js`, `bash foo.sh`)
- tokens that look like paths/files (`/`, `./`, `../`, `~`, or extension-like tokens such as `.py`, `.sh`, `.json`)

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
- **Auto AI budget**: Max 4 per command line
- **Overflow hint**: `To trigger new LLM suggestions press ctrl + space`
- **Manual override**: `Ctrl+Space` always fetches

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

1. Check server is running: `curl http://127.0.0.1:22000`
2. Check logs: Server prints to stdout
3. Verify config: `cat ~/.ghostshell/config.json`

### Vector DB initialization slow

First run loads your entire `.zsh_history` and generates embeddings. This is normal and only happens once. Subsequent runs are instant.

### "Too many docs" error

The vector DB batches inserts in groups of 100. If you see this error, your history file may be very large. The system will handle it automatically.

## Development

### Run Tests

```bash
python3 -m unittest test_learning_migration.py
```

### Project Structure

```
ai_terminal2/
├── ghostshell.zsh       # Shell plugin
├── server.py            # HTTP server
├── engine.py            # Suggestion engine
├── vector_db.py         # Vector DB + learning signals
├── cli.py               # CLI tool
├── requirements.txt     # Dependencies
└── test_learning_migration.py  # Tests
```

## Contributing

Contributions welcome! Areas for improvement:

- [ ] Support for bash (currently zsh only)
- [ ] Better history parsing (handle multiline commands)
- [ ] Configurable auto AI call budget
- [ ] Vector DB compression/optimization
- [ ] More AI providers

## License

MIT

## Credits

Built with:
- [zvec](https://github.com/zvecr/zvec) - Vector database
- [sentence-transformers](https://www.sbert.net/) - Embeddings
- [LiteLLM](https://github.com/BerriAI/litellm) - Multi-provider AI
- [FastAPI](https://fastapi.tiangolo.com/) - Web framework

---

**GhostShell** - Your terminal's new best friend 👻✨
