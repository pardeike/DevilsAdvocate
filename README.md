# Devil's Advocate

Ask the other model to challenge a research finding, design or proposed solution.
It can inspect sources independently and remember what it learns while you argue
through revisions. It does not implement the task.

Say it naturally in a prompt:

> Before implementing this, ask the devil's advocate to challenge the design.

> Get the other model's take on this conclusion, then push back if its objections don't hold.

The installed skill also supports `$devils-advocate` in Codex and
`/devils-advocate` in Claude. Casual requests use the same MCP workflow. Skill
routing is a host/model decision, not a keystroke interceptor.

| Main agent | Discussion partner | Effort |
| --- | --- | --- |
| Codex | Latest Claude Opus, currently Opus 5.5 | medium |
| Claude | Codex GPT-6.1 Sol | medium |

Selection is automatic. There is no provider selector in tool arguments.
Claude's `opus` alias follows the latest Opus available to that CLI/account.
Codex's verified current Sol ID is kept in `defaults.json`; update that setting
when a newer Sol becomes available. Never silently substitute a different model.
Existing native CLI subscription authentication is used; no API keys are needed.

## Install

Requires Python 3.11+, tmux, and signed-in `codex` and `claude` CLIs on macOS.
On this Mac use the canonical quiet workflow:

```sh
./scripts/dev test
./scripts/dev smoke
./scripts/dev host-smoke
./scripts/dev deploy
```

`test` drives real tmux with fake providers. `smoke` uses both real providers,
checks independent file reading and retained conversation context on a second
turn, then shuts down each owned session. `host-smoke` checks real natural-language MCP selection in fresh Claude and Codex
hosts. `deploy` runs tests, installs an
immutable runtime release, registers this MCP in Codex and Claude user configs,
installs the skill for both, and probes both installed stdio configurations.
It preserves other MCP registrations and refuses a conflicting registration.
Codex receives per-tool approval for this MCP's prepare/discuss/shutdown tools so
noninteractive sessions can use the requested workflow; other MCP permissions and
the partner's read-only restrictions are unchanged.
Success prints exactly `ok`; failures name the step and log in `artifacts/logs`.

New sessions load the tools and skill after installation. Existing hosts may
need an MCP/configuration reload. Installation and a direct stdio probe do not
prove that an already-running host refreshed its tool snapshot.

## Tools

| Tool | Purpose |
| --- | --- |
| `advocate_prepare(topic, cwd?)` | Create an owned tmux session and prime an opposite-model conversation with the research context. |
| `advocate_discuss(message, session_id?)` | Push back or supply new evidence in the same native conversation. |
| `advocate_reply(session_id?, wait_seconds?)` | Read/wait for a pending reply, without submitting another turn. |
| `advocate_status(session_id?)` | Read provider, phase and native conversation identity without model usage. |
| `advocate_shutdown(session_id?)` | Cancel an active turn and tear down only this owned tmux session. Retain transcripts. |

Prepare/discuss wait up to 25 seconds by default. A longer research turn returns
`thinking`; use reply until it completes. Never resubmit a pending or uncertain
turn. A provider failure requires a fresh session. Each turn has a five-minute
bound. Omit session_id to use the current session on that MCP connection, or
retain the UUID to continue after reconnecting.

The skeptical prompt distinguishes facts from speculation, asks for independent
evidence, revises conclusions when challenged, and avoids disagreement for its
own sake. It explicitly rejects speculative "too much work/time" objections and
hour/line-count estimates. Complexity objections need a concrete mechanism.

## Session behavior

A dedicated tmux server owns an idle-blocking Python supervisor. Each exchange
runs the provider's structured noninteractive CLI and resumes its native
conversation ID. The CLI process exits between turns; context persists in the
provider's conversation, not a reconstructed transcript. The tmux supervisor
survives MCP reconnects until shutdown. There are no terminal pastes or screen
scraping, and no background model turns while idle.

Codex runs with a read-only sandbox and no approval prompts. Claude runs in plan
mode with read/search/web tools, no write/edit tools, and no permission prompts.
Read-only shell access is available where the CLI permits it. Both receive the
same discussion-only role, and inherited MCP integrations/hooks are disabled to
avoid recursion and unrelated side effects. This is a research restriction,
not a claim of adversarial isolation from arbitrary hostile content.

State and transcripts live in
`~/Library/Application Support/DevilsAdvocate/sessions/<UUID>/`. Short private
sockets live under `/tmp/devils-advocate-<uid>/<UUID>/`. Shutdown keeps evidence
and native provider history while removing the owned tmux server/socket paths.
To inspect a live session manually, read its session.json for tmux_socket, then
use `tmux -S PATH attach -t advocate`. Do not type into the supervisor pane.

See [design and evidence](docs/design.md) for the existing project practices
reused here and the validation boundary. Portable plugin manifests are included;
the local deploy command is the verified installation path for both hosts.


## Allowance-aware fallback

Before preparation and every discussion turn, the server calls
`brrainztools usage all`. A shared 60-second cache prevents repeated polls across hosts and workers.
BrrainzTools owns authentication, live allowance
collection, and TokenCoffee forecasting. The MCP does not call provider usage APIs.

The opposite provider remains preferred. A fresh, account-verified forecast
triggers fallback when the average of optimistic and pessimistic projected usage
at reset reaches 100%. Forecast samples must be at most 15 minutes old.
Without a trusted forecast, any allowance window at 10% or less triggers fallback.
The 10% reserve is configurable in defaults.json.

If both providers are constrained, choose the lower average projected usage at
reset. Use each provider's highest average across its forecast windows.
If comparable forecasts are unavailable, choose more live allowance remaining.
This can use a provider below the 10% reserve, but never at or below 1%.
A provider with 1% or less remaining never starts another turn.
Unknown or stale live allowance cannot authorize a turn.
Live checks must be at most two minutes old. All returned allowance windows are
checked conservatively, including model windows.

When a discussion switches providers, it transfers the saved user/advocate
transcript into a new native conversation. Unreported tool results and internal
reasoning do not transfer. Subsequent turns retain that provider while it is healthy. Reevaluate constrained providers before each turn.
Results expose `allowance_selection`, including the reason and selected provider.
A failed or uncertain model turn is never retried automatically.
BrrainzTools must be on PATH. No additional login is stored by this project.
