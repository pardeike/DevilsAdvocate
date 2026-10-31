"""Native CLI adapters. Authentication remains owned by the installed CLIs."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import math
import time
import fcntl
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def defaults() -> dict:
    return json.loads((ROOT / 'defaults.json').read_text())


def environment() -> dict[str, str]:
    env = os.environ.copy()
    for key in list(env):
        if key in {'TMUX', 'TMUX_PANE', 'CLAUDECODE', 'CODEX_THREAD_ID',
                   'CODEX_INTERNAL_ORIGINATOR_OVERRIDE', 'CLAUDE_CODE_ENTRYPOINT'} or key.startswith('GLASSWING_'):
            env.pop(key, None)
    # Keep subscription auth; do not accidentally switch a review to metered API keys.
    for key in ('ANTHROPIC_API_KEY', 'OPENAI_API_KEY'):
        env.pop(key, None)
    return env


def command(state: dict) -> list[str]:
    provider = state['provider']
    exe = state['executables'][provider]
    native = state.get('native_session_id')
    if provider == 'claude':
        cmd = [exe, '--safe-mode', '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}',
               '--tools', 'Read,Grep,Glob,WebSearch,WebFetch,Bash',
               '--allowedTools', 'Read,Grep,Glob,WebSearch,WebFetch',
               '--permission-mode', 'plan', '--permission-prompts', 'none',
               '--model', state['model'], '--effort', state['effort'], '--output-format', 'json',
               '--append-system-prompt', (ROOT / 'skeptic.md').read_text()]
        cmd += ['--resume', native] if native else ['--session-id', state['claude_session_id']]
        return cmd + ['-p']
    cmd = [exe, 'exec', '--ignore-user-config', '--ignore-rules',
           '--skip-git-repo-check', '--json', '-m', state['model'],
           '-c', 'model_reasoning_effort=' + json.dumps(state['effort']),
           '-c', 'approval_policy="never"', '-c', 'sandbox_mode="read-only"',
           '-c', 'web_search="live"', '-c', 'features.memories=false',
           '-c', 'model_instructions_file=' + json.dumps(str(ROOT / 'skeptic.md'))]
    if native:
        cmd.insert(2, 'resume')
        return cmd + [native, '-']
    return cmd + ['-']


def parse(provider: str, output: str, previous_id: str | None) -> dict:
    if provider == 'claude':
        data = json.loads(output)
        if data.get('is_error'):
            raise RuntimeError(str(data.get('result', 'Claude reported a failed turn')))
        if not data.get('session_id') or not isinstance(data.get('result'), str):
            raise RuntimeError('Claude returned no session ID or final reply')
        return {'native_session_id': data['session_id'], 'reply': data['result'],
                'actual_models': list(data.get('modelUsage', {})), 'usage': data.get('usage', {})}
    native = previous_id
    replies = []
    usage = {}
    for line in output.splitlines():
        event = json.loads(line)
        if event.get('type') == 'thread.started':
            native = event.get('thread_id')
        elif event.get('type') in {'turn.failed', 'error'}:
            raise RuntimeError(str(event.get('error', event)))
        elif event.get('type') == 'item.completed':
            item = event.get('item', {})
            if item.get('type') == 'agent_message':
                replies.append(item.get('text', ''))
        elif event.get('type') == 'turn.completed':
            usage = event.get('usage', {})
    if not native or not replies:
        raise RuntimeError('Codex returned no native session ID or final reply')
    return {'native_session_id': native, 'reply': '\n\n'.join(replies),
            'actual_models': [], 'usage': usage}


def executables() -> dict[str, str]:
    found = {name: shutil.which(name) for name in ('codex', 'claude', 'tmux')}
    missing = [name for name, path in found.items() if not path]
    if missing:
        raise RuntimeError('Missing installed CLI: ' + ', '.join(missing))
    return found


def fetch_allowance(executable: str) -> dict:
    """Reuse BrrainzTools authentication and live allowance collection."""
    result = subprocess.run([executable, 'usage', 'all'], capture_output=True,
                            text=True, timeout=30, env=environment())
    # A partial failure exits 1 but still includes the other provider's data.
    if result.returncode not in (0, 1):
        raise RuntimeError('BrrainzTools allowance check failed')
    return json.loads(result.stdout)['data']



def allowance(executable: str) -> dict:
    # Share a short cache across MCP hosts and workers to avoid usage endpoint 429s.
    folder = Path.home() / 'Library/Caches/DevilsAdvocate'
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    path = folder / 'allowance.json'
    with (folder / 'allowance.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            cached = json.loads(path.read_text())
            if 0 <= time.time() - cached['saved_at'] < 60:
                if cached['executable'] == executable:
                    return cached['data']
        except (OSError, ValueError, KeyError, TypeError):
            pass
        data = fetch_allowance(executable)
        temp = path.with_suffix('.tmp')
        temp.write_text(json.dumps(dict(saved_at=time.time(), executable=executable, data=data)))
        temp.chmod(0o600)
        temp.replace(path)
        return data


def credit_capacity(entry: dict) -> bool:
    credits = entry.get('credits')
    return (entry.get('provider') == 'codex' and isinstance(credits, dict)
            and credits.get('status') == 'ok' and credits.get('canContinue') is True)


def available(data: dict, provider: str, reserve: float, use_forecast: bool = True) -> tuple[bool, str]:
    entry = next((p for p in data.get('providers', []) if p.get('provider') == provider), {})
    if entry.get('status') != 'ok' or not entry.get('windows'):
        return False, 'allowance unavailable'
    try:
        checked = datetime.fromisoformat(entry['checkedAt'].replace('Z', '+00:00'))
        age = (datetime.now(timezone.utc) - checked).total_seconds()
        if not 0 <= age <= 120:
            return False, 'allowance is stale'
        credit_backed = credit_capacity(entry)
        for window in entry['windows']:
            remaining = window['remainingPercent']
            if type(remaining) not in (int, float) or not math.isfinite(remaining) or not 0 <= remaining <= 100:
                return False, 'invalid allowance'
            if credit_backed and window.get('name') in {'primary', 'secondary'}:
                continue
            if remaining <= 1:
                return False, f"{window['name']} has {remaining}% remaining"
            forecast = window.get('forecast', {})
            trusted = use_forecast and forecast.get('status') == 'ok' and forecast.get('accountMatch') == 'verified'
            if trusted:
                try:
                    sample = datetime.fromisoformat(forecast['latestSampleAt'].replace('Z', '+00:00'))
                    sample_age = (datetime.now(timezone.utc) - sample).total_seconds()
                    projections = [forecast[key]['usedPercentAtReset'] for key in ('optimistic', 'pessimistic')]
                    trusted = (0 <= sample_age <= 900 and all(
                        type(value) in (int, float) and math.isfinite(value) and value >= 0
                        for value in projections))
                except (KeyError, TypeError, ValueError):
                    trusted = False
            if trusted:
                average = sum(projections) / 2
                if average >= 100:
                    return False, f"{window['name']} forecasts {average:.1f}% average usage at reset"
            elif remaining <= reserve:
                return False, f"{window['name']} has {remaining}% remaining"
    except (KeyError, TypeError, ValueError):
        return False, 'invalid allowance'
    return True, ('verified Codex credits available' if credit_backed
                  else 'verified allowance above reserve')


def pressure(data: dict, provider: str) -> tuple[float | None, float]:
    entry = next(p for p in data['providers'] if p['provider'] == provider)
    forecasts = []
    remaining = min(w['remainingPercent'] for w in entry['windows'])
    for window in entry['windows']:
        forecast = window.get('forecast', {})
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(
                forecast['latestSampleAt'].replace('Z', '+00:00'))).total_seconds()
            values = [forecast[key]['usedPercentAtReset'] for key in ('optimistic', 'pessimistic')]
            if (forecast.get('status') == 'ok' and forecast.get('accountMatch') == 'verified'
                    and 0 <= age <= 900 and all(type(v) in (int, float)
                    and math.isfinite(v) and v >= 0 for v in values)):
                forecasts.append(sum(values) / 2)
        except (KeyError, TypeError, ValueError):
            pass
    return (max(forecasts) if forecasts else None, remaining)


def select(state: dict, data: dict) -> dict:
    reserve = state.get('allowance_reserve_percent', 10)
    preferred = 'claude' if state['caller'] == 'codex' else 'codex'
    current = state['provider']
    healthy = {p: available(data, p, reserve) for p in ('codex', 'claude')}
    eligible = [p for p in ('codex', 'claude')
                if available(data, p, 1, use_forecast=False)[0]]
    if not eligible:
        raise RuntimeError('No provider has verified usable credits or allowance above the 1% hard stop')
    if healthy[current][0]:
        selected, reason = current, healthy[current][1]
    elif any(healthy[p][0] for p in eligible):
        selected = next(p for p in eligible if healthy[p][0])
        reason = current + ': ' + healthy[current][1] + '; ' + selected + ': ' + healthy[selected][1]
    else:
        pressures = {p: pressure(data, p) for p in eligible}
        if all(pressures[p][0] is not None for p in eligible):
            selected = min(eligible, key=lambda p: (pressures[p][0], -pressures[p][1], p != preferred))
            reason = ('Only provider with verified allowance above hard stop' if len(eligible) == 1
                      else 'Both providers constrained; lower average projected usage at reset')
        else:
            selected = max(eligible, key=lambda p: (pressures[p][1], p == preferred))
            reason = 'Providers constrained; more live allowance remaining'
    return dict(provider=selected, model=defaults()[selected + '_model'],
                allowance_selection=dict(preferred_provider=preferred, selected_provider=selected,
                    reserve_percent=reserve, reason=reason,
                    fallback=selected != preferred,
                    credits_available=credit_capacity(next(
                        (p for p in data['providers'] if p.get('provider') == selected), {}))))
