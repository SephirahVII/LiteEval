"""Run a frozen treasury package through the platform's registered Agent/Workflow."""
import argparse
import asyncio
import json
from pathlib import Path

from liteeval.application.benchmark_exports import export_execution
from liteeval.bootstrap import benchmark_service
from liteeval.infrastructure.persistence.store import Store
from liteeval.infrastructure.sources.harness import load_harness


async def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('package',type=Path)
    parser.add_argument('--db',type=Path,required=True)
    parser.add_argument('--project',required=True)
    parser.add_argument('--target-connection',required=True)
    parser.add_argument('--parameters',type=json.loads,default={'max_tokens':8192})
    parser.add_argument('--output',type=Path,default=Path('data/treasury-network'))
    args=parser.parse_args()
    service=benchmark_service(Store(args.db))
    package=args.package.resolve()
    release=service.publish(args.project,package)
    def register(path):
        spec,sha=load_harness(path,service.blobs)
        h=spec['definition']['harness']
        return service.repository.register(args.project,h['id'],h['role'],sha,spec)
    workflow=register(package/'workflow')
    agent=register(Path(__file__).resolve().parents[2]/'harnesses/json_action')
    result=await service.run(args.project,release['id'],agent,target_connection=args.target_connection,
                             target_parameters=args.parameters,workflow_ref=workflow,trusted_local=True)
    args.output.mkdir(parents=True,exist_ok=True)
    execution_id=result['execution_id']
    archive=args.output/(execution_id+'.zip')
    export_execution(service,args.project,execution_id,archive)
    report={'project_id':args.project,'release_id':release['id'],'release_digest':release['digest'],
            'agent_revision':agent,'workflow_revision':workflow,'result':result,'archive':str(archive.resolve())}
    (args.output/(execution_id+'.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'execution_id':execution_id,'summary':result['summary'],'archive':str(archive.resolve())}))


if __name__=='__main__':
    asyncio.run(main())
