"""Fresh-host natural-language routing proof using only the installed MCP."""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
from devils_advocate import providers


def run(log, callers=None):
    out=ROOT/'artifacts/host-smoke';out.mkdir(parents=True,exist_ok=True)
    installed=str(Path.home()/'.codex/mcp-servers/devils-advocate/devils-advocate')
    config=json.dumps({'mcpServers':{'devils-advocate':{'command':installed,'args':['--caller','claude']}}})
    prompt='''I am considering this policy: when a research call times out, assume no work ran and resend it immediately. Get the devil's advocate to challenge that idea. Use its actual MCP, not a simulated counterargument. Summarize the material correction and shut down the discussion afterward. Do not edit files or run experiments. No user questions are needed for this bounded review.'''
    commands={
        'claude':['claude','--strict-mcp-config','--mcp-config',config,
                  '--allowedTools','mcp__devils-advocate__*', '--permission-prompts','none',
                  '--model','opus','--effort','medium','--output-format','stream-json','--verbose','-p'],
        'codex':['codex','exec','--ignore-user-config','--ignore-rules','--skip-git-repo-check',
                 '--json','-m','gpt-6.1-sol','-c','model_reasoning_effort="medium"',
                 '-c','approval_policy="never"','-c','sandbox_mode="danger-full-access"',
                 '-c','mcp_servers.devils-advocate.command='+json.dumps(installed),
                 '-c','mcp_servers.devils-advocate.args=["--caller","codex"]',
                 '-c','mcp_servers.devils-advocate.tools.advocate_prepare.approval_mode="approve"',
                 '-c','mcp_servers.devils-advocate.tools.advocate_discuss.approval_mode="approve"',
                 '-c','mcp_servers.devils-advocate.tools.advocate_shutdown.approval_mode="approve"','-']}
    for caller,command in commands.items():
        if callers and caller not in callers: continue
        stdout=out/(caller+'.jsonl');stderr=out/(caller+'.stderr.log')
        with stdout.open('w') as dest,stderr.open('w') as err:
            process=subprocess.Popen(command,cwd=ROOT,stdin=subprocess.PIPE,stdout=dest,stderr=err,
                                     env=providers.environment(),text=True,start_new_session=True)
            try:process.communicate(prompt,timeout=240)
            except BaseException:
                import signal
                os.killpg(process.pid,signal.SIGTERM)
                try:process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid,signal.SIGKILL);process.wait()
                raise
        if process.returncode:raise RuntimeError(caller+' host failed; inspect '+str(stderr))
        raw=stdout.read_text()
        # Each provider's structured tool events include the invoked MCP tool names.
        events=[json.loads(line) for line in raw.splitlines()]
        calls=[]
        for event in events:
            if caller=='claude':
                for item in event.get('message',{}).get('content',[]):
                    if isinstance(item,dict) and item.get('type')=='tool_use':calls.append(item.get('name',''))
            else:
                item=event.get('item',{})
                if item.get('type')=='mcp_tool_call':calls.append(item.get('tool',''))
        assert any('advocate_prepare' in c for c in calls),caller+' did not prepare a real counter session'
        assert any('advocate_shutdown' in c for c in calls),caller+' did not shut down the discussion'
        log.write(caller+' actual tool calls: '+json.dumps(calls)+'\n');log.flush()
