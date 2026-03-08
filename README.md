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
```

Natural-language flows:

```bash
# how do I merge branch feature/login into main?
## explain how to set up a local k3d cluster for testing a Kubernetes app
```

## For Agents

Agensic is not just an AI terminal UX layer. It is also a forensic layer for agent execution.

The repo publishes a `terminal-commands` skill for agentic tools that need deterministic provenance for shell activity. When an agent uses the skill, commands are signed so they can be registered and later verified as AI-executed actions.

How the proof works, briefly:

- Each AI-executed command is signed locally with an Ed25519 keypair stored under `~/.agensic`.
- The daemon verifies that signature before assigning the `AI_EXECUTED` label.
- Incomplete, malformed, stale, or invalid proofs are recorded as `INVALID_PROOF`, not silently upgraded to success.
- The provenance TUI and CLI read those stored records from SQLite so you can review them later.

You can install this skill from GitHub, in one of the following ways:

```bash
npx skills add Alex188dot/ai-terminal

npx ctx7 skills install Alex188dot/ai-terminal
```

Repo: [github.com/Alex188dot/ai-terminal](https://github.com/Alex188dot/ai-terminal)

That means you can answer questions like:

- What command ran?
- Which agent produced it?
- Which model was used?
- Was it signed and verified correctly?
- What was the exit code and the error message?

Preferred one-off flow:

```bash
agensic ai-exec --agent <agent_id> --model <model_id> --agent-name '<agent_name>' -- <command...>
```

Preferred session flow:

```bash
agensic session start --agent <agent_id> --model <model_id> --agent-name '<agent_name>' --ttl-minutes 120
# run commands normally while the session is active
agensic session stop
```

Verification:

```bash
agensic provenance --contains "<token>" --limit 5
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

The installer writes a real launcher to `~/.agensic/bin/agensic` and adds that directory to your shell `PATH` in a managed shell RC block.

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
