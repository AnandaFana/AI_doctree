"""Sync this tool's shared nodes, keeping ignored local pilot directories separate."""
from pathlib import Path
import argparse
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from doctree.markdown_protocol import plan_sync, apply_plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args()
    # Explicit local fixture boundary; never change navigation inside these copies.
    plan = plan_sync(ROOT, [], 'doctree', discovery_options={
        'exclude_dirs': ['artifacts', 'local-projects', 'numerical-2.0-5pages', 'examples']})
    result = apply_plan(plan) if args.apply else plan
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return int(args.check and bool(plan['changes']))


if __name__ == '__main__':
    raise SystemExit(main())
