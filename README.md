<p align="center">
  <img src="./assets/agensic-logo.png" alt="Agensic logo" width="100%" />
</p>

<h2 align="center">Know what ran, where, when and who executed it</h2>

<h3 align="center">The missing observability layer for AI-powered terminal workflows</h3>

<p align="center">
  <code>Signed Provenance</code>
  <code>Tracked Sessions</code>
  <code>Git Time Travel</code>
  <code>Replayable Sessions</code>
  <code>IDE style Tab Autocomplete</code>
</p>

---

Agensic upgrades your existing terminal workflow for the AI era. It is built for developers who want the productivity boost of AI agents right in their shell, but refuse to compromise on **auditability, control and privacy**. 

As AI agents increasingly take over the command line and execute tasks autonomously, visibility and control become critical.
Agensic treats **terminal commands as first-class citizens** and provides a robust framework for tracking, inspecting and managing them.

Instead of flattening all terminal activity into a single ambiguous "shell history", Agensic acts as a forensic observer and a smart co-pilot. It seamlessly tracks interactive AI coding sessions, cryptographically signs agent-executed commands and provides blazing-fast, history-backed IDE-style Tab autocomplete.

---

## 📋 Table of Contents

1. [Platform Support](#-platform-support)
2. [Installation](#-installation)
3. [Quick Start](#-quick-start)
4. [Agensic Provenance](#-agensic-provenance)
   - [Features](#-features-provenance)
   - [Supported Labels](#-supported-labels)
5. [Agensic Sessions](#-agensic-sessions)
   - [Features](#-features-sessions)
   - [Supported Agents](#-supported-agents)
6. [Agensic Autocomplete](#-agensic-autocomplete)
   - [Features](#-features-autocomplete)
   - [Supported Providers](#-supported-providers)
7. [Safety & Privacy](#-safety--privacy)
8. [Project Creator](#-project-creator)
9. [Support](#-support)
10. [License](#-license)

---

## 💻 <a id="-platform-support"></a>Platform Support

Agensic is designed to integrate deeply with your shell environment.

- macOS (Zsh) — fully supported ✅

- Linux (Bash) — fully supported ✅

- Windows (PowerShell) — coming soon 🕐 (estimated end of April 2026)

---

## 🚀 <a id="-installation"></a>Installation

The fastest path is using the managed installer:

```bash
bash ./install.sh
```

The installer defaults to a CPU-only PyTorch wheel to avoid large CUDA downloads on machines that do not need GPU inference. If you already have `uv`, the installer will use it automatically for faster setup.

On first run, Agensic will prompt you to choose your preferred LLM provider and configure your API keys for command autocomplete.

Alternatively, you can configure your LLM provider and API keys by running:

```bash
agensic setup
```

## ⚡ <a id="-quick-start"></a>Quick Start

After setup, **open a new terminal** and Agensic will automatically start tracking your terminal activity and provide autocomplete suggestions as you type!

### 🤖 Seamless Agent Auto-Tracking

Start working, as you normally would, for example by using your favorite AI coding assistant, like Claude Code, Codex CLI, etc:

```bash
claude
```

or

```bash
codex
```

See the [Supported Agents](#-supported-agents) section for a complete list of all supported agents. If your agent is not in the list, you can add it by running <code>agensic --add_agent "executable"</code> and then use it normally so that Agensic can track it. 

Please note: if you are resuming a previous conversation with your agent, you will need to use the manual command line invocation, for example <code>agensic run codex resume your_convo_id</code>.

After you're done, you can use Agensic to inspect the session history:

```bash
agensic sessions
```

or view the forensic provenance:

```bash
agensic provenance
```

---

## 🧾 <a id="-agensic-provenance"></a>Agensic Provenance
Agensic gives you a forensic timeline for every command that matters. It classifies what happened, records rich metadata and surfaces a clear audit trail so you can tell whether a command was manually typed, accepted from a suggestion or executed directly by an agent.

### <a id="-features-provenance"></a>✨ Features

### 🔍 Full-Screen Provenance Explorer
<p>
Need to answer, "What exactly ran here and who triggered it?" Run <code>agensic provenance</code> to open the full-screen provenance viewer. Filter by label, agent, provider or time window, inspect the exact command trail and export the current view to JSON or CSV when you need a durable record for debugging, incident review or compliance.
</p>



### 🛡️ Cryptographic Command Provenance & Signing
<p>
Stop guessing whether a human or an AI broke the build. Agensic captures rich metadata for every command and uses local Ed25519 signing to tag agent-executed runs with an undeniable <code>AI_EXECUTED</code> label. You get a clear, auditable timeline separating human keystrokes from AI suggestions and automated executions.
</p>



### 🏷️ Structured Attribution Labels
<p>
Agensic does not reduce terminal history to a raw list of strings. Every tracked command is classified into a provenance label, giving you a consistent way to filter runs, investigate behavior and understand how a command entered your shell in the first place.
</p>

### <a id="-supported-labels"></a>🔖 Supported Labels

Agensic currently supports the following provenance labels:

- <code>AI_EXECUTED</code> for commands executed directly by an AI agent with signed proof
- <code>HUMAN_TYPED</code> for commands manually typed by a human in the shell
- <code>AI_SUGGESTED_HUMAN_RAN</code> for commands suggested by an external AI LLM call and then run by a human
- <code>AG_SUGGESTED_HUMAN_RAN</code> for commands suggested by Agensic and then run by a human
- <code>INVALID_PROOF</code> for commands that arrived with proof metadata that failed validation
- <code>UNKNOWN</code> for commands where the available evidence is insufficient to assign a stronger attribution label

---

## 🕵️‍♂️ <a id="-agensic-sessions"></a>Agensic Sessions
Agensic records command provenance and tracks interactive agent sessions, giving you undeniable proof of what happened and the ability to safely manipulate repo state, via the Time Travel feature.

### <a id="-features-sessions"></a>✨ Features

### ▶️ Replayable Sessions
<p>
Need to audit last night's session, to understand what happened? Run <code>agensic sessions</code> and select the session you want to replay. Our blazing-fast, full-screen TUI lets you instantly browse timelines, inspect payloads and replay the session. When you need to share evidence for an incident review, export the exact dataset to JSON or CSV in one keystroke.
</p>



### ⏪ Time Travel
<p>
Ever wonder, "What did my repo look like exactly before the agent made that destructive commit?" Time Travel lets you rewind your repository to the exact Git state captured at a specific session checkpoint. Agensic safely restores untracked and modified files into a brand new branch, ensuring you never accidentally destroy your live working tree while investigating.
</p>

### 🗄️ Resilient Local-First State
<p>
Your data stays yours. Agensic uses a robust SQLite state backend with an append-only event journal and automated snapshotting. This means your forensic history survives unexpected terminal crashes, system reboots and long-lived local usage without corruption.
</p>

### <a id="-supported-agents"></a>🤖 Supported Agents

Agensic provides support for a set of pre-configured built-in agents that can be tracked:

- Claude Code
- Codex CLI
- Gemini CLI
- OpenClaw
- OpenCode
- Kimi Code
- Mini Agent
- Qwen Code
- GitHub Copilot CLI
- Kiro CLI
- Cline CLI
- Cursor CLI
- Nanoclaw
- Droid
- Hermes Agent
- Ollama
- Mistral Vibe
- Aider
- Pi.dev
- Kilo Code
- Continue CLI
- Custom Agents

---

## ⚡ <a id="-agensic-autocomplete"></a>Agensic Autocomplete
Agensic reimagines terminal suggestions. Context-aware, semantic and always fast IDE-style, satisfying Tab autocomplete.

### <a id="-features-autocomplete"></a>✨ Features

### 🚀 Blazing Fast Local History Suggestions
<p>
Say goodbye to input lag. As you type, Agensic queries a store of your actual command history. Suggestions appear instantly as ghost text, that you can easily accept with <code>Tab</code>. Because it learns from your patterns and relies on what you actually do, the suggestions are highly accurate and repo-aware.
</p>



### 🧠 Semantic Search & Typo Recovery
<p>
Humans make mistakes, Agensic fixes them. If you type <code>dokcer</code>, Agensic instantly suggests <code>Did you mean: docker</code>. Can't remember the exact syntax? Semantic reranking rescues your intent, finding the right command even if your prefix doesn't match perfectly: if you type <code>docker records</code> you will get <code>Did you mean: docker logs</code> (provided you have run <code>docker logs</code> in the past)
</p>



### 💡 On-Demand AI Fallback
<p>
AI is a powerful fallback, not a tax on every keystroke. Agensic enforces per-line LLM budgets to prevent prompt spam. When local history isn't enough, automatic LLM fallback will be triggered. Alternatively, you can always hit <code>Ctrl+Space</code> to manually fetch an intelligent suggestion from your preferred local or hosted LLM.
</p>



### 💬 Natural Language Command Modes
<p>
Forget exact syntax, just describe what you want. Type something like: <code># how can I find all files larger than 10MB in this folder?</code>. Agensic generates 3 copy-ready shell commands and inserts the most likely option directly into your next line. Need guidance instead of commands? Use the assistant mode to get step-by-step answers right in your terminal, <code>## How do I install Node on macOS?</code>. Want to understand a command before running it? Use: <code>agensic --explain "your command here"</code>
</p>



### 🛡️ Native Tab Preservation & Risk Blocking
<p>
Agensic respects your terminal. It leaves <code>Tab</code> alone when you are completing file paths or scripts, so native shell completion still wins where it's best. More importantly, destructive commands (like <code>rm -rf</code>, <code>mkfs</code>, or <code>dd</code>) are strictly blocked from suggestion pools so you never accidentally execute a disaster.
</p>

### <a id="-supported-providers"></a>🌐 Supported Providers

Agensic fits into your stack, whether you run models locally or use hosted endpoints.

*   **Local:** Ollama, LM Studio
*   **Hosted:** OpenAI, Anthropic, Gemini, Azure, DeepSeek, Groq, Mistral, Qwen (DashScope), MiniMax, Moonshot, OpenRouter, Xiaomi MiMo, Z.AI, AWS SageMaker
*   **Custom:** any OpenAI-compatible endpoint
*   **History Only:** run entirely offline without AI calls, using only your local command history

---

## 🔒 <a id="-safety--privacy"></a>Safety & Privacy

Agensic operates on the principle of least privilege and maximum privacy:

*   **Local Auth:** every localhost API route is strictly authenticated with automatic token rotation
*   **Secret Redaction:** high-entropy values, URL credentials and known secret formats (like AWS keys or JWTs) are stripped and redacted **before** any data leaves your machine for an LLM call
*   **Command Guardrails:** destructive commands are hard-blocked from both the suggestion engine and the feedback loop
*   **Rate Limiting:** outbound LLM calls are bounded by strict timeouts and budgets to prevent API abuse

---

## <a id="-project-creator"></a>👤 Project Creator

Alessio Leodori

## <a id="-support"></a>🤝 Support

Find Agensic useful? Star ⭐️ the repo and tell a friend! This will help us grow 🌱

## 📄 <a id="-license"></a>License

Agensic is open-source and licensed under the **Apache-2.0 License**.
