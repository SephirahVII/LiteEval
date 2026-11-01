"""Local API/Worker lifecycle and explicit live judge smoke check."""
import argparse
import asyncio
import json
import os
import getpass
from pathlib import Path
from liteeval.interfaces.http.api import serve
from liteeval.application.dataset_runner import ExperimentRunner
from liteeval.application.reports import compare
from liteeval.infrastructure.persistence.store import Store
from liteeval.domain.contracts import EvalCase, AgentRun
from liteeval.integrations.judges.configured import ConfiguredEvaluator
from liteeval.interfaces.http.sessions import BrowserSessions
from liteeval.application.previews import PreviewRunner
from liteeval.infrastructure.execution.workers import WorkerRegistry

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
    owner=sub.add_parser('owner-init');owner.add_argument('--username',default='owner')
    bench = sub.add_parser('benchmark'); actions = bench.add_subparsers(dest='action', required=True)
    validate = actions.add_parser('validate'); validate.add_argument('path')
    publish = actions.add_parser('import'); publish.add_argument('path'); publish.add_argument('--project', required=True)
    releases = actions.add_parser('releases'); releases.add_argument('--project', required=True)
    execute = actions.add_parser('run'); execute.add_argument('release_id'); execute.add_argument('--project', required=True)
    execute.add_argument('--agent-harness', required=True); execute.add_argument('--evaluation-harness', default='liteeval.native/v1')
    execute.add_argument('--target-connection'); execute.add_argument('--judge-connection'); execute.add_argument('--trusted-local', action='store_true')
    export = actions.add_parser('export'); export.add_argument('execution_id'); export.add_argument('--project', required=True); export.add_argument('--output', required=True)
    rejudge = actions.add_parser('rejudge'); rejudge.add_argument('execution_id'); rejudge.add_argument('--project', required=True)
    harness = sub.add_parser('harness'); harness.add_argument('path'); harness.add_argument('--project', required=True)
    args=parser.parse_args();store=Store(args.db)
    if args.command in ('benchmark', 'harness'):
        from liteeval.bootstrap import benchmark_service
        from liteeval.infrastructure.sources.harness import load_harness
        from liteeval.application.benchmark_exports import export_execution
        service = benchmark_service(store)
        if args.command == 'harness':
            spec, sha = load_harness(args.path, service.blobs)
            result = {'harness_revision_id': service.repository.register(args.project, spec['definition']['harness']['id'], spec['definition']['harness']['role'], sha, spec), 'digest': sha}
        elif args.action == 'validate':
            package = service.loader.load(args.path)
            result = {'valid': True, 'id': package.id, 'version': package.version, 'digest': package.digest, 'task_count': len(package.tasks)}
        elif args.action == 'import': result = service.publish(args.project, args.path)
        elif args.action == 'releases': result = service.repository.list_releases(args.project)
        elif args.action == 'run':
            result = asyncio.run(service.run(args.project, args.release_id, args.agent_harness, evaluation_ref=args.evaluation_harness,
                target_connection=args.target_connection, judge_connection=args.judge_connection, trusted_local=args.trusted_local))
        elif args.action == 'rejudge': result = asyncio.run(service.rejudge(args.project, args.execution_id))
        else:
            export_execution(service, args.project, args.execution_id, args.output)
            result = {'export': str(Path(args.output).resolve())}
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command=='owner-init':
        password=getpass.getpass('Owner password (12+ characters): ')
        if password!=getpass.getpass('Confirm password: '):raise SystemExit('Passwords do not match')
        BrowserSessions(store).configure_owner(args.username,password)
        print('Owner configured. Existing browser sessions revoked.')
    elif args.command=='api':serve(store,port=args.port)
    elif args.command=='run':print(json.dumps({'claimed':asyncio.run(ExperimentRunner(store).run(args.experiment_id))}))
    elif args.command=='compare':print(json.dumps(compare(store,args.left,args.right),ensure_ascii=False,indent=2))
    elif args.command=='key':print(json.dumps(store.issue_key(args.project_id,args.label)))
    elif args.command=='cancel':print(json.dumps(store.cancel(args.experiment_id)))
    elif args.command=='judge-check':
        from dataclasses import asdict
        config=json.loads(Path(args.config_file).read_text())
        if config.get('connection_id'):
            from liteeval.application.connections import bind
            with store.connect() as db:
                connection=store.required(db,'model_connections',config['connection_id'])
                config=bind(db,store,connection['project_id'],config,judge=True)
        score=asyncio.run(ConfiguredEvaluator('live-check','llm_judge',config,store).evaluate(EvalCase('check',1,{'question':'What is 2+2?'},'4'),AgentRun('check','check','succeeded',output='4')))
        print(json.dumps(asdict(score),ensure_ascii=False,indent=2))
        if score.outcome=='error':raise SystemExit(1)
    else:
        async def work():
            runner=ExperimentRunner(store)
            preview_runner=PreviewRunner(store)
            registry=WorkerRegistry(store)
            registry.heartbeat()
            monitor=asyncio.create_task(registry.monitor())
            try:
                while True:
                    job=None;preview=None
                    try:
                        job=await runner.run_next()
                        # Service both queues between experiments; --once retains its one-job behavior.
                        if not args.once or not job:preview=await preview_runner.run_next()
                    except Exception as exc:
                        from liteeval.infrastructure.security.secrets import safe_error
                        print(safe_error(exc),flush=True);job=None
                    if monitor.done():monitor.result()
                    if args.once:print(json.dumps({'experiment_id':job,'preview_id':preview}));return
                    if not job and not preview:await asyncio.sleep(0.5)
            finally:
                monitor.cancel()
                await asyncio.gather(monitor,return_exceptions=True)
                registry.stop()
        try:asyncio.run(work())
        except KeyboardInterrupt:pass

if __name__=='__main__':main()
