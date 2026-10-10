"""Local API/Worker lifecycle and explicit live judge smoke check."""
import argparse
import asyncio
import json
import os
from pathlib import Path
from .api import serve
from .runner import ExperimentRunner
from .reports import compare
from .storage import Store
from .core import EvalCase,AgentRun
from .evaluators import ConfiguredEvaluator

def main():
    parser=argparse.ArgumentParser(description='LiteEval backend MVP')
    parser.add_argument('--db',default=os.getenv('LITEEVAL_DB','data/liteeval.db'))
    sub=parser.add_subparsers(dest='command',required=True)
    api=sub.add_parser('api');api.add_argument('--port',type=int,default=8765)
    worker=sub.add_parser('worker');worker.add_argument('--once',action='store_true')
    run=sub.add_parser('run');run.add_argument('experiment_id')
    comp=sub.add_parser('compare');comp.add_argument('left');comp.add_argument('right')
    key=sub.add_parser('key');key.add_argument('project_id');key.add_argument('--label',default='client')
    cancel=sub.add_parser('cancel');cancel.add_argument('experiment_id')
    judge=sub.add_parser('judge-check');judge.add_argument('config_file')
    args=parser.parse_args();store=Store(args.db)
    if args.command=='api':serve(store,port=args.port)
    elif args.command=='run':print(json.dumps({'claimed':asyncio.run(ExperimentRunner(store).run(args.experiment_id))}))
    elif args.command=='compare':print(json.dumps(compare(store,args.left,args.right),ensure_ascii=False,indent=2))
    elif args.command=='key':print(json.dumps(store.issue_key(args.project_id,args.label)))
    elif args.command=='cancel':print(json.dumps(store.cancel(args.experiment_id)))
    elif args.command=='judge-check':
        from dataclasses import asdict
        config=json.loads(Path(args.config_file).read_text())
        score=asyncio.run(ConfiguredEvaluator('live-check','llm_judge',config).evaluate(EvalCase('check',1,{'question':'What is 2+2?'},'4'),AgentRun('check','check','succeeded',output='4')))
        print(json.dumps(asdict(score),ensure_ascii=False,indent=2))
        if score.outcome=='error':raise SystemExit(1)
    else:
        async def work():
            runner=ExperimentRunner(store)
            while True:
                try:job=await runner.run_next()
                except Exception as exc:
                    from .security import safe_error
                    print(safe_error(exc),flush=True);job=None
                if args.once:print(json.dumps({'experiment_id':job}));return
                if not job:await asyncio.sleep(0.5)
        try:asyncio.run(work())
        except KeyboardInterrupt:pass

if __name__=='__main__':main()
