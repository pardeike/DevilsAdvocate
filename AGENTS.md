# DevilsAdvocate

Local stdio MCP for persistent cross-provider research discussions. Python standard
library only, tmux, and the user's authenticated Codex/Claude CLIs.

- Use `./scripts/dev test`, `./scripts/dev smoke`, `./scripts/dev host-smoke`, and `./scripts/dev deploy`.
  Each command saves full output under ignored artifacts/logs and prints only
  `ok` after its required checks pass. Smoke uses both real providers.
- Preserve other tmux servers and agent sessions. Follow docs/design.md for the
  Glasswing/vcomp lifecycle practices this project reuses.
- Models and role instructions belong in defaults.json and skeptic.md. Allowance-based provider fallback is authorized and must be visible in results.
  Do not silently downgrade models or retry uncertain provider turns.
- MCP stdout contains only newline-delimited JSON-RPC. No extra dependencies,
  TUI automation, or generic agent orchestration framework.
- Tests must exercise real tmux ownership/lifecycle with fake provider commands;
  real-provider smoke separately proves authentication, model choice and resume.
- Local deployment for both hosts is authorized. No public publishing or pushes.
