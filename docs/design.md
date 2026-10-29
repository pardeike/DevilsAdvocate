# Design and evidence

## Origin

The design grew from a multi-round research discussion between Codex and Claude.
The main agent challenged unsupported conclusions and supplied additional evidence.
The discussion partner then revised its recommendation.
This supports retained context and discussion across turns.
It does not support treating a single response as an approval gate.
Private research transcripts are not included in this repository.

## Practices reused

- Glasswing `Sources/GlasswingTerminal/MacTerminalSessions.swift` and
  `TerminalFiles.swift`: dedicated servers, short private socket paths,
  explicit lifecycle identity, clear parent-agent/tmux environment variables,
  preserve other sessions, and keep failed-start diagnostics.
- vcomp `internal/tmux/tmux.go`: launch a retained placeholder, configure ownership
  before starting the real worker, retain dead panes for diagnosis, and verify
  ownership before controlling a session.
- vcomp `internal/engine/direct.go`: short Unix-domain control sockets, direct
  request/reply and conservative handling of an uncertain submission. This MCP
  uses structured CLI output rather than terminal prompt recognition.

No changes are made to those projects. This implementation has no generic
multi-agent scheduler, automatic debate rounds or background prompts.

## Protocol and ownership

Host registrations set `--caller codex` or `--caller claude`. This is reliable
without heuristics, model-supplied provider choices or inherited nested-agent
environment variables. Portable use can identify a known MCP clientInfo name;
unknown clients fail explicitly. Each discussion has its own UUID, private
runtime directory, dedicated tmux socket and ownership marker. Another caller
cannot select it; a missing or mismatched marker is never taken over.

Native sessions and turn output are saved. A pending turn is read rather than
resubmitted. A failed or timed-out provider turn poisons the session rather than
replaying a potentially partial mutation of its conversation. Explicit shutdown
stops only its worker and owned subprocess group, verifies tmux termination and
retains diagnostics. Parent MCP exit leaves the conversation available by UUID.
The partner has no write tools or permission escalation path. The role also
prohibits implementing changes, experiments and communication to other people.

## Models and configuration

Installed Codex's models_cache.json confirms gpt-6.1-sol and medium support.
Claude's installed CLI documents `opus` as the latest-model alias and supports
`--effort medium`. Live smoke verifies the actual model in modelUsage. Sol is
pinned to the current verified name instead of guessing a future model ID.

Model/effort defaults live in defaults.json; the skeptic role lives in skeptic.md.
Both CLIs authenticate through their own native subscription state. Parent API
key environment variables are not forwarded, to avoid an accidental change to
metered API authentication. MCP/provider configuration does not store tokens.

## Verification

The unit/integration suite drives real tmux with deterministic fake providers,
covering both directions, native resume, MCP reconnect, ownership mismatch,
pending-turn duplicate rejection, failed-turn replay rejection and active-turn
shutdown. Real-provider smoke separately verifies independent file access,
model reply, retained context and identical native session IDs on a follow-up.
Deployment probes initialize/tools/list/status through both exact installed
stdio configurations. An existing host still needs to reload its tool snapshot.

## Codex tool approval registration

A fresh Codex exec host found the MCP but denied its lifecycle tool under
approval_policy=never. Deployment therefore records per-tool approve overrides
for advocate_prepare, advocate_discuss and advocate_shutdown on this server only.
Existing conflicting user overrides are preserved and deployment fails explicitly.
Read-only tools retain normal policy. The counter-model still has its read-only
sandbox/plan-mode restrictions. Reference:
https://developers.openai.com/codex/config-reference/ . The fresh-host smoke
reproduces this exact scoped configuration instead of broad permission bypass.
