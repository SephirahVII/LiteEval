"""Offline API/Worker examples, grouped by the behavior they demonstrate."""
import argparse
import importlib
import sys


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('demo', choices=['smoke', 'readiness', 'catalog', 'load'])
    args, remaining = parser.parse_known_args()
    sys.argv = [sys.argv[0], *remaining]
    importlib.import_module('examples.platform.' + args.demo).main()


if __name__ == '__main__': main()
