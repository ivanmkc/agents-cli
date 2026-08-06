"""Degenerate candidate 3: read the answer key at <repo>.tasks.json.

The manifest lives at repo_root.parent / f"{repo_root.name}.tasks.json" —
outside the tree, but at a fully predictable path the subprocess can read.
"""
import argparse, json, sys
from pathlib import Path

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--repo', required=True, type=Path)
    p.add_argument('--query', required=True)
    p.add_argument('--max-results', type=int, default=8)
    p.add_argument('--token-budget', type=int, default=2000)
    a = p.parse_args()
    key = a.repo.parent / f"{a.repo.name}.tasks.json"
    chunks = []
    try:
        manifest = json.loads(key.read_text())
        for task in manifest['tasks']:
            if task['query'] == a.query:
                for s in task['expected_spans']:
                    lines = (a.repo / s['file']).read_text().splitlines()
                    content = '\n'.join(lines[s['start_line'] - 1:s['end_line']])
                    chunks.append({'file': s['file'],
                                   'start_line': s['start_line'],
                                   'end_line': s['end_line'],
                                   'content': content, 'score': 1.0})
                break
    except OSError:
        pass
    json.dump({'chunks': chunks[:a.max_results]}, sys.stdout)
    return 0

if __name__ == '__main__':
    sys.exit(main())
