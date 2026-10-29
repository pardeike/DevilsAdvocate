"""Small MCP stdio server; long discussions return a durable pending turn."""
from __future__ import annotations

import concurrent.futures
import json
import os
import sys
import threading
from pathlib import Path

from .sessions import Sessions

TEXT = {'type': 'string'}
SESSION = {'type': 'string', 'description': 'Optional session UUID. Omit to use the session prepared on this MCP connection.'}
WAIT = {'type': 'integer', 'minimum': 0, 'maximum': 25, 'default': 25,
        'description': 'Wait up to this many seconds. If still thinking, call advocate_reply; do not resubmit.'}


def tool(name, description, properties, required=(), read_only=False):
    return {'name': name, 'description': description,
            'inputSchema': {'type': 'object', 'properties': properties, 'required': list(required), 'additionalProperties': False},
            'annotations': {'readOnlyHint': read_only, 'destructiveHint': False, 'openWorldHint': True}}


TOOLS = [
    tool('advocate_prepare', 'Start and prime a persistent skeptical discussion with the opposite provider when allowance permits, automatically. Use when the user asks for a devil\'s advocate, counterargument, independent challenge, or the other model\'s take. Prefer Claude Opus for Codex callers and Codex Sol for Claude callers. Use the caller provider when opposite-provider allowance or its forecast is constrained. Medium effort. No task implementation.',
         {'topic': {**TEXT, 'description': 'Question/proposal, goal, constraints, known evidence and unresolved assumptions. The partner can inspect sources independently.'},
          'cwd': {**TEXT, 'description': 'Existing research/project working directory. Defaults to the MCP working directory.'}, 'wait_seconds': WAIT}, ['topic']),
    tool('advocate_discuss', 'Continue the SAME skeptical discussion. Allowance checks can transfer the transcript to the caller provider. Supply pushback, new evidence or a revised proposal. The partner retains its own research context. Wait for a pending reply before submitting another turn.',
         {'message': TEXT, 'session_id': SESSION, 'wait_seconds': WAIT}, ['message']),
    tool('advocate_reply', 'Read or wait for the current turn. A pending result is not a failure and must not be resubmitted. Safe to repeat; completed replies remain available.',
         {'session_id': SESSION, 'wait_seconds': WAIT}, read_only=True),
    tool('advocate_status', 'Inspect the current session/provider/model/native conversation ID and phase without invoking a model.',
         {'session_id': SESSION}, read_only=True),
    tool('advocate_shutdown', 'Stop only this owned discussion and its dedicated tmux server, including any active provider turn. Keep transcripts for review. Idempotent with an explicit session_id.',
         {'session_id': SESSION})
]


class MCP:
    def __init__(self, caller=None, root: Path | None = None):
        self.caller = caller
        self.root = root
        self.sessions = Sessions(caller, root) if caller else None
        self.output_lock = threading.Lock()

    def handle(self, method: str, params: dict) -> dict:
        if method == 'initialize':
            if not self.caller:
                name = str(params.get('clientInfo', {}).get('name', '')).lower()
                self.caller = 'codex' if 'codex' in name else 'claude' if 'claude' in name else None
                if not self.caller:
                    raise ValueError('Unknown MCP caller; configure --caller codex or --caller claude')
                self.sessions = Sessions(self.caller, self.root)
            return {'protocolVersion': params.get('protocolVersion', '2024-11-05'),
                    'capabilities': {'tools': {}},
                    'serverInfo': {'name': 'devils-advocate', 'version': '0.2.0'},
                    'instructions': 'Use this MCP for casual or explicit requests to ask the devil\'s advocate, get the other model\'s take, or challenge an idea. Prepare with project cwd and relevant context, then discuss in the same session. Read pending replies; never replay a pending turn. Prefer substantive evidence-based exchange over a one-shot approval. Shutdown when finished. The partner researches but does not implement.'}
        if method == 'ping':
            return {}
        if method == 'tools/list':
            return {'tools': TOOLS}
        if method != 'tools/call':
            raise ValueError('Unsupported method: ' + method)
        try:
            if not self.sessions:
                raise ValueError('Initialize first')
            name, args = params['name'], params.get('arguments', {})
            schema = next(t for t in TOOLS if t['name'] == name)['inputSchema']
            if not isinstance(args, dict) or set(args) - set(schema['properties']) or set(schema['required']) - set(args):
                raise ValueError('Invalid tool arguments')
            wait = args.get('wait_seconds', 25)
            if type(wait) is not int or not 0 <= wait <= 25:
                raise ValueError('wait_seconds must be an integer from 0 to 25')
            sid = args.get('session_id')
            if name == 'advocate_prepare':
                result = self.sessions.prepare(args.get('cwd') or os.getcwd(), args['topic'])
                result = self.sessions.call('reply', result['session_id'], wait_seconds=wait)
            elif name == 'advocate_discuss':
                result = self.sessions.call('submit', sid, message=args['message'])
                result = self.sessions.call('reply', result['session_id'], wait_seconds=wait)
            elif name == 'advocate_reply':
                result = self.sessions.call('reply', sid, wait_seconds=wait)
            elif name == 'advocate_shutdown':
                result = self.sessions.call('shutdown', sid)
            else:
                result = self.sessions.call('status', sid) if sid or self.sessions.current else {'phase': 'not_prepared', 'caller': self.caller}
            failed = result.get('phase') == 'failed'
            return {'content': [{'type': 'text', 'text': json.dumps(result, ensure_ascii=False)}],
                    'structuredContent': result, 'isError': failed}
        except Exception as error:
            result = {'error': str(error)}
            return {'content': [{'type': 'text', 'text': json.dumps(result)}],
                    'structuredContent': result, 'isError': True}

    def respond(self, request: dict) -> None:
        if 'id' not in request:
            return
        response = {'jsonrpc': '2.0', 'id': request['id']}
        try:
            response['result'] = self.handle(request['method'], request.get('params', {}))
        except Exception as error:
            response['error'] = {'code': -32602, 'message': str(error)}
        with self.output_lock:
            print(json.dumps(response, ensure_ascii=False), flush=True)

    def serve(self) -> None:
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            for line in sys.stdin:
                try:
                    request = json.loads(line)
                    if request.get('method') == 'initialize':
                        self.respond(request)
                    else:
                        pool.submit(self.respond, request)
                except json.JSONDecodeError:
                    with self.output_lock:
                        print(json.dumps({'jsonrpc': '2.0', 'id': None, 'error': {'code': -32700, 'message': 'Invalid JSON'}}), flush=True)
