"""Codex exec --json adapter. Raw JSONL survives parser updates."""
import json
import os
import sys
import time
from dataclasses import asdict
from pathlib import Path
import shutil
from liteeval.domain.contracts import AgentRun, ContentPart, Event
from liteeval.infrastructure.security.secrets import secret_from_env, safe_error
from liteeval.domain.values import uid


def parse_jsonl(stdout):
    events={};order=[];output=None;usage={};partial=False;failed=False;turn_usage=[]
    for line in stdout.splitlines():
        try:data=json.loads(line)
        except ValueError:partial=True;continue
        if not isinstance(data,dict):partial=True;continue
        kind=data.get('type','unknown')
        if kind=='turn.completed':turn_usage.append(data.get('usage',{}));failed=False
        if kind in {'error','turn.failed'}:failed=True
        item=data.get('item')
        if isinstance(item,dict):
            key=item.get('id',uid());itype=item.get('type','custom')
            if key not in events:order.append(key)
            event=events.get(key) or Event('custom',itype,event_id=str(key),sequence=len(order)-1)
            event.attributes={'source':'codex','raw_item':item}
            event.status='running' if kind=='item.started' else item.get('status','succeeded')
            if itype=='command_execution':
                event.kind='command';event.name='shell'
                event.input_parts=[ContentPart('json',json_value={'command':item.get('command')})]
                event.output_parts=[ContentPart('text',text=item.get('aggregated_output',''))]
                event.attributes.update({'command':item.get('command'),'exit_code':item.get('exit_code')})
            elif itype=='agent_message':
                event.kind='message';event.name='agent';event.output_parts=[ContentPart('text',text=item.get('text',''))]
                if kind=='item.completed':output=item.get('text','')
            elif itype=='mcp_tool_call':
                event.kind='tool';event.name=item.get('tool','mcp')
                event.attributes['arguments']=item.get('arguments',{})
                event.input_parts=[ContentPart('json',json_value=item.get('arguments',{}))]
                event.output_parts=[ContentPart('json',json_value=item.get('result'))]
            elif itype in {'web_search','web_search_call'}:
                event.kind='tool';event.name='web_search'
                event.input_parts=[ContentPart('json',json_value=item.get('query',item.get('action')))]
                event.output_parts=[ContentPart('json',json_value=item.get('results'))]
            elif itype=='error':
                event.kind='error';event.status='failed'
            elif itype=='file_change':
                event.kind='file_operation';event.name='patch';event.output_parts=[ContentPart('json',json_value=item.get('changes',[]))]
            events[key]=event
        else:
            key=uid();order.append(key);events[key]=Event('error' if kind in {'error','turn.failed'} else 'custom',kind,attributes=data,sequence=len(order)-1,event_id=key)
    if turn_usage:
        from liteeval.application.usage import sum_turns
        usage=sum_turns(turn_usage)
    return {'events':[events[k] for k in order],'output':output,'usage':usage,'partial':partial,'failed':failed}

def launcher_for(executable):
    if executable.lower().endswith('.py'):return [sys.executable,executable]
    if os.name=='nt' and not executable.lower().endswith('.exe'):
        # Avoid cmd /c and its shell interpolation when npm exposes codex.cmd.
        wrapper=shutil.which(executable if executable.lower().endswith('.cmd') else executable+'.cmd')
        if wrapper:
            script=Path(wrapper).parent/'node_modules/@openai/codex/bin/codex.js'
            node=shutil.which('node')
            if script.is_file() and node:return [node,str(script)]
    return [executable]


class CodexAdapter:
    def __init__(self,config,environment):self.config=config;self.environment=environment
    async def invoke(self,case):
        start=time.monotonic();run=AgentRun(uid(),case.id,'failed')
        try:
            if not self.environment or not hasattr(self.environment,'execute'):raise ValueError('Codex needs an execution environment')
            executable=os.getenv('LITEEVAL_CODEX_BINARY','codex')
            if self.config.get('environment',{}).get('type')=='docker':executable='codex'
            # Codex's own sandbox remains active; never silently disable it.
            launcher=launcher_for(executable)
            args=launcher+['exec','--json','--sandbox','workspace-write','--skip-git-repo-check']
            if self.config.get('ignore_user_config'):args+=['--ignore-user-config']
            if self.config.get('ephemeral'):args+=['--ephemeral']
            if self.config.get('web_search'):args+=['-c','web_search='+json.dumps(self.config['web_search'])]
            if self.config.get('model'):args+=['--model',self.config['model']]
            if self.config.get('reasoning_effort'):
                effort=self.config['reasoning_effort']
                if effort not in {'low','medium','high','xhigh'}:raise ValueError('Unsupported reasoning effort')
                args+=['-c',f'model_reasoning_effort="{effort}"']
            args+=['-']
            names=[] if self.config.get('environment',{}).get('type')=='docker' else ['PATH','HOME','USERPROFILE','APPDATA','LOCALAPPDATA','SYSTEMROOT','WINDIR','TEMP','TMP','COMSPEC','PATHEXT','CODEX_HOME']
            if self.config.get('inherit_proxy'):names+=['HTTP_PROXY','HTTPS_PROXY','ALL_PROXY','NO_PROXY','http_proxy','https_proxy','all_proxy','no_proxy']
            env={k:os.environ[k] for k in names if k in os.environ}
            if self.config.get('environment',{}).get('type')=='docker':env.pop('HOME',None)
            if self.config.get('api_key_env'):env['CODEX_API_KEY']=secret_from_env(self.config['api_key_env'])
            result=await self.environment.execute(args,timeout=float(self.config.get('timeout_seconds',120)),stdin=case.input.get('prompt',case.input.get('question','')),env=env)
            parsed=parse_jsonl(result['stdout'])
            run.raw_response={'stdout_jsonl':result['stdout'],'stderr':result['stderr'],'exit_code':result['exit_code']}
            run.events=parsed['events'];run.output=parsed['output'];run.usage=parsed['usage']
            run.capture_level='reported_spans' if parsed['partial'] else 'full_harness'
            run.status='timed_out' if result['timed_out'] else 'succeeded' if result['exit_code']==0 and not parsed['failed'] and run.output is not None else 'failed'
            if run.status!='succeeded':run.error='Codex execution failed or final output missing'
        except Exception as exc:run.error=safe_error(exc)
        run.latency_ms=int((time.monotonic()-start)*1000)
        return run
