"""Owned dedicated tmux servers, short sockets and explicit lifecycle."""
from __future__ import annotations

import json
import os
import socket
import shutil
import stat
import subprocess
import sys
import time
import uuid
from pathlib import Path

from . import providers
from .worker import save


def data_root() -> Path:
    return Path.home() / 'Library/Application Support/DevilsAdvocate/sessions'


def private_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    info = path.lstat()
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid():
        raise RuntimeError('Runtime directory is not a private owned directory: ' + str(path))
    path.chmod(0o700)


class Sessions:
    def __init__(self, caller: str, root: Path | None = None):
        if caller not in {'codex', 'claude'}:
            raise ValueError('Caller must be codex or claude')
        self.caller = caller
        self.root = root or data_root()
        self.current = None

    def load(self, session_id: str | None) -> tuple[Path, dict]:
        sid = session_id or self.current
        if not sid or str(uuid.UUID(sid)) != sid:
            raise ValueError('Prepare a session first, or supply its UUID session_id')
        folder = self.root / sid
        state = json.loads((folder / 'session.json').read_text())
        if state['session_id'] != sid or state['caller'] != self.caller:
            raise ValueError('Session belongs to another caller')
        return folder, state

    @staticmethod
    def tmux(state: dict, *args: str, check=True) -> subprocess.CompletedProcess:
        return subprocess.run([state['executables']['tmux'], '-S', state['tmux_socket'], *args],
                              capture_output=True, text=True, check=check, timeout=8,
                              env=providers.environment())

    def owned(self, state: dict) -> bool:
        result = self.tmux(state, 'show-options', '-qv', '-t', 'advocate',
                           '@devils-advocate-session', check=False)
        return result.returncode == 0 and result.stdout.strip() == state['session_id']

    def rpc(self, state: dict, request: dict) -> dict:
        with socket.socket(socket.AF_UNIX) as client:
            client.settimeout(32)
            client.connect(state['rpc_socket'])
            client.sendall((json.dumps(request) + '\n').encode())
            raw = client.makefile('rb').readline(4 * 1024 * 1024)
        response = json.loads(raw)
        if not response['ok']:
            raise RuntimeError(response['error'])
        return response['result']

    def prepare(self, cwd: str, topic: str) -> dict:
        directory = Path(cwd).expanduser().resolve(strict=True)
        if not directory.is_dir() or not topic.strip() or len(topic) > 20000:
            raise ValueError('Provide an existing working directory and a topic of 1..20000 characters')
        cfg = providers.defaults()
        usage_exe = shutil.which('brrainztools')
        if not usage_exe:
            raise RuntimeError('BrrainzTools is required for allowance checks')
        usage = providers.allowance(usage_exe)
        sid = str(uuid.uuid4())
        folder = self.root / sid
        runtime = Path('/tmp') / f'devils-advocate-{os.getuid()}' / sid
        state = dict(session_id=sid, caller=self.caller,
            provider='claude' if self.caller == 'codex' else 'codex',
            model=cfg['claude_model'] if self.caller == 'codex' else cfg['codex_model'],
            effort=cfg['effort'], cwd=str(directory), topic=topic, phase='starting',
            turn_timeout_seconds=cfg['turn_timeout_seconds'], executables=providers.executables(),
            native_session_id=None, claude_session_id=str(uuid.uuid4()),
            tmux_socket=str(runtime / 'tmux.sock'), rpc_socket=str(runtime / 'rpc.sock'))
        state.update(allowance_executable=usage_exe,
                     allowance_reserve_percent=cfg.get('allowance_reserve_percent', 10))
        state.update(providers.select(state, usage))
        private_directory(folder)
        private_directory(runtime.parent)
        private_directory(runtime)
        save(folder / 'session.json', state)
        config = folder / 'tmux.conf'
        config.write_text('set -g status off\nset -g remain-on-exit on\nset -g destroy-unattached off\nset -g default-terminal tmux-256color\nset -g allow-passthrough off\n')
        started = False
        try:
            # vcomp: configure a retained placeholder before starting a possibly failing worker.
            self.tmux(state, '-f', str(config), 'new-session', '-d', '-s', 'advocate',
                      '-c', str(directory), '--', '/bin/cat')
            started = True
            self.tmux(state, 'set-option', '-t', 'advocate', '@devils-advocate-session', sid)
            self.tmux(state, 'respawn-pane', '-k', '-t', 'advocate:0.0', '-c', str(directory),
                      '--', sys.executable, str(providers.ROOT / 'main.py'), 'worker', str(folder))
            deadline = time.monotonic() + 8
            while True:
                try:
                    self.rpc(state, {'action': 'status'})
                    break
                except (FileNotFoundError, ConnectionRefusedError):
                    if time.monotonic() >= deadline:
                        pane = self.tmux(state, 'capture-pane', '-p', '-t', 'advocate', check=False).stdout
                        raise RuntimeError('Worker failed to start: ' + pane[-2000:])
                    time.sleep(0.03)
            self.current = sid
            return self.rpc(state, {'action': 'submit', 'message':
                'Establish this skeptical discussion. Identify the first important assumptions or evidence gaps in the topic below. If no proposal has been supplied yet, keep this brief and wait for it.\n\n' + topic})
        except Exception:
            if started:  # new-session succeeded on this attempt's fresh private UUID socket.
                self.tmux(state, 'kill-server', check=False)
            state['phase'] = 'failed'
            save(folder / 'session.json', state)
            raise

    def call(self, action: str, session_id=None, **args) -> dict:
        folder, state = self.load(session_id)
        if state['phase'] == 'closed' and action == 'status':
            return {k: state.get(k) for k in ('session_id', 'phase', 'provider', 'native_session_id')}
        owned = self.owned(state)
        if state['phase'] == 'closed' and action == 'shutdown' and not owned:
            if self.tmux(state, 'has-session', '-t', 'advocate', check=False).returncode == 0:
                raise RuntimeError('A foreign session exists at the saved socket; leave it untouched')
            return {'session_id': state['session_id'], 'phase': 'closed', 'tmux_stopped': True}
        if not owned:
            raise RuntimeError('Owned tmux session is missing; inspect ' + str(folder) + '. Prepare a new session; no turn was replayed.')
        if action == 'shutdown':
            result = ({'session_id': state['session_id'], 'phase': 'closed'} if state['phase'] == 'closed'
                      else self.rpc(state, {'action': 'shutdown'}))
            self.tmux(state, 'kill-server')  # This socket belongs exclusively to this UUID.
            if self.tmux(state, 'has-session', '-t', 'advocate', check=False).returncode == 0:
                raise RuntimeError('tmux shutdown was not verified')
            runtime = Path(state['rpc_socket']).parent
            for name in ('rpc.sock', 'tmux.sock'):
                path = runtime / name
                if path.exists() and stat.S_ISSOCK(path.lstat().st_mode):
                    path.unlink()
            if runtime.exists() and not any(runtime.iterdir()):
                runtime.rmdir()
            if self.current == state['session_id']:
                self.current = None
            result['tmux_stopped'] = True
            return result
        return self.rpc(state, {'action': action, **args})
