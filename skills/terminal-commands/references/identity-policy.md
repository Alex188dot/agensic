# Identity Policy

## Agent ID
1. Normalize agent ID to lowercase before signing.
2. Keep the runtime/tool-provided ID when available.
3. Use `unknown` if agent identity is unavailable.
4. Do not coerce unmapped IDs to known registry agents.
5. Unmapped IDs are valid for deterministic signing.

## Model ID
1. Preserve model as raw runtime string whenever available.
2. Use `unknown-model` if model is unavailable.
3. Do not rewrite model for proof signing.

## Agent Name
1. `agent_name` is optional and user-defined.
2. Use it to distinguish multiple bots/instances of the same agent.
3. It is informational only and not used for signature trust decisions.

## Session-First Signing Contract
1. Prefer `agensic_session_start` for multi-command runs.
2. Commands run while session env is active are auto-signed by Agensic and expected to classify as `AI_EXECUTED`.
3. End with `agensic_session_stop`.
4. Use `agensic_session_status` for checks.
5. Session expiry is actively enforced by timer signal (not only lazily on next command).
6. Use `agensic ai-exec` for one-off commands.
7. `agensic ai-session start/stop` export flow remains available for compatibility, but is deprecated.

## Examples
1. Session flow (mapped identity):
   - `agensic_session_start --agent codex --model gpt-5.3 --agent-name 'Planner A' --ttl-minutes 120`
   - run commands normally
   - `agensic_session_stop`
2. One-off flow (unmapped identity):
   - `agensic ai-exec --agent my-new-agent --model custom-v1 --agent-name 'Ops Bot' -- echo ok`
3. Defaulted identity flow (missing identity):
   - `agensic ai-exec -- echo ok`
   - runtime defaults to `agent=unknown` and `model=unknown-model` (warning emitted)
