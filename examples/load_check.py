"""Controlled local load smoke check; deliberately excludes model/network latency."""
import argparse
import asyncio
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
from pathlib import Path
import platform
import sys
import tempfile
import threading
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from liteeval.storage import Store
from liteeval.runner import ExperimentRunner

class Agent(BaseHTTPRequestHandler):
    def do_POST(self):
        self.rfile.read(int(self.headers['Content-Length']))
        self.send_response(200);self.send_header('Content-Length','15');self.end_headers();self.wfile.write(b'{"answer":"ok"}')
    def log_message(self,*args):pass

class Server(ThreadingHTTPServer):request_queue_size=128

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--cases',type=int,default=1000);parser.add_argument('--concurrency',type=int,default=8)
    args=parser.parse_args()
    if not 1<=args.cases<=10000:parser.error('cases must be 1..10000')
    server=Server(('127.0.0.1',0),Agent);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        with tempfile.TemporaryDirectory() as root:
            store=Store(Path(root)/'load.db');pid=store.create_project('Load check')['id'];did=store.create_dataset(pid,'Load')['id']
            started=time.monotonic();store.import_cases(did,[{'input':{'question':str(i)},'expected':'ok'} for i in range(args.cases)])
            aid=store.add_agent(pid,'mock',{'type':'http','endpoint':f'http://127.0.0.1:{server.server_port}/agent'})['id']
            eid=store.add_evaluator(pid,'Exact','exact_match',{})['id']
            exp=store.create_experiment(pid,did,aid,[eid],{'concurrency':args.concurrency})['id']
            asyncio.run(ExperimentRunner(store).run(exp));result=store.get_experiment(exp)
            report={'python':platform.python_version(),'cases':args.cases,'concurrency':args.concurrency,
                    'elapsed_seconds':round(time.monotonic()-started,3),'status':result['status'],'summary':result['summary']}
            output=Path('data/load-check.json');output.parent.mkdir(exist_ok=True);output.write_text(json.dumps(report,indent=2))
            print(json.dumps({k:v for k,v in report.items() if k!='summary'}))
            print(json.dumps({'run_status_counts':report['summary']['run_status_counts'],'success_rate':report['summary']['success_rate'],'report':str(output)}))
            if result['status']!='completed' or report['summary']['success_rate']!=1:raise SystemExit(1)
    finally:server.shutdown();server.server_close();thread.join()

if __name__=='__main__':main()
