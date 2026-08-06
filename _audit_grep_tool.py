"""Degenerate candidate 1: grep + return whole files (the 'default' behavior)."""
import argparse, json, re, sys
from pathlib import Path

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--repo', required=True, type=Path)
    p.add_argument('--query', required=True)
    p.add_argument('--max-results', type=int, default=8)
    p.add_argument('--token-budget', type=int, default=2000)
    a = p.parse_args()
    terms = [t for t in re.findall(r'[A-Za-z_]\w*', a.query) if len(t) > 3]
    chunks = []
    for path in sorted(a.repo.rglob('*.py')) + sorted(a.repo.rglob('*.yaml')):
        try:
            text = path.read_text(errors='replace')
        except OSError:
            continue
        score = sum(text.count(t) for t in terms if len(t) > 6)
        if score:
            lines = text.splitlines()
            chunks.append({
                'file': path.relative_to(a.repo).as_posix(),
                'start_line': 1, 'end_line': len(lines),
                'content': text, 'score': float(score)})
    chunks.sort(key=lambda c: -c['score'])
    json.dump({'chunks': chunks[:a.max_results]}, sys.stdout)
    return 0

if __name__ == '__main__':
    sys.exit(main())
