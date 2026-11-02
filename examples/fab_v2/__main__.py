"""FAB entry point: prepare, native, direct, or local evidence report."""
import argparse
import runpy
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__, add_help=False)
    parser.add_argument('action', choices=['prepare', 'native', 'direct', 'report'])
    args, remaining = parser.parse_known_args()
    sys.argv = [sys.argv[0], *remaining]
    module = {'direct': 'benchmarks.fab_v2_direct', 'native': 'examples.fab_v2.native',
              'prepare': 'examples.fab_v2.prepare', 'report': 'examples.fab_v2.report'}[args.action]
    runpy.run_module(module, run_name='__main__')


if __name__ == '__main__': main()
