"""Thin opt-in SDK for sync/async functions and explicitly instrumented spans."""
import contextvars
from contextlib import contextmanager
from dataclasses import asdict
import functools
import inspect
import time
import threading
from .core import Event,ContentPart
from .security import redact
from .transport import request_json

_current=contextvars.ContextVar('liteeval_trace',default=None)
_parent=contextvars.ContextVar('liteeval_span_parent',default=None)

class TraceClient:
    def __init__(self,base_url,project_id,token,fail_open=True,log_content=True):
        self.base_url,self.project_id,self.token=base_url.rstrip('/'),project_id,token
        self.fail_open,self.log_content=fail_open,log_content
        self.last_error=None
    def send(self,input,output,events=None,status='succeeded',metadata=None):
        payload={'project_id':self.project_id,'input':redact(input) if self.log_content else {},
                 'output':redact(output) if self.log_content else None,'events':events or [],'status':status,
                 'capture_level':'reported_spans' if events else 'output_only','metadata':metadata or {}}
        try:
            return request_json(self.base_url+'/v1/traces',payload,{'Authorization':'Bearer '+self.token},timeout=5)
        except Exception as exc:
            self.last_error=type(exc).__name__
            if not self.fail_open:raise
            return None
    def trace(self,input_mapper=None,agent_version=None,model=None):
        def decorate(fn):
            def start(args,kwargs):
                bound=dict(inspect.signature(fn).bind(*args,**kwargs).arguments)
                input=input_mapper(bound) if input_mapper else bound
                return input,{'events':[],'lock':threading.Lock()},time.time()
            def finish(input,state,started,output,status):
                self.send(input,output,state['events'],status,{'agent_version':agent_version,'model':model,'started_at':started,'ended_at':time.time()})
            if inspect.iscoroutinefunction(fn):
                @functools.wraps(fn)
                async def async_wrap(*args,**kwargs):
                    input,state,started=start(args,kwargs);token=_current.set(state);output=None;status='failed'
                    try:output=await fn(*args,**kwargs);status='succeeded';return output
                    finally:
                        _current.reset(token)
                        # Reporting never blocks the event loop and defaults to fail-open.
                        import asyncio
                        await asyncio.to_thread(finish,input,state,started,output,status)
                return async_wrap
            @functools.wraps(fn)
            def wrap(*args,**kwargs):
                input,state,started=start(args,kwargs);token=_current.set(state);output=None;status='failed'
                try:output=fn(*args,**kwargs);status='succeeded';return output
                finally:_current.reset(token);finish(input,state,started,output,status)
            return wrap
        return decorate
    @contextmanager
    def span(self,kind,name,input=None,attributes=None):
        state=_current.get();event=Event(kind,name,started_at=time.time(),attributes=redact(attributes or {}))
        parent_token=_parent.set(event.event_id)
        if state:
            event.parent_id=parent_token.old_value if isinstance(parent_token.old_value,str) else None
            with state['lock']:
                event.sequence=len(state['events']);state['events'].append(None);index=event.sequence
        if input is not None and self.log_content:event.input_parts=[ContentPart('json',json_value=redact(input))]
        try:yield event
        except BaseException:event.status='failed';raise
        finally:
            event.ended_at=time.time()
            if not self.log_content:event.input_parts=[];event.output_parts=[]
            if state:state['events'][index]=redact(asdict(event))
            _parent.reset(parent_token)
