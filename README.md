# GhostShell 👻✨

An intelligent terminal autocomplete powered by vector databases and AI.

## What's New: Vector DB Paradigm 2.0

GhostShell now uses a **vector database** (zvec) to store and retrieve your command history with semantic similarity search. This means:

- ⚡ **Lightning fast** suggestions from your history
- 🧠 **Smart matching** using AI embeddings
- 🔄 **No duplicates** - commands are automatically deduplicated
- 📚 **Learns continuously** - every command you run is added to the database
- 💰 **Cost efficient** - AI is only invoked for truly new commands

### How It Works

1. **Type a command** → GhostShell waits 0.2s for you to pause
2. **Pause detected** → Searches vector DB for top 20 matching commands
3. **Shows ghost text** → Displays the best match as you type
4. **Keep typing** → Filters the 20 suggestions in real-time (no API calls!)
5. **No matches?** → Only then does it invoke AI for suggestions

### Example Flow

```bash
$ doc█
# [0.2s pause]
# Vector DB returns 20 docker commands
$ docker ps█  # ← ghost text appears
# You type: "docker st"
# Filters pool to: docker start, docker stop, docker stats
$ docker start █container-id  # ← filtered ghost text
# No API calls after initial fetch!
```

## Features

- 🎯 **Smart Suggestions**: Semantic search finds related commands
- ⌨️ **No Trigger Characters**: Removed space, dot, slash triggers
- ⏱️ **Pause Detection**: 0.2s pause triggers suggestions
- 🎨 **Ghost Text**: Subtle gray text shows completions
- 🔄 **Cycle Options**: Ctrl+P/N to cycle through suggestions
- 📊 **Pool Management**: Keeps 20 suggestions in memory
- 🤖 **AI Fallback**: Uses LLM only for unknown commands

## Installation

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

This will install:
- `zvec` - Vector database
- `sentence-transformers` - For embeddings
- `torch` - ML framework
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

Subsequent runs are instant!

## Configuration

Create `~/.ghostshell/config.json`:

```json
{
  "provider": "openai",
  "model": "gpt-4o-mini",
  "api_key": "your-api-key-here"
}
```

### Supported Providers

- **OpenAI**: `gpt-4o-mini`, `gpt-4o`, etc.
- **Groq**: Fast inference with `llama-3.1-70b-versatile`
- **Anthropic**: `claude-3-5-sonnet-20241022`
- **Ollama**: Local models (set `base_url`)
- **Gemini**: Google's models

## Usage

### Basic Usage

Just start typing! After a 0.2s pause, suggestions appear:

```bash
$ git█
# [pause]
$ git status█  # ← ghost text appears
```

### Keyboard Shortcuts

- **Tab** - Accept current suggestion
- **Ctrl+P** - Previous suggestion
- **Ctrl+N** - Next suggestion  
- **Ctrl+Space** - Manual trigger (force fetch)
- **Option+→** - Accept first word only
- **Enter** - Execute command (logs to vector DB)

### How Suggestions Work

1. **Vector DB First**: Searches your history for similar commands
2. **Real-time Filtering**: As you type, filters the 20 suggestions
3. **AI Fallback**: Only invoked when no history matches exist

This means:
- ✅ Most suggestions are instant (from vector DB)
- ✅ No repeated API calls while typing
- ✅ AI only for truly new commands

## Architecture

```
┌─────────────────┐
│  ghostshell.zsh │  ← Shell plugin (pause detection, filtering)
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
- **ghostshell.zsh**: Shell integration, UI, keyboard handling

## Technical Details

### Vector Database

- **Storage**: `~/.ghostshell/zvec_commands`
- **Model**: all-MiniLM-L6-v2 (384 dimensions)
- **Index**: HNSW (m=16, ef_construction=200)
- **Deduplication**: Deterministic command IDs (SHA-256)
- **Max query**: 1024 results (zvec limitation)

### Pause Detection

- **Method**: Background timer + SIGUSR1 signal
- **Delay**: 0.2 seconds
- **Cancellation**: Timer cancelled on new input

### Suggestion Pool

- **Size**: Up to 20 suggestions from vector DB
- **Filtering**: Client-side (zsh) as you type
- **Refresh**: Only when pool is exhausted

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
- [ ] Configurable pause delay
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

**GhostShell** - Your terminal's new best friend 👻
