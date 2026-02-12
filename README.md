# GhostShell 👻

AI Autocomplete for your Terminal. Works with ROS2, Git, Docker, and everything else.

## Features
- **Ghost Text**: Suggests commands in gray as you type.
- **Provider Agnostic**: Use OpenAI, Anthropic, or Local LLMs (Ollama) via LiteLLM.
- **Context Aware**: Knows your current directory.

## Installation

1.  Place all files in a folder.
2.  Run:
    ```bash
    chmod +x install.sh
    ./install.sh
    ```
3.  **Restart your terminal.**

## Setup

Run the wizard to configure your AI provider:

```bash
aiterminal setup