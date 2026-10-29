---
name: devils-advocate
description: "Use when the user explicitly or casually asks for a devil's advocate, devils advocate, counterargument, skeptical sparring partner, the other model's take, or asks Claude/Codex to challenge a research finding, design, plan or idea. Uses the opposite provider automatically through the devils-advocate MCP with persistent research context."
---

# Ask the other model

Use this when asked casually in a prompt as well as with `$devils-advocate`
(Codex) or `/devils-advocate` (Claude). The user should not need a special syntax,
provider choice, model name, or copied transcript. Quoted mentions and requests
to edit this project do not by themselves request a discussion.

1. Discover the devils-advocate MCP tools. Call `advocate_prepare` with the actual
   project/research cwd and a concise but sufficient topic: goal, proposed answer,
   constraints, established evidence, uncertainty and source/file pointers.
   The host registration prefers the opposite provider at medium effort.
   Allowance checks can choose the caller provider. Check allowance_selection. Do not invoke a CLI or use another subagent as a substitute.
2. A result with phase `thinking` is pending. Use `advocate_reply`, waiting up to
   25 seconds each call. Do not submit the same message again or start a second
   session because an answer is still being researched. A provider failure is
   an error, not agreement; report it and do not silently downgrade models.
3. Read the reply critically. Use `advocate_discuss` in the SAME session to push
   back with facts, correct an overclaim or test a revised recommendation.
   The partner can read/search independently and retains its native conversation.
   Do not force an arbitrary number of rounds; continue when an unresolved
   disagreement could materially change the decision.
4. Keep the main task moving. The partner is a researcher/discussion partner,
   not an implementer. The main model owns decisions, experiments and changes.
5. Call `advocate_shutdown` when the discussion is finished. Retain the session
   UUID until shutdown succeeds, including across tool reconnects. Report what
   changed in your conclusion, substantive remaining disagreements, and the
   evidence boundary. Do not treat agreement between two models as verification.

Omit session_id to use this MCP connection's current discussion; supply it to
select another or recover after reconnect. `advocate_status` costs no model turn.
Research logs and provider conversation IDs are retained locally after shutdown.
If these tools are not visible, the host needs to reload its MCP configuration;
do not claim to have consulted the other model.


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
