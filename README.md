# Agensic

**Forensic Observability for AI Agents**

**Make your terminal smarter without replacing it.**

Agensic records, signs, and verifies AI-generated terminal commands so developers can trust what ran, who produced it, and why.

It is built for people who like their existing shell and want better intelligence, better auditability, and better control, not a brand new terminal to learn.

## Why Agensic

AI in the terminal is useful right up until trust becomes the problem.

Autocomplete is easy to demo. Auditability is what matters in production.

Agensic brings both together:

- For humans, it makes your terminal faster with autocomplete, AI command generation, assistant flows and a provenance TUI.
- For agents, it provides signed command execution so actions are attributable, queryable, and verifiable later.

## For Humans

Agensic helps developers who want to keep their terminal and make it more capable.

- Inline command autocomplete while you type
- `#` mode for quick command generation from natural language
- `##` mode for free-text help in the terminal
- Provenance TUI for reviewing what happened
- Local-first workflow with optional AI assistance when needed

Examples:

```bash
agensic setup
agensic provenance --tui
agensic --explain "tar -czf backup.tgz src"
```

Natural-language flows:

```bash
# how do I merge branch feature/login into main?
## explain how to set up a local k3d cluster for testing a Kubernetes app
```

## What Makes It Different

- It improves your existing terminal instead of replacing it.
- It treats provenance as a product feature, not an afterthought.
- It serves both interactive developers and autonomous agents.
- It keeps a practical local footprint with SQLite instead of turning your shell into a heavy platform dependency. 🗃️

## Install And Run

```bash
bash ./install.sh
```

Open a new terminal and run:

```bash
agensic setup
```

The installer writes a real launcher to `~/.local/bin/agensic` by default, installs the Python package into `$XDG_STATE_HOME/agensic/install/.venv`, and uses XDG config/state/cache directories on macOS and Linux.

When `uv` is available, the installer prefers it for faster environment creation and package installation. Otherwise it falls back to stdlib `venv` + `pip`.

If you only want an isolated CLI install, you can also use standard Python tooling:

```bash
uv tool install .
# or
pipx install .
```

That avoids `pip install` into the host interpreter. Use `bash ./install.sh` when you also want the managed Zsh shell wiring from this repo.

Once the daemon is running, you can use the `agensic` CLI to interact with it or use the shell integration to use it inline autocomplete in your existing shell.

## Other Commands

Check local auth status:

```bash
agensic auth status
agensic auth rotate
```

Run diagnostics:

```bash
agensic doctor
```

Open the provenance interface:

```bash
agensic provenance --tui
```

Export provenance:

```bash
agensic provenance --tui --export json --out ./provenance.json
agensic provenance --tui --export csv --out ./provenance.csv
```

## Built For

Agensic is for developers who want:

- A smarter terminal without changing how they work
- AI help without surrendering visibility
- Agent execution with evidence, attribution, and replayable history

If your shell is becoming a workspace for both humans and agents, Agensic is the audit trail.

## License

Apache-2.0
