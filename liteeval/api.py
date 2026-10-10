"""Authenticated JSON API; reusable services stay independent of the transport."""
import base64
import hmac
import json
import os
import re
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import parse_qs,urlparse
from dataclasses import dataclass
from .reports import compare
from .security import safe_error
from .storage import Conflict,NotFound,Store,encoded

MAX_BODY=8_000_000

@dataclass
class BinaryResponse:
    content:bytes
    mime_type:str

class Unauthorized(Exception):pass
class Forbidden(Exception):pass

def authenticate(store,headers,admin_token):
    token=(headers.get('Authorization','') or '').removeprefix('Bearer ')
    if admin_token and hmac.compare_digest(token,admin_token):return {'admin':True,'project_id':None}
    project=store.authenticate(token)
    if project:return {'admin':False,'project_id':project}
    raise Unauthorized('Bearer token required')

def dispatch(store,method,path,body=None,principal=None,headers=None):
    # principal=None is only for trusted in-process calls, not the HTTP handler.
    principal=principal or {'admin':True,'project_id':None}
    body=body or {};headers=headers or {};parsed=urlparse(path);route=parsed.path.rstrip('/')
    query={k:v[0] for k,v in parse_qs(parsed.query).items()}
    def admin():
        if not principal['admin']:raise Forbidden('Admin token required for configuration changes')
    def scope(project_id):
        if not principal['admin'] and principal['project_id']!=project_id:raise Forbidden('Project access denied')
    def resource(table,id_):
        with store.connect() as db:
            row=dict(store.required(db,table,id_))
            if table=='projects':project_id=row['id']
            elif table=='cases':project_id=store.required(db,'datasets',row['dataset_id'])['project_id']
            elif table=='runs':project_id=store.required(db,'experiments',row['experiment_id'])['project_id']
            else:project_id=row['project_id']
            scope(project_id);return row
    try:
        if not isinstance(body,dict):raise ValueError('JSON object required')
        if method=='GET' and route=='/v1/health':return 200,{'status':'ok','schema_version':2}
        lists={'projects':'projects','datasets':'datasets','traces':'traces','agent-versions':'agent_versions',
               'evaluators':'evaluator_revisions','experiments':'experiments','artifacts':'artifacts'}
        if method=='GET' and route.removeprefix('/v1/') in lists:
            table=lists[route.removeprefix('/v1/')];pid=query.get('project_id') or principal['project_id']
            if pid:scope(pid)
            filters={k:query[k] for k in ['status','kind','name','label'] if k in query}
            return 200,store.list_items(table,pid,query.get('limit',50),query.get('offset',0),filters)
        if method=='POST' and route=='/v1/projects':
            admin();return 201,store.create_project(body['name'])
        match=re.fullmatch(r'/v1/projects/([0-9a-f]{32})/api-keys',route)
        if match and method=='POST':
            admin();resource('projects',match[1]);return 201,store.issue_key(match[1],body.get('label','client'))
        match=re.fullmatch(r'/v1/api-keys/([0-9a-f]{32})/revoke',route)
        if match and method=='POST':
            admin();return 200,store.revoke_key(match[1])
        if method=='POST' and route=='/v1/datasets':
            scope(body['project_id']);return 201,store.create_dataset(body['project_id'],body['name'])
        if method=='POST' and route=='/v1/traces':
            scope(body['project_id'])
            if not isinstance(body['input'],dict):raise ValueError('Trace input must be an object')
            return 201,store.create_trace(body['project_id'],body['input'],body.get('output'),body.get('events'),
                body.get('status','succeeded'),body.get('capture_level','output_only'),metadata=body.get('metadata'))
        match=re.fullmatch(r'/v1/datasets/([0-9a-f]{32})/(cases|import|export|cases/from-trace)',route)
        if match:
            did,action=match.groups();resource('datasets',did)
            if method=='POST' and action=='import':return 201,store.import_cases(did,body.get('records'),body.get('csv_text'))
            if method=='GET' and action=='export':return 200,store.export_cases(did)
            if method=='GET' and action=='cases':return 200,store.list_cases(did,query.get('limit',50),query.get('offset',0),query.get('category'),query.get('severity'))
            if method=='POST' and action=='cases/from-trace':
                resource('traces',body['trace_id'])
                return 201,store.case_from_trace(did,body['trace_id'],body.get('expected',body.get('expected_output')),body.get('metadata'))
            if method=='POST' and action=='cases':
                if not isinstance(body['input'],dict):raise ValueError('Case input must be an object')
                return 201,store.add_case(did,body['input'],body.get('expected',body.get('expected_output')),body.get('metadata'))
        match=re.fullmatch(r'/v1/cases/([0-9a-f]{32})/revisions',route)
        if match and method=='POST':
            resource('cases',match[1])
            if not isinstance(body['input'],dict):raise ValueError('Case input must be an object')
            return 201,store.revise_case(match[1],body['input'],body.get('expected',body.get('expected_output')),body.get('metadata'))
        if method=='POST' and route=='/v1/agent-versions':
            admin();scope(body['project_id']);return 201,store.add_agent(body['project_id'],body['label'],body['config'])
        if method=='POST' and route=='/v1/evaluators':
            admin();scope(body['project_id']);return 201,store.add_evaluator(body['project_id'],body['name'],body['kind'],body.get('config',{}))
        if method=='POST' and route=='/v1/experiments':
            scope(body['project_id'])
            return 201,store.create_experiment(body['project_id'],body['dataset_id'],body['agent_version_id'],body['evaluator_ids'],body.get('config'),headers.get('Idempotency-Key'))
        match=re.fullmatch(r'/v1/experiments/([0-9a-f]{32})/(cancel|results|rerun)',route)
        if match:
            resource('experiments',match[1])
            if method=='POST' and match[2]=='cancel':return 200,store.cancel(match[1])
            if method=='GET' and match[2]=='results':return 200,store.get_experiment(match[1])
            if method=='POST' and match[2]=='rerun':return 201,store.rerun(match[1],body.get('agent_version_id'))
        if method=='GET' and route=='/v1/compare':
            resource('experiments',query['left']);resource('experiments',query['right'])
            return 200,compare(store,query['left'],query['right'])
        match=re.fullmatch(r'/v1/artifacts/([0-9a-f]{32})/content',route)
        if match and method=='GET':
            resource('artifacts',match[1]);info,data=store.read_artifact(match[1]);return 200,BinaryResponse(data,info['mime_type'])
        match=re.fullmatch(r'/v1/(projects|datasets|traces|cases|agent-versions|evaluators|experiments|runs|artifacts)/([0-9a-f]{32})',route)
        if match and method=='GET':
            name,id_=match.groups();table=lists.get(name,name);row=resource(table,id_)
            if name=='traces':return 200,store.get_trace(id_)
            if name=='runs':return 200,store.get_run(id_)
            if name=='experiments':return 200,store.get_experiment(id_)
            if name=='cases':
                with store.connect() as db:
                    return 200,json.loads(db.execute('SELECT content_json FROM case_revisions WHERE case_id=? AND revision=?',(id_,row['revision'])).fetchone()[0])
            return 200,row
        return 404,{'error':'not_found'}
    except Forbidden as exc:return 403,{'error':'forbidden','message':str(exc)}
    except Conflict as exc:return 409,{'error':'conflict','message':str(exc)}
    except NotFound as exc:return 404,{'error':'not_found','message':str(exc)}
    except (KeyError,ValueError,TypeError,IndexError) as exc:return 400,{'error':'invalid_request','message':safe_error(exc)}


def make_handler(store,admin_token=None):
    admin_token=admin_token or os.getenv('LITEEVAL_ADMIN_TOKEN')
    class Handler(BaseHTTPRequestHandler):
        def respond(self,method):
            try:
                if self.path.rstrip('/')=='/v1/health' and method=='GET':
                    principal={'admin':False,'project_id':None}
                else:principal=authenticate(store,self.headers,admin_token)
                length=int(self.headers.get('Content-Length','0'))
                if not 0<=length<=MAX_BODY:
                    self.send_error(413);return
                self.connection.settimeout(15)
                data=self.rfile.read(length)
                if len(data)!=length:raise ValueError('Incomplete request body')
                body=json.loads(data) if data else {}
                status,result=dispatch(store,method,self.path,body,principal,dict(self.headers))
            except Unauthorized as exc:status,result=401,{'error':'unauthorized','message':str(exc)}
            except (ValueError,UnicodeDecodeError,TimeoutError) as exc:status,result=400,{'error':'invalid_request','message':safe_error(exc)}
            except Exception:
                status,result=500,{'error':'internal_error','message':'Request failed; consult Worker/service logs'}
            if isinstance(result,BinaryResponse):payload=result.content;mime=result.mime_type
            else:payload=json.dumps(result,ensure_ascii=False,allow_nan=False).encode();mime='application/json; charset=utf-8'
            self.send_response(status);self.send_header('Content-Type',mime)
            self.send_header('Content-Length',str(len(payload)));self.send_header('X-Content-Type-Options','nosniff')
            self.end_headers();self.wfile.write(payload)
        def do_GET(self):self.respond('GET')
        def do_POST(self):self.respond('POST')
        def log_message(self,*args):pass  # Query strings and trace contents may contain sensitive data.
    return Handler

def serve(store,host='127.0.0.1',port=8765):
    if host not in {'127.0.0.1','localhost'}:raise ValueError('Bind to loopback; use a TLS proxy for remote access')
    token=os.getenv('LITEEVAL_ADMIN_TOKEN','')
    if len(token)<24:raise ValueError('Set LITEEVAL_ADMIN_TOKEN to a random secret of at least 24 characters')
    httpd=ThreadingHTTPServer((host,port),make_handler(store,token))
    print(f'LiteEval API listening at http://{host}:{port}',flush=True)
    try:httpd.serve_forever()
    finally:httpd.server_close()
