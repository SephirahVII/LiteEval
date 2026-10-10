"""API process and standalone worker process."""

import argparse
import asyncio
import json
import os

from .api import serve
from .runner import ExperimentRunner, compare
from .storage import Store


def main():
    parser = argparse.ArgumentParser(description="LiteEval local prototype")
    parser.add_argument("--db", default=os.getenv("LITEEVAL_DB", "data/liteeval.db"))
    sub = parser.add_subparsers(dest="command", required=True)
    api = sub.add_parser("api")
    api.add_argument("--port", type=int, default=8765)
    worker = sub.add_parser("worker")
    worker.add_argument("--once", action="store_true")
    run = sub.add_parser("run")
    run.add_argument("experiment_id")
    comp = sub.add_parser("compare")
    comp.add_argument("left")
    comp.add_argument("right")
    args = parser.parse_args()
    store = Store(args.db)
    if args.command == "api":
        serve(store, port=args.port)
    elif args.command == "run":
        print(json.dumps({"claimed": asyncio.run(ExperimentRunner(store).run(args.experiment_id))}))
    elif args.command == "compare":
        print(json.dumps(compare(store, args.left, args.right), ensure_ascii=False, indent=2))
    else:
        runner = ExperimentRunner(store)
        if args.once:
            print(json.dumps({"experiment_id": asyncio.run(runner.run_next())}))
        else:
            while True:
                if not asyncio.run(runner.run_next()):
                    import time
                    time.sleep(1)


if __name__ == "__main__":
    main()
