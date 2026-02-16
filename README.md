# GhostShell 👻✨

**The AI Autocomplete for your Terminal.** 🚀
Experience the power of advanced LLMs directly in your shell. GhostShell provides low-latency, context-aware command suggestions as you type, supporting every CLI tool you use: from `git` and `docker` to `npm`, `pip`, and more.

---

## 🌟 Key Features

- **Ghost Text**: Real-time suggestions appear in gray as you type.
- **Multi-Suggestion System**: Provides up to 3 smart completions. Cycle through them to find the perfect fit.
- **Provider Agnostic**: Seamlessly integrate with OpenAI, Anthropic, Google Gemini, Groq, or local models via Ollama and LM Studio.
- **Context Awareness**: Understands your current directory and project structure for pinpoint accuracy.
- **Native Zsh Integration**: Built as a lightweight Zsh plugin for maximum performance.

---

## ⚡ Commands & Shortcuts

### ⌨️ Keyboard Shortcuts
Master GhostShell with these intuitive keys:

| Action | Shortcut | Description |
| :--- | :--- | :--- |
| **Accept Full** | `Tab` | Inserts the entire suggestion at once |
| **Partial Accept** | `Option + Right` | Inserts only the first word of the suggestion |
| **Manual Trigger** | `Ctrl + Space` | Manually request a suggestion from the AI |
| **Cycle Next** | `Ctrl + N` | Switch to the next available suggestion |
| **Cycle Prev** | `Ctrl + P` | Switch to the previous suggestion |

### 🛠 CLI Commands
Manage your GhostShell instance using the `aiterminal` command:

| Command | Usage | Description |
| :--- | :--- | :--- |
| **Setup** | `aiterminal setup` | Interactive wizard to configure your AI provider |
| **Start** | `aiterminal start` | Manually start the background AI daemon |
| **Stop** | `aiterminal stop` | Stop the GhostShell daemon and background services |
| **Logs** | `aiterminal logs` | View real-time server logs for debugging |
| **Test** | `aiterminal test` | Verify connection to the AI daemon and model |
| **Shortcuts**| `aiterminal shortcuts`| Display a list of keyboard shortcuts in the terminal |
| **Boot Sync**| `aiterminal enable_startup` | Enable GhostShell to start automatically on boot (macOS) |

---

## 🚀 Installation

Getting started is easy:

1.  **Clone the repository** and navigate to the directory.
2.  **Run the installer**:
    ```bash
    chmod +x install.sh
    ./install.sh
    ```
3.  **Restart your terminal** or run `source ~/.zshrc`.
4.  **Configure your provider**:
    ```bash
    aiterminal setup
    ```

---

## 🛠 Troubleshooting

If you encounter any issues:
- Check the logs: `aiterminal logs`
- Verify the daemon is running: `aiterminal test`
- Make sure your API keys are correctly set in `aiterminal setup`

---

*Made with ❤️ for developers who love the terminal*