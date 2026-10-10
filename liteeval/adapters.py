"""HTTP mapping and adapter registry."""
import asyncio
import json
import math
import re
import socket
import time
import urllib.error
from dataclasses import asdict
from typing import Any
from .core import AgentAdapter, AgentRun, EvalCase, Event
from .security import checked_url, secret_from_env, safe_error, redact
from .transport import post_json, HTTPFailure
from .storage import uid

PATH=re.compile(r'(?:\.[A-Za-z_][A-Za-z0-9_]*|\[\d+\])')

def select(value: Any,path: str) -> Any:
    if path in {'$', ''}:return value
    if not isinstance(path,str):raise ValueError('Field path must be a string')
    rest=path[1:] if path.startswith('$') else '.'+path
    if ''.join(PATH.findall(rest))!=rest:raise ValueError('Unsupported field path')
    for token in PATH.findall(rest):
        if token.startswith('.'):
            if not isinstance(value,dict):raise KeyError(path)
            value=value[token[1:]]
        else:
            if not isinstance(value,list):raise KeyError(path)
            value=value[int(token[1:-1])]
    return value

def map_template(template,context):
    if isinstance(template,str) and template.startswith('$'):
        return select(context,template)
    if isinstance(template,dict):return {k:map_template(v,context) for k,v in template.items()}
    if isinstance(template,list):return [map_template(v,context) for v in template]
    return template

class HTTPAdapter:
    def __init__(self,config,run_id=None):
        self.run_id=run_id
        self.config=config
        self.endpoint=checked_url(config['endpoint'])
        self.timeout=float(config.get('timeout_seconds',30))

    async def invoke(self,case: EvalCase) -> AgentRun:
        started=time.monotonic();run=AgentRun(self.run_id or uid(),case.id,'failed')
        conf=self.config
        try:
            headers=dict(conf.get('headers',{}))
            for name,ref in conf.get('headers_env',{}).items():headers[name]=secret_from_env(ref)
            auth=conf.get('auth')
            if auth:
                if auth.get('type')!='bearer':raise ValueError('Only bearer auth supported')
                headers['Authorization']='Bearer '+secret_from_env(auth['token_env'])
            # Stable across retries/recovery. The external Agent must implement deduplication.
            headers['Idempotency-Key']=run.id
            context={'input':case.input,'expected':case.expected,'metadata':case.metadata,'case':asdict(case)}
            body=map_template(conf.get('request_template',{'input':'$.input'}),context)
            raw=await post_json(self.endpoint,body,headers,self.timeout)
            run.raw_response=redact(raw)
            run.output=select(raw,conf.get('output_path','answer'))
            events=select(raw,conf['events_path']) if conf.get('events_path') else raw.get('events',[])
            if not isinstance(events,list):raise ValueError('events must be an array')
            run.events=[Event.from_dict(e,i) for i,e in enumerate(events)]
            complete=bool(raw.get('trajectory_complete',False))
            run.capture_level='full_harness' if complete else 'reported_spans' if run.events else 'output_only'
            run.usage=select(raw,conf.get('usage_path','usage')) if ('usage_path' in conf or 'usage' in raw) else {}
            if not isinstance(run.usage,dict):raise ValueError('usage must be an object')
            run.cost=select(raw,conf.get('cost_path','cost')) if ('cost_path' in conf or 'cost' in raw) else None
            run.currency=conf.get('currency') or raw.get('currency')
            if run.cost is not None and (type(run.cost) not in (int,float) or not math.isfinite(run.cost) or run.cost<0):raise ValueError('cost must be nonnegative')
            run.status='succeeded'
        except HTTPFailure as exc:
            run.error=safe_error(exc);run.retryable=exc.retryable;run.raw_response={'status':exc.status,'body':exc.raw}
        except (asyncio.TimeoutError,TimeoutError,socket.timeout) as exc:
            run.status='timed_out';run.error=safe_error(exc);run.retryable=True
        except urllib.error.URLError as exc:
            run.error=safe_error(exc);run.retryable=True
        except Exception as exc:
            run.error=safe_error(exc)
        run.latency_ms=int((time.monotonic()-started)*1000)
        return run

def make_adapter(config,environment=None,run_id=None):
    if config['type']=='http':
        return HTTPAdapter(config,run_id)
    if config['type']=='codex':
        from .codex import CodexAdapter
        return CodexAdapter(config,environment)
    raise ValueError('Unknown Agent adapter')
