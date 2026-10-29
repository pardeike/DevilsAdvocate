#!/usr/bin/env python3
"""Quiet local verification and delivery workflow."""
import argparse
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from devils_advocate.server import MCP


def command(args, log):
    process = subprocess.Popen(args, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        code = process.wait()
        if code:
            raise subprocess.CalledProcessError(code, args)
    except BaseException:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try: process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        raise


def deploy(log):
    files = [Path('main.py'), Path('defaults.json'), Path('skeptic.md')]
    files += list(Path('devils_advocate').glob('*.py'))
    files += list(Path('skills').rglob('*.md'))
    digest = hashlib.sha256(b''.join(str(p).encode() + (ROOT/p).read_bytes() for p in sorted(files))).hexdigest()[:16]
    install = Path.home()/'.codex/mcp-servers/devils-advocate'
    release = install/'releases'/digest
    release.mkdir(parents=True, exist_ok=True)
    for file in files:
        target=release/file
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT/file,target)
    launcher=install/'devils-advocate'
    # Absolute current release, no in-place replacement of code used by active workers.
    import shlex
    staging=install/('launcher.'+str(os.getpid())+'.tmp')
    staging.write_text('#!/bin/sh\nexec '+shlex.quote(sys.executable)+' '+shlex.quote(str(release/'main.py'))+' "$@"\n')
    staging.chmod(0o755)
    staging.replace(launcher)
    command(['/Users/ap/Scripts/mcp-local','register','devils-advocate',str(launcher),'--caller','codex'],log)
    # The requested low-friction discussion must also work with approval_policy=never.
    # Approve only these named tools on this installed server, not other MCPs.
    import tomllib
    config_path=Path.home()/'.codex/config.toml'
    source=config_path.read_text()
    settings=tomllib.loads(source)['mcp_servers']['devils-advocate']
    additions=[]
    for name in ['advocate_prepare','advocate_discuss','advocate_shutdown']:
        mode=settings.get('tools',{}).get(name,{}).get('approval_mode')
        if mode is not None and mode!='approve':
            raise RuntimeError('Preserve existing Codex approval override for '+name)
        if mode is None:
            additions.append('\n[mcp_servers.devils-advocate.tools.'+name+']\napproval_mode = "approve"\n')
    if additions:
        backup=ROOT/'artifacts/logs/codex-config-before-policy.toml'
        backup.write_text(source);backup.chmod(0o600)
        config_path.write_text(source+''.join(additions))

    existing=subprocess.run(['claude','mcp','get','devils-advocate'],capture_output=True,text=True)
    if existing.returncode==0:
        # Do not replace a differently owned/configured entry automatically.
        if str(launcher) not in existing.stdout or '--caller claude' not in existing.stdout:
            raise RuntimeError('Existing Claude registration differs; preserve it and inspect manually')
    else:
        command(['claude','mcp','add','--scope','user','devils-advocate','--',str(launcher),'--caller','claude'],log)
    for base in [Path.home()/'.codex/skills',Path.home()/'.claude/skills']:
        dest=base/'devils-advocate'
        source=ROOT/'skills/devils-advocate'
        if dest.is_symlink() and dest.resolve()==source: continue
        if dest.exists() or dest.is_symlink(): raise RuntimeError('Preserve unexpected existing skill '+str(dest))
        dest.symlink_to(source, target_is_directory=True)
    command(['/Users/ap/Scripts/mcp-local','check','devils-advocate'],log)
    command(['claude','mcp','get','devils-advocate'],log)
    # Probe both exact installed launch configurations over real stdio JSON-RPC.
    for caller in ['codex','claude']:
        messages=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2024-11-05','capabilities':{},'clientInfo':{'name':caller,'version':'test'}}},
                  {'jsonrpc':'2.0','method':'notifications/initialized'},
                  {'jsonrpc':'2.0','id':2,'method':'tools/list'},
                  {'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'advocate_status','arguments':{}}}]
        result=subprocess.run([str(launcher),'--caller',caller],input=''.join(json.dumps(m)+'\n' for m in messages),capture_output=True,text=True,check=True,timeout=15)
        responses={m['id']:m for m in map(json.loads,result.stdout.splitlines())}
        assert len(responses[2]['result']['tools'])==5
        assert responses[3]['result']['structuredContent']['caller']==caller
        log.write(result.stdout)


def smoke(log):
    # Real providers and real tmux. Temporary read-only evidence remains in project artifacts.
    work=ROOT/'artifacts/smoke'
    work.mkdir(parents=True,exist_ok=True)
    nonce=os.urandom(8).hex()
    (work/'evidence.txt').write_text('The experiment marker is '+nonce+'. The proposed design assumes a single client.\n')
    results=[]
    for caller in ['codex','claude']:
        mcp=MCP(caller)
        sid=None
        def call(name,args):
            result=mcp.handle('tools/call',{'name':name,'arguments':args})
            log.write(json.dumps(result)+'\n');log.flush()
            if result.get('isError'):raise RuntimeError(str(result))
            return result['structuredContent']
        def wait(r):
            deadline=time.monotonic()+330
            while r['phase']=='thinking':
                if time.monotonic()>deadline:raise RuntimeError('Smoke exceeded bounded wait')
                r=call('advocate_reply',{'session_id':r['session_id'],'wait_seconds':25})
            return r
        try:
            r=call('advocate_prepare',{'cwd':str(work),'topic':'Read evidence.txt independently. Quote its experiment marker, then challenge the single-client assumption in at most 100 words. Do not edit anything.','wait_seconds':0})
            sid=r['session_id'];r=wait(r)
            assert nonce in r['reply'], 'Partner did not independently read the evidence file'
            native=r['native_session_id']
            r=wait(call('advocate_discuss',{'message':'Without re-reading the file, repeat the experiment marker you found earlier. Now suppose two clients can arrive concurrently. Revise your recommendation in at most 100 words.','wait_seconds':0}))
            assert nonce in r['reply'] and r['native_session_id']==native,'Native context was not retained'
            results.append(r)
        finally:
            if sid:call('advocate_shutdown',{'session_id':sid})
    (work/'results.json').write_text(json.dumps(results,indent=2))
    assert (work/'evidence.txt').read_text().startswith('The experiment marker is '+nonce)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['test','deploy','smoke','host-smoke'])
    parser.add_argument('--host', choices=['codex','claude'], help='Limit host-smoke to one host')
    args=parser.parse_args()
    os.chdir(ROOT)
    def cancelled(_signal, _frame):
        raise KeyboardInterrupt()
    signal.signal(signal.SIGTERM, cancelled)
    logs=ROOT/'artifacts/logs';logs.mkdir(parents=True,exist_ok=True)
    path=logs/(args.action+'.log')
    try:
        with path.open('w') as log:
            if args.action in ['test','deploy']:
                command([sys.executable,'-m','unittest','discover','-s','tests','-v'],log)
            if args.action=='deploy':deploy(log)
            if args.action=='smoke':smoke(log)
            if args.action=='host-smoke':
                from host_smoke import run
                run(log, [args.host] if args.host else None)
        print('ok')
    except KeyboardInterrupt:
        print(f'{args.action} cancelled; full log: {path}', file=sys.stderr)
        return 130
    except Exception as error:
        print(f'{args.action} failed: {error}\nFull log: {path}',file=sys.stderr)
        return 1
    return 0


if __name__=='__main__':
    raise SystemExit(main())
