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
from liteeval.domain.contracts import AgentAdapter, AgentRun, EvalCase, Event
from liteeval.infrastructure.security.secrets import checked_url, secret_from_env, safe_error, redact
from liteeval.infrastructure.network.http import post_json, HTTPFailure
from liteeval.domain.values import uid

from liteeval.domain.paths import select

def map_template(template,context):
    if isinstance(template,str) and template.startswith('$'):
        return select(context,template)
    if isinstance(template,dict):return {k:map_template(v,context) for k,v in template.items()}
    if isinstance(template,list):return [map_template(v,context) for v in template]
    return template

class HTTPAdapter:
    def __init__(self,config,run_id=None,store=None):
        self.run_id=run_id
        self.store=store
        self.config=config
        self.endpoint=checked_url(config['endpoint'])
        self.timeout=float(config.get('timeout_seconds',30))

    async def invoke(self,case: EvalCase) -> AgentRun:
        started=time.monotonic();run=AgentRun(self.run_id or uid(),case.id,'failed')
        conf=self.config
        try:
            headers=dict(conf.get('headers',{}))
            if conf.get('connection_id'):
                from liteeval.application.connections import api_key
                key=api_key(self.store,conf['connection_id'])
                if key:headers['Authorization']='Bearer '+key
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

def make_adapter(config,environment=None,run_id=None,store=None):
    if config['type']=='http':
        return HTTPAdapter(config,run_id,store)
    if config['type']=='codex':
        from liteeval.integrations.agents.codex import CodexAdapter
        return CodexAdapter(config,environment)
    raise ValueError('Unknown Agent adapter')
