# Next Steps

## Policy engine

- Add a central policy engine in front of execution.
- Key policy by agent id, model, workspace, session type, and possibly user-selected profile.
- Match rules on:
  - executable
  - argv patterns
  - cwd
  - environment
  - repo state
  - network targets
- Support decisions:
  - `allow`
  - `deny`
  - `ask`
  - `allow_with_logging`
- Evaluate policy before spawn, not only after process inspection.
- Log the matched rule, decision, and reason into provenance for every command.
- Add a policy simulation mode to show what would be blocked before enforcing it.

## Branch and repo protections

- Detect repo root, current branch, and dirty state before each exec.
- Deny destructive commands on protected branches such as:
  - `main`
  - `master`
  - `prod`
  - `release/*`
- Optionally deny all writes unless the agent is on an approved branch.
- Support pinning a tracked session to:
  - one repo root
  - one allowed branch or branch pattern
- Block execution if the cwd moves outside the approved repo scope.
- Block execution if the branch changes outside the approved branch policy.

## Enforcement model

- Make pre-exec policy checks the primary control point.
- Keep runtime watchers and lineage checks as backup enforcement.
- Do not rely only on shell wrappers.
- Keep the central rule simple:
  - descendants must stay in the tracked boundary
  - commands must satisfy policy before they execute

## Additional controls to add

- Filesystem policy:
  - read-only vs writable paths per agent
  - explicit protected paths
- Network policy:
  - no network
  - allowlist only
  - full network
- Secret policy:
  - block known secret files
  - block shell credential files
  - block cloud credentials
  - block `.env` reads where appropriate
- Budget policy:
  - max runtime
  - max subprocess count
  - max files changed
- Approval policy for sensitive actions:
  - `git push`
  - `rm`
  - deploy commands
  - database migrations
  - package publish flows
- Repo snapshotting before and after tracked sessions.
- Session templates such as:
  - `safe-coder`
  - `read-only-reviewer`
  - `release-agent`

## Multi-session support

- Support multiple tracked sessions in parallel across multiple terminals.
- Replace the single global active-session assumption with per-session records only.
- Give each tracked session its own:
  - `session_id`
  - policy directory
  - transcript
  - controller pid
  - root pid
- Update `track status` to list active sessions instead of returning only one.
- Update `track stop` to stop by `session_id`, with an option to stop all.

## Transcript and stream capture

- Separate `stdout` and `stderr` capture.
- Note the tradeoff:
  - PTY mode preserves better interactive UX
  - PTY mode merges streams
- Support two modes:
  - interactive mode: PTY-first
  - forensic mode: separate `stdout` and `stderr`
- Record stream origin in transcript events.
- Consider a hybrid model if the target tool tolerates it:
  - PTY for terminal interaction
  - pipe for `stderr`

## More tests to add

- Provenance correlation for stronger escape cases.
- CPU spinner via detached Node/C path.
- Localhost listener via detached Node/C path.
- True 3-generation helper recording:
  - `ppid`
  - `pid`
  - `sid`
  - `pgid`
- Clean multi-generation lineage tests with distinct parent/child/grandchild identities.
- Timing variation tests:
  - very short-lived escapes
  - longer-lived escapes
  - repeated rapid churn
- Resource abuse tests:
  - CPU loops
  - file watchers
  - network listeners
- Alternate app-launch surfaces beyond Terminal and AppleScript.

## Longer-term hardening

- If a stronger containment guarantee is needed later, add OS-level sandboxing rather than relying only on user-space tracking.
