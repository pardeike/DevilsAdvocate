import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import patch

from devils_advocate import providers
from devils_advocate.server import MCP
from devils_advocate.sessions import Sessions


FAKE = '''#!/opt/homebrew/bin/python3
import json,sys,time,pathlib
text=sys.stdin.read()
pathlib.Path(sys.argv[0]+'.prompt').write_text(text)
if 'SLOW' in text: time.sleep(30)
if 'FAIL' in text: sys.exit(7)
args=sys.argv[1:]
codex='exec' in args
native='fake-native-codex'
resume='--resume' in args or 'resume' in args
reply=('continued' if resume else 'primed')+': '+text[-80:]
if codex:
 print(json.dumps({'type':'thread.started','thread_id':native}))
 print(json.dumps({'type':'item.completed','item':{'type':'agent_message','text':reply}}))
 print(json.dumps({'type':'turn.completed','usage':{'input_tokens':1}}))
else:
 flag='--resume' if resume else '--session-id'
 print(json.dumps({'session_id':args[args.index(flag)+1],'result':reply,'modelUsage':{'claude-opus-5-5':{}}}))
'''


class SessionsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='devils-advocate-test-')
        self.root = Path(self.temp.name)
        self.fake = self.root / 'fake-provider'
        self.fake.write_text(FAKE)
        self.fake.chmod(0o700)
        self.exe = providers.executables()
        self.exe.update(codex=str(self.fake), claude=str(self.fake))
        self.patch = patch('devils_advocate.providers.executables', return_value=self.exe)
        self.patch.start()
        self.usage_file = self.root / 'usage.json'
        self.write_usage()
        usage_cli = self.root / 'brrainztools'
        usage_cli.write_text('#!/usr/bin/python3\nimport pathlib\nprint(pathlib.Path('
                             + repr(str(self.usage_file)) + ').read_text())\n')
        usage_cli.chmod(0o700)
        self.env_patch = patch.dict(os.environ, PATH=str(self.root) + os.pathsep + os.environ['PATH'])
        self.env_patch.start()
        self.sessions = []

    def write_usage(self, codex=80, claude=80):
        entries = []
        for name, remaining in [('codex', codex), ('claude', claude)]:
            entries.append({'provider': name, 'status': 'ok',
                            'checkedAt': datetime.now(timezone.utc).isoformat(),
                            'windows': [{'name': 'weekly', 'remainingPercent': remaining}]})
        self.usage_file.write_text(json.dumps({'data': {'providers': entries}}))
        cache = Path.home() / 'Library/Caches/DevilsAdvocate/allowance.json'
        if cache.exists():
            cached = json.loads(cache.read_text())
            if cached.get('executable') == str(self.root / 'brrainztools'):
                cached['saved_at'] = 0
                cache.write_text(json.dumps(cached))

    def tearDown(self):
        for s, sid in self.sessions:
            try: s.call('shutdown', sid)
            except Exception: pass
        self.env_patch.stop()
        self.patch.stop()
        self.temp.cleanup()

    def prepare(self, caller='codex', topic='Review this assumption'):
        s = Sessions(caller, self.root / 'sessions')
        r = s.prepare(str(self.root), topic)
        self.sessions.append((s, r['session_id']))
        return s, r

    def test_opposite_provider_and_native_resume_both_directions(self):
        for caller, opposite in [('codex', 'claude'), ('claude', 'codex')]:
            s, initial = self.prepare(caller)
            r = s.call('reply', wait_seconds=5)
            self.assertEqual(r['provider'], opposite)
            self.assertEqual(r['effort'], 'medium')
            self.assertIn('primed', r['reply'])
            native = r['native_session_id']
            s.call('submit', message='Consider new evidence')
            r = s.call('reply', wait_seconds=5)
            self.assertIn('continued', r['reply'])
            self.assertEqual(r['native_session_id'], native)
            # A reconnected MCP process can select the saved conversation by UUID.
            again = Sessions(caller, self.root / 'sessions')
            self.assertEqual(again.call('status', initial['session_id'])['native_session_id'], native)
            closed = again.call('shutdown', initial['session_id'])
            self.assertTrue(closed['tmux_stopped'])
            self.assertEqual(again.call('shutdown', initial['session_id'])['phase'], 'closed')

    def test_low_allowance_falls_back_both_directions(self):
        for caller in ['codex', 'claude']:
            self.write_usage(codex=80 if caller == 'codex' else 10,
                             claude=80 if caller == 'claude' else 10)
            session, _ = self.prepare(caller)
            reply = session.call('reply', wait_seconds=5)
            self.assertEqual(reply['phase'], 'ready')
            self.assertEqual(reply['provider'], caller)
            self.assertTrue(reply['allowance_selection']['fallback'])

    def test_mid_discussion_switch_preserves_transcript(self):
        session, initial = self.prepare(topic='A unique original proposal')
        self.assertEqual(session.call('reply', wait_seconds=5)['provider'], 'claude')
        self.write_usage(claude=9)
        turn = session.call('submit', message='Review the new evidence')
        reply = session.call('reply', wait_seconds=5)
        self.assertEqual(reply['phase'], 'ready')
        self.assertEqual(reply['provider'], 'codex')
        prompt = Path(str(self.fake) + '.prompt').read_text()
        self.assertIn('A unique original proposal', prompt)
        self.assertIn('primed:', prompt)
        self.write_usage(claude=80)
        session.call('submit', message='Continue')
        reply = session.call('reply', wait_seconds=5)
        self.assertEqual(reply['provider'], 'codex')
        self.assertIn('continued', reply['reply'])

    def test_both_low_refuses_before_creating_session(self):
        self.write_usage(codex=1, claude=1)
        with self.assertRaisesRegex(RuntimeError, 'No provider'):
            self.prepare()

    def test_pending_turn_is_not_resubmitted_and_shutdown_cancels_it(self):
        s, r = self.prepare(topic='SLOW discussion')
        pending = s.call('reply', wait_seconds=0)
        self.assertEqual(pending['phase'], 'thinking')
        with self.assertRaisesRegex(RuntimeError, 'already running'):
            s.call('submit', message='Another prompt')
        before = time.monotonic()
        self.assertTrue(s.call('shutdown')['tmux_stopped'])
        self.assertLess(time.monotonic() - before, 7)

    def test_shutdown_recovers_after_worker_closed_before_tmux_teardown(self):
        s, r = self.prepare()
        s.call('reply', wait_seconds=5)
        _, state = s.load(r['session_id'])
        s.rpc(state, {'action': 'shutdown'})
        self.assertTrue(s.call('shutdown', r['session_id'])['tmux_stopped'])
        self.assertEqual(s.call('shutdown', r['session_id'])['phase'], 'closed')

    def test_failed_turn_cannot_be_replayed(self):
        s, _ = self.prepare(topic='FAIL discussion')
        r = s.call('reply', wait_seconds=5)
        self.assertEqual(r['phase'], 'failed')
        with self.assertRaisesRegex(RuntimeError, 'uncertain'):
            s.call('submit', message='Try again')

    def test_cannot_touch_other_owner_or_foreign_tmux(self):
        s, r = self.prepare()
        other = Sessions('claude', self.root / 'sessions')
        with self.assertRaisesRegex(ValueError, 'another caller'):
            other.call('shutdown', r['session_id'])
        _, state = s.load(r['session_id'])
        s.tmux(state, 'set-option', '-t', 'advocate', '@devils-advocate-session', 'foreign')
        with self.assertRaisesRegex(RuntimeError, 'missing'):
            s.call('shutdown')
        # Only the test, which created this socket, may restore its ownership marker.
        s.tmux(state, 'set-option', '-t', 'advocate', '@devils-advocate-session', r['session_id'])

    def test_mcp_schemas_no_provider_selection_and_bad_arguments(self):
        mcp = MCP('codex', self.root / 'sessions')
        schemas = mcp.handle('tools/list', {})['tools']
        self.assertEqual(len(schemas), 5)
        for tool in schemas:
            self.assertNotIn('provider', tool['inputSchema']['properties'])
        invalid = mcp.handle('tools/call', {'name':'advocate_prepare','arguments':{'topic':'test','provider':'claude'}})
        self.assertTrue(invalid['isError'])
        status = mcp.handle('tools/call', {'name':'advocate_status','arguments':{}})
        self.assertEqual(status['structuredContent']['phase'], 'not_prepared')

    def test_environment_and_no_recursion_or_write_tool_config(self):
        with patch.dict(os.environ, TMUX='parent', CLAUDECODE='yes', CODEX_THREAD_ID='parent', OPENAI_API_KEY='secret'):
            env = providers.environment()
            for name in ['TMUX','CLAUDECODE','CODEX_THREAD_ID','OPENAI_API_KEY']:
                self.assertNotIn(name, env)
        cfg = dict(executables=self.exe, provider='claude', model='opus', effort='medium', claude_session_id='id')
        cmd = providers.command(cfg)
        self.assertIn('--safe-mode', cmd)
        self.assertIn('--strict-mcp-config', cmd)
        self.assertIn('plan', cmd)
        self.assertNotIn('Edit', cmd[cmd.index('--tools')+1])
        cfg.update(provider='codex', model='gpt-6.1-sol')
        cmd = providers.command(cfg)
        self.assertIn('--ignore-user-config', cmd)
        self.assertIn('sandbox_mode="read-only"', cmd)
        self.assertIn('model_reasoning_effort="medium"', cmd)


class AllowanceTest(unittest.TestCase):
    def entry(self, name='codex', remaining=80):
        return {'provider': name, 'status': 'ok',
                'checkedAt': datetime.now(timezone.utc).isoformat(),
                'windows': [{'name': 'primary', 'remainingPercent': remaining}]}

    def test_unknown_stale_and_invalid_are_not_safe(self):
        for entry in [{}, dict(self.entry(), status='unavailable'),
                      dict(self.entry(), windows=[]),
                      dict(self.entry(), checkedAt='2020-01-01T00:00:00Z'),
                      self.entry(remaining=float('nan')), self.entry(remaining=True)]:
            self.assertFalse(providers.available({'providers': [entry]}, 'codex', 10)[0])

    def test_any_restricted_window_blocks_provider(self):
        entry = self.entry('claude')
        entry['windows'].append({'name': 'model-name:fable', 'remainingPercent': 10})
        self.assertFalse(providers.available({'providers': [entry]}, 'claude', 10)[0])

    def test_partial_failure_keeps_healthy_caller(self):
        data = {'providers': [self.entry(), {'provider': 'claude', 'status': 'unavailable'}]}
        result = providers.select({'caller': 'codex', 'provider': 'claude'}, data)
        self.assertEqual(result['provider'], 'codex')
        self.assertTrue(result['allowance_selection']['fallback'])

    def test_forecast_average_controls_selection(self):
        entry = self.entry(remaining=40)
        forecast = dict(status='ok', accountMatch='verified',
                        latestSampleAt=datetime.now(timezone.utc).isoformat(),
                        optimistic={'usedPercentAtReset': 90},
                        pessimistic={'usedPercentAtReset': 120})
        entry['windows'][0]['forecast'] = forecast
        self.assertFalse(providers.available({'providers': [entry]}, 'codex', 10)[0])
        forecast['pessimistic']['usedPercentAtReset'] = 100
        entry['windows'][0]['remainingPercent'] = 5
        self.assertTrue(providers.available({'providers': [entry]}, 'codex', 10)[0])
        entry['windows'][0]['remainingPercent'] = 1
        self.assertFalse(providers.available({'providers': [entry]}, 'codex', 10)[0])

    def test_caller_fallback_uses_live_reserve_when_both_forecasts_exhaust(self):
        entries = [self.entry('codex', 73), self.entry('claude', 15)]
        for entry in entries:
            entry['windows'][0]['forecast'] = dict(status='ok', accountMatch='verified',
                latestSampleAt=datetime.now(timezone.utc).isoformat(),
                optimistic={'usedPercentAtReset': 110}, pessimistic={'usedPercentAtReset': 190})
        entries[0]['windows'][0]['forecast']['optimistic']['usedPercentAtReset'] = 100
        result = providers.select({'caller': 'codex', 'provider': 'claude'}, {'providers': entries})
        self.assertEqual(result['provider'], 'codex')
        self.assertIn('projected', result['allowance_selection']['reason'])

    def test_both_low_choose_more_remaining_and_obey_hard_stop(self):
        data = {'providers': [self.entry('codex', 4), self.entry('claude', 7)]}
        result = providers.select({'caller': 'claude', 'provider': 'codex'}, data)
        self.assertEqual(result['provider'], 'claude')
        data['providers'][1]['windows'][0]['remainingPercent'] = 1
        self.assertEqual(providers.select({'caller': 'claude', 'provider': 'codex'}, data)['provider'], 'codex')

    def test_untrusted_forecast_uses_reserve(self):
        entry = self.entry(remaining=5)
        for status, match, sample in [('ok', 'unverified', datetime.now(timezone.utc).isoformat()),
                                     ('unavailable', 'verified', datetime.now(timezone.utc).isoformat()),
                                     ('ok', 'verified', '2020-01-01T00:00:00Z')]:
            entry['windows'][0]['forecast'] = dict(status=status, accountMatch=match,
                latestSampleAt=sample, optimistic={'usedPercentAtReset': 50},
                pessimistic={'usedPercentAtReset': 60})
            self.assertFalse(providers.available({'providers': [entry]}, 'codex', 10)[0])

    def test_usage_cache_is_shared_and_reuses_partial_results(self):
        with tempfile.TemporaryDirectory() as temporary:
            data = {'providers': [self.entry()]}
            with patch('devils_advocate.providers.Path.home', return_value=Path(temporary)), \
                 patch('devils_advocate.providers.fetch_allowance', return_value=data) as fetch:
                self.assertEqual(providers.allowance('/usage'), data)
                self.assertEqual(providers.allowance('/usage'), data)
                self.assertEqual(fetch.call_count, 1)

    def test_brrainztools_partial_exit_is_parsed(self):
        result = subprocess.CompletedProcess([], 1, json.dumps({'data': {'providers': [self.entry()]}}), '')
        with patch('devils_advocate.providers.subprocess.run', return_value=result) as run:
            self.assertEqual(len(providers.fetch_allowance('/fake/brrainztools')['providers']), 1)
            self.assertEqual(run.call_args.args[0], ['/fake/brrainztools', 'usage', 'all'])


class PrimingTest(unittest.TestCase):
    def test_skeptic_is_constructive_and_discounts_effort_estimates(self):
        role = (providers.ROOT / 'skeptic.md').read_text()
        self.assertIn('Do not manufacture objections', role)
        self.assertIn('Do not use "too much work"', role)
        self.assertIn('speculative schedules', role)
        self.assertIn('actual measurements', role)
        self.assertIn('Do not edit project files', role)
