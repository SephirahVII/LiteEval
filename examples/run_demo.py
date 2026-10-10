"""One command runs the authenticated API -> Agent -> Worker -> Compare demo."""
import asyncio
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import sys
import threading
from urllib.request import Request,urlopen
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from liteeval.api import make_handler
from liteeval.storage import Store
from liteeval.runner import ExperimentRunner

class Agent(BaseHTTPRequestHandler):
    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        q=body['input']['question']
        answer='退款请在订单页申请' if q=='如何退款？' else '请联系人工客服' if self.server.version=='v2' else '不知道'
        payload=json.dumps({'answer':answer,'events':[{'kind':'tool','name':'lookup_policy','attributes':{'arguments':{'topic':q}}}],
                           'trajectory_complete':True,'usage':{'input_tokens':20,'output_tokens':10},'cost':0.001,'currency':'CNY'},ensure_ascii=False).encode()
        self.send_response(200);self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
    def log_message(self,*args):pass

def main():
    store=Store('data/demo/liteeval.db');token=secrets.token_urlsafe(32)
    servers=[]
    def start(handler):
        server=ThreadingHTTPServer(('127.0.0.1',0),handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start();servers.append((server,thread));return server
    a=start(Agent);a.version='v1';b=start(Agent);b.version='v2';api=start(make_handler(store,token))
    base=f'http://127.0.0.1:{api.server_port}'
    def call(method,path,body=None):
        request=Request(base+path,json.dumps(body,ensure_ascii=False).encode() if body is not None else None,
                        {'Authorization':'Bearer '+token,'Content-Type':'application/json'},method=method)
        with urlopen(request) as response:return json.load(response)
    try:
        pid=call('POST','/v1/projects',{'name':'Demo support agent'})['id']
        did=call('POST','/v1/datasets',{'project_id':pid,'name':'Regression'})['id']
        trace=call('POST','/v1/traces',{'project_id':pid,'input':{'question':'如何退款？'},'output':'错误回复','status':'failed'})['id']
        call('POST',f'/v1/datasets/{did}/cases/from-trace',{'trace_id':trace,'expected':'退款请在订单页申请'})
        call('POST',f'/v1/datasets/{did}/import',{'records':[{'input':{'question':'无法登录怎么办？'},'expected':'请联系人工客服','metadata':{'severity':'high'}}]})
        eid=call('POST','/v1/evaluators',{'project_id':pid,'name':'Task correctness','kind':'exact_match','config':{}})['id']
        runs=[]
        for server in (a,b):
            aid=call('POST','/v1/agent-versions',{'project_id':pid,'label':server.version,'config':{'type':'http','endpoint':f'http://127.0.0.1:{server.server_port}/agent'}})['id']
            exp=call('POST','/v1/experiments',{'project_id':pid,'dataset_id':did,'agent_version_id':aid,'evaluator_ids':[eid],'config':{'concurrency':2}})['id']
            asyncio.run(ExperimentRunner(store).run(exp));runs.append(exp)
        report=call('GET',f'/v1/compare?left={runs[0]}&right={runs[1]}')
        Path('data/demo/report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(json.dumps({'case_changes':report['counts'],'v1_success_rate':report['left_summary']['success_rate'],
                          'v2_success_rate':report['right_summary']['success_rate'],'report':'data/demo/report.json'},ensure_ascii=False,indent=2))
    finally:
        for server,thread in servers:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':main()
