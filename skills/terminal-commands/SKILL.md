---
name: terminal-commands
description: ALWAYS use when the agent is about to run shell commands so command provenance is auditable as AI_EXECUTED, including for unmapped or custom agent IDs.
license: Apache-2.0
metadata:
  author: Alessio Leodori
  version: "1.0"
---

# Terminal Commands

## Overview
Use GhostShell signing whenever terminal commands need deterministic provenance.
Prefer session signing for multi-command work.

## Mandatory Wrapper Policy
1. Always attempt signed execution before running any shell command.
2. Preferred path for multi-command work: `scripts/signed_session.sh` or native `ghostshell_session_*`.
3. Preferred path for one-off work: `scripts/signed_exec.sh` or `aiterminal ai-exec`.
4. Never claim a command was signed unless signing actually succeeded.
5. For command batches, run `./scripts/enforce_signed_batch.sh --mode strict` before execution to reject raw commands outside wrapper/session flow.

## Identity Rules
1. Normalize `agent` to lowercase.
2. Preserve model as the raw runtime string when available.
3. Use `unknown-model` if model identity is unavailable.
4. Use `unknown` if agent identity is unavailable.
5. Allow unmapped/custom agent IDs; do not coerce to a registry ID.
6. `agent_name` is optional and informational.

## Preferred Flow (Multi-Command)
1. Start a signed session in the active shell:

```bash
ghostshell_session_start --agent <agent_id_lower_or_unknown> --model <model_raw_or_unknown-model> --agent-name '<optional name>' --ttl-minutes 120
```

2. Run commands normally while the session is active.
3. Stop the session when done:

```bash
ghostshell_session_stop
```

4. Check current state when needed:

```bash
ghostshell_session_status
```

Session expiry is actively enforced by a timer signal, so stale sessions are cleared even if no command is executed after timeout.

For convenience wrappers, use:

```bash
./scripts/signed_session.sh start [--agent <agent_id>] [--model <model_id>] [--agent-name '<name>']
./scripts/signed_session.sh status
./scripts/signed_session.sh stop
```

Export-based session flow remains available for compatibility, but is deprecated:

```bash
eval "$(aiterminal ai-session start --agent <agent_id_lower_or_unknown> --model <model_raw_or_unknown-model> --agent-name '<optional name>')"
# run commands
eval "$(aiterminal ai-session stop)"
```

## Fallback Flow (One-Off Command)
For single commands, use `ai-exec` directly:

```bash
aiterminal ai-exec --agent <agent_id_lower_or_unknown> --model <model_raw_or_unknown-model> --agent-name '<optional name>' -- <argv...>
```

For operator-heavy commands (pipes, redirects, `&&`, `||`, subshells), keep command string mode explicit:

```bash
aiterminal ai-exec --agent <agent_id_lower_or_unknown> --model <model_raw_or_unknown-model> --agent-name '<optional name>' -- zsh -lc '<original command string>'
```

Or use:

```bash
./scripts/signed_exec.sh [--agent <agent_id>] [--model <model_id>] [--agent-name '<name>'] -- <command...>
./scripts/signed_exec.sh [--agent <agent_id>] [--model <model_id>] [--agent-name '<name>'] --command '<command string>'
```

Missing identity defaults to `agent=unknown` and `model=unknown-model` with a warning.
Signed wrapper execution also performs a low-latency local provenance check by default.
Use `--verify-mode strict` to fail hard when a run cannot be verified as `AI_EXECUTED`.

## Verification
After important executions:

```bash
aiterminal provenance --limit 5
```

Or narrow by command token:

```bash
aiterminal provenance --contains "<token>" --limit 5
```

Signed runs should appear as `AI_EXECUTED`.

## Failure Handling
1. If wrapper/session commands are unavailable, emit a clear warning that provenance is not auditable as `AI_EXECUTED`.
2. Under warn-and-run mode, continue with raw shell execution only when command execution is still required for the task.
3. Never claim a command was signed if signing failed or was unavailable.

## References
1. Identity policy: `references/identity-policy.md`
2. One-off helper: `scripts/signed_exec.sh`
3. Session helper: `scripts/signed_session.sh`
4. Batch guard helper: `scripts/enforce_signed_batch.sh`
