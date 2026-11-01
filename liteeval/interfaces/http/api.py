"""Authenticated JSON API; reusable services stay independent of the transport."""
import hmac
import csv
import io
import json
import os
import re
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import parse_qs,urlparse
from dataclasses import dataclass
from liteeval.application.reports import compare
from liteeval.infrastructure.security.secrets import safe_error
from liteeval.domain.values import encoded
from liteeval.domain.errors import Conflict, NotFound
from liteeval.infrastructure.persistence.store import Store
from liteeval.infrastructure.persistence.migrations import SCHEMA_VERSION
from liteeval.interfaces.http.sessions import AuthError, BrowserSessions, cookie_header, cookie_token, csrf_token, check_origin, normalize_origin
from liteeval.application import previews as previews; from liteeval.infrastructure.persistence import queries as web_queries; from liteeval.application import catalog as catalog; from liteeval.application import planning as planning; from liteeval.infrastructure.execution import workers as runtime; from liteeval import __version__
from liteeval.application.imports import validate_batch, BatchValidationError
from liteeval.application import connections as connections; from liteeval.application import rollouts as rollouts

MAX_BODY=8_000_000

@dataclass
class BinaryResponse:
    content:bytes
    mime_type:str
    filename:str='artifact'

class Unauthorized(Exception):pass
class Forbidden(Exception):pass

def authenticate(store,headers,admin_token):
    token=(headers.get('Authorization','') or '').removeprefix('Bearer ')
    if admin_token and hmac.compare_digest(token.encode(),admin_token.encode()):return {'admin':True,'project_id':None}
    project=store.authenticate(token)
    if project:return {'admin':False,'project_id':project}
    raise Unauthorized('Bearer token required')

def dispatch(store,method,path,body=None,principal=None,headers=None):
    # principal=None is only for trusted in-process calls, not the HTTP handler.
    principal=principal or {'admin':True,'project_id':None}
    body={} if body is None else body
    headers={k.lower():v for k,v in (headers or {}).items()};parsed=urlparse(path);route=parsed.path.rstrip('/')
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
        if method=='GET' and route=='/v1/health':return 200,{'status':'ok','schema_version':SCHEMA_VERSION,'version':__version__}
        if method=='GET' and route=='/v1/runtime/status':
            admin();return 200,runtime.status(store)
        if route == '/v1/benchmark-releases' and method == 'GET':
            from liteeval.bootstrap import benchmark_service
            pid = query.get('project_id') or principal['project_id']
            if not pid: raise ValueError('project_id required')
            scope(pid)
            return 200, {'items': benchmark_service(store).repository.list_releases(pid)}
        match = re.fullmatch(r'/v1/benchmark-executions/([0-9a-f]{32})', route)
        if match and method == 'GET':
            from liteeval.bootstrap import benchmark_service
            pid = query.get('project_id') or principal['project_id']
            if not pid: raise ValueError('project_id required')
            scope(pid)
            return 200, benchmark_service(store).repository.execution(pid, match[1])
        if route=='/v1/model-connections':
            if method=='POST':
                scope(body['project_id']);return 201,connections.create(store,body)
            if method=='GET':
                pid=query.get('project_id') or principal['project_id']
                if not pid:raise ValueError('project_id required')
                scope(pid);return 200,connections.list_connections(store,pid,query.get('limit',50),query.get('offset',0),query.get('archived','exclude'))
        match=re.fullmatch(r'/v1/model-connections/([0-9a-f]{32})',route)
        if match:
            resource('model_connections',match[1])
            if method=='GET':return 200,connections.get(store,match[1])
            if method=='PATCH':return 200,connections.update(store,match[1],body)
        match=re.fullmatch(r'/v1/(experiments|runs)/([0-9a-f]{32})/(statistics|export|events)',route)
        if match and method=='GET':
            kind,id_,action=match.groups();resource(kind,id_)
            args={'experiment_id':id_} if kind=='experiments' else {'run_id':id_}
            if action=='events':
                if kind!='runs':raise ValueError('Events require a Run')
                return 200,rollouts.events(store,id_,query.get('after',0),query.get('limit',200))
            if action=='statistics':return 200,rollouts.get_statistics(store,**args)
            format=query.get('format','json');content,mime=rollouts.export(store,**args,format=format)
            return 200,BinaryResponse(content,mime,f'{kind}-{id_}.{format}')
        if method=='POST' and route=='/v1/experiments/preflight':
            scope(body['project_id']);return 200,planning.preflight(store,body)
        match=re.fullmatch(r'/v1/datasets/([0-9a-f]{32})/import-preview',route)
        if match and method=='POST':
            dataset=resource('datasets',match[1]);store.active(dataset)
            if set(body)-{'records','csv_text'}:raise ValueError('Provide records or csv_text')
            _,report=validate_batch(store.validate_case,body.get('records'),body.get('csv_text'))
            return 200,report
        match=re.fullmatch(r'/v1/datasets/([0-9a-f]{32})/versions',route)
        if match:
            resource('datasets',match[1])
            if method=='GET':return 200,catalog.list_versions(store,match[1],query.get('limit',50),query.get('offset',0))
            if method=='POST':
                if set(body)-{'name','description'}:raise ValueError('Dataset versions support name and description')
                return 201,catalog.publish_dataset(store,match[1],body['name'],body.get('description',''))
        match=re.fullmatch(r'/v1/dataset-versions/([0-9a-f]{32})(/cases)?',route)
        if match and method=='GET':
            resource('dataset_versions',match[1])
            if match[2]:return 200,catalog.version_cases(store,match[1],query.get('limit',50),query.get('offset',0))
            return 200,catalog.get_version(store,match[1])
        match=re.fullmatch(r'/v1/evaluators/([0-9a-f]{32})/revisions',route)
        if match:
            resource('evaluator_revisions',match[1])
            if method=='GET':return 200,catalog.list_revisions(store,match[1],query.get('limit',50),query.get('offset',0))
            if method=='POST':
                admin()
                if set(body)-{'name','config','base_revision'}:raise ValueError('A revision supports name, config and base_revision; create a separate evaluator to change kind')
                return 201,catalog.revise_evaluator(store,match[1],body['config'],body['base_revision'],body.get('name'))
        if method=='POST' and route=='/v1/evaluator-previews':
            admin();scope(body['project_id'])
            return 202,previews.enqueue(store,body)
        match=re.fullmatch(r'/v1/evaluator-previews/([0-9a-f]{32})',route)
        if match and method=='GET':
            result=previews.get_preview(store,match[1]);scope(result['project_id']);return 200,result
        match=re.fullmatch(r'/v1/experiments/([0-9a-f]{32})/(progress|runs)',route)
        if match and method=='GET':
            resource('experiments',match[1])
            if match[2]=='progress':return 200,web_queries.progress(store,match[1])
            if query.get('include_output','false') not in {'true','false'}:raise ValueError('include_output must be true or false')
            return 200,web_queries.paginated_runs(store,match[1],query.get('limit',50),query.get('offset',0),query.get('status'),query.get('outcome'),query.get('include_output')=='true')
        lists={'projects':'projects','datasets':'datasets','traces':'traces','agent-versions':'agent_versions',
               'evaluators':'evaluator_revisions','experiments':'experiments','artifacts':'artifacts'}
        if method=='GET' and route.removeprefix('/v1/') in lists:
            table=lists[route.removeprefix('/v1/')];pid=query.get('project_id') or principal['project_id']
            if pid:scope(pid)
            filters={k:query[k] for k in ['status','kind','name','label'] if k in query}
            if query.get('latest','false') not in {'true','false'}:raise ValueError('latest must be true or false')
            return 200,store.list_items(table,pid,query.get('limit',50),query.get('offset',0),filters,query.get('archived','exclude'),query.get('latest')=='true')
        match=re.fullmatch(r'/v1/(datasets|cases|evaluators)/([0-9a-f]{32})',route)
        if match and method=='PATCH':
            table=lists.get(match[1],match[1]);resource(table,match[2])
            if table=='evaluator_revisions':admin()
            return 200,store.update_resource(table,match[2],body)
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
            if method=='GET' and action=='export':
                result=store.export_cases(did,query.get('archived','exclude'))
                if query.get('format','json')=='csv':
                    output=io.StringIO(newline='');writer=csv.writer(output);writer.writerow(['input','expected','metadata'])
                    for record in result['records']:writer.writerow([encoded(record[field]) for field in ('input','expected','metadata')])
                    return 200,BinaryResponse(output.getvalue().encode('utf-8-sig'),'text/csv; charset=utf-8','cases.csv')
                if query.get('format','json')!='json':raise ValueError('format must be json or csv')
                return 200,result
            if method=='GET' and action=='cases':return 200,store.list_cases(did,query.get('limit',50),query.get('offset',0),query.get('category'),query.get('severity'),query.get('archived','exclude'))
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
            if 'base_revision' in body and body['base_revision'] is None:raise ValueError('base_revision must be a positive integer')
            return 201,store.revise_case(match[1],body['input'],body.get('expected',body.get('expected_output')),body.get('metadata'),body.get('base_revision'))
        if method=='POST' and route=='/v1/agent-versions':
            admin();scope(body['project_id']);return 201,store.add_agent(body['project_id'],body['label'],body['config'])
        if method=='POST' and route=='/v1/evaluators':
            admin();scope(body['project_id']);return 201,store.add_evaluator(body['project_id'],body['name'],body['kind'],body.get('config',{}))
        if method=='POST' and route=='/v1/experiments':
            scope(body['project_id'])
            request=planning.normalize(body)
            return 201,store.create_experiment(request['project_id'],request['dataset_id'],request['agent_version_id'],request['evaluator_ids'],request['config'],headers.get('idempotency-key'),
                                               dataset_version_id=request['dataset_version_id'],name=request['name'],description=request['description'],
                                               diagnostic=request['diagnostic'],preflight_hash=request['preflight_hash'])
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
                    content=json.loads(db.execute('SELECT content_json FROM case_revisions WHERE case_id=? AND revision=?',(id_,row['revision'])).fetchone()[0])
                    return 200,{**content,'archived_at':row['archived_at']}
            return 200,row
        return 404,{'error':'not_found'}
    except planning.StalePreflight as exc:return 409,{'error':'stale_preflight','message':str(exc),'preflight':exc.report}
    except planning.PreflightError as exc:return 400,{'error':'preflight_failed','message':str(exc),'preflight':exc.report}
    except BatchValidationError as exc:return 400,{'error':'invalid_import','message':str(exc),'validation':exc.report}
    except Forbidden as exc:return 403,{'error':'forbidden','message':str(exc)}
    except Conflict as exc:return 409,{'error':'conflict','message':str(exc)}
    except NotFound as exc:return 404,{'error':'not_found','message':str(exc)}
    except (KeyError,ValueError,TypeError,IndexError) as exc:return 400,{'error':'invalid_request','message':safe_error(exc)}


def make_handler(store,admin_token=None):
    admin_token=admin_token or os.getenv('LITEEVAL_ADMIN_TOKEN')
    sessions=BrowserSessions(store)
    public_origin=os.getenv('LITEEVAL_PUBLIC_ORIGIN')
    normalized_public=normalize_origin(public_origin) if public_origin else None
    if public_origin and not normalized_public:
        raise ValueError('LITEEVAL_PUBLIC_ORIGIN must be an HTTP(S) origin without a path')
    secure_cookie=bool(normalized_public and normalized_public[0]=='https') or os.getenv('LITEEVAL_COOKIE_SECURE')=='1'
    class Handler(BaseHTTPRequestHandler):
        def expected_origin(self):
            if public_origin:return public_origin
            host=self.headers.get('Host','').lower()
            port=self.server.server_address[1]
            if host not in {f'127.0.0.1:{port}',f'localhost:{port}'}:
                raise AuthError(403,'Unexpected Host; configure LITEEVAL_PUBLIC_ORIGIN for a reverse proxy')
            return 'http://'+host

        def respond(self,method):
            extra_headers={}
            try:
                route=urlparse(self.path).path.rstrip('/')
                length=int(self.headers.get('Content-Length','0'))
                if not 0<=length<=MAX_BODY:
                    self.send_error(413);return
                self.connection.settimeout(15)
                data=self.rfile.read(length)
                if len(data)!=length:raise ValueError('Incomplete request body')
                def invalid_constant(value):raise ValueError('Nonfinite JSON number')
                body=json.loads(data,parse_constant=invalid_constant) if data else {}
                if not isinstance(body,dict):raise ValueError('JSON object required')
                if route=='/v1/auth/session' and method=='POST':
                    check_origin(self.headers,self.expected_origin())
                    if set(body)!={'username','password'}:raise ValueError('Provide username and password')
                    token=sessions.login(body['username'],body['password'],self.client_address[0])
                    extra_headers['Set-Cookie']=cookie_header(token,secure_cookie)
                    status,result=200,{'username':body['username'],'role':'owner','csrf_token':csrf_token(token)}
                else:
                    token=None
                    if route=='/v1/health' and method=='GET':
                        principal={'admin':False,'project_id':None}
                    elif self.headers.get('Authorization'):
                        principal=authenticate(store,self.headers,admin_token)
                    else:
                        token=cookie_token(self.headers.get('Cookie'))
                        principal=sessions.authenticate(token)
                        if method not in {'GET','HEAD','OPTIONS'}:
                            check_origin(self.headers,self.expected_origin())
                            supplied=self.headers.get('X-CSRF-Token','')
                            if not hmac.compare_digest(supplied.encode(),csrf_token(token).encode()):
                                raise AuthError(403,'X-CSRF-Token required for browser writes')
                    if route=='/v1/auth/me' and method=='GET':
                        status,result=200,{'username':principal.get('username'),'role':'owner' if principal['admin'] else 'project',
                                           'project_id':principal['project_id'],'csrf_token':csrf_token(token) if token else None}
                    elif route=='/v1/auth/session' and method=='DELETE':
                        if not token:raise AuthError(400,'Logout requires a browser session')
                        sessions.revoke(token)
                        extra_headers['Set-Cookie']=cookie_header('',secure_cookie,clear=True)
                        status,result=200,{'logged_out':True}
                    else:
                        status,result=dispatch(store,method,self.path,body,principal,dict(self.headers))
                        if method=='POST' and re.fullmatch(r'/v1/cases/[0-9a-f]{32}/revisions',route) and 'base_revision' not in body:
                            extra_headers['Warning']='299 LiteEval "Send base_revision for conflict protection"'
            except AuthError as exc:status,result=exc.status,{'error':'authentication_error','message':str(exc)}
            except Unauthorized as exc:status,result=401,{'error':'unauthorized','message':str(exc)}
            except (ValueError,UnicodeDecodeError,TimeoutError) as exc:status,result=400,{'error':'invalid_request','message':safe_error(exc)}
            except Exception:
                status,result=500,{'error':'internal_error','message':'Request failed; consult Worker/service logs'}
            if isinstance(result,BinaryResponse):payload=result.content;mime=result.mime_type
            else:payload=json.dumps(result,ensure_ascii=False,allow_nan=False).encode();mime='application/json; charset=utf-8'
            self.send_response(status);self.send_header('Content-Type',mime)
            self.send_header('Content-Length',str(len(payload)));self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Cache-Control','no-store')
            if isinstance(result,BinaryResponse):self.send_header('Content-Disposition',f'attachment; filename="{result.filename}"')
            for key,value in extra_headers.items():self.send_header(key,value)
            self.end_headers();self.wfile.write(payload)
        def do_GET(self):self.respond('GET')
        def do_POST(self):self.respond('POST')
        def do_PATCH(self):self.respond('PATCH')
        def do_DELETE(self):self.respond('DELETE')
        def log_message(self,*args):pass  # Query strings and trace contents may contain sensitive data.
    return Handler

def serve(store,host='127.0.0.1',port=8765):
    if host not in {'127.0.0.1','localhost'}:raise ValueError('Bind to loopback; use a TLS proxy for remote access')
    token=os.getenv('LITEEVAL_ADMIN_TOKEN','')
    if token and len(token)<24:raise ValueError('Set LITEEVAL_ADMIN_TOKEN to a random secret of at least 24 characters')
    with store.connect() as db:owner_configured=db.execute('SELECT 1 FROM owner_account WHERE id=1').fetchone() is not None
    if not token and not owner_configured:raise ValueError('Run owner-init or set LITEEVAL_ADMIN_TOKEN before starting API')
    httpd=ThreadingHTTPServer((host,port),make_handler(store,token))
    print(f'LiteEval API listening at http://{host}:{port}',flush=True)
    try:httpd.serve_forever()
    finally:httpd.server_close()
