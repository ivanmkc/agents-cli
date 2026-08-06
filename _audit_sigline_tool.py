"""Degenerate candidate 2: return only single hit-lines (signature lines).

A plausible LLM mutation: 'trim chunks to only the matching line to boost
precision'. Useless to an agent (no function body), but should score near 1.0
if the fitness has the any-overlap recall cliff.
"""
import argparse, json, re, sys
from pathlib import Path

def main():
    p = argparse.ArgumentParser()
    p.add_argument('--repo', required=True, type=Path)
    p.add_argument('--query', required=True)
    p.add_argument('--max-results', type=int, default=8)
    p.add_argument('--token-budget', type=int, default=2000)
    a = p.parse_args()
    terms = [t for t in re.findall(r'[A-Za-z_]\w*', a.query) if len(t) > 6]
    chunks = []
    for path in sorted(a.repo.rglob('*')):
        if path.suffix not in ('.py', '.yaml') or not path.is_file():
            continue
        try:
            lines = path.read_text(errors='replace').splitlines()
        except OSError:
            continue
        rel = path.relative_to(a.repo).as_posix()
        for i, line in enumerate(lines):
            hits = [t for t in terms if t in line]
            if not hits:
                continue
            score = 1.0 * len(hits)
            if re.match(r'^\s*(class|def)\s', line):
                score *= 3
            if 'import' in line:
                score *= 0.5
            chunks.append({'file': rel, 'start_line': i + 1, 'end_line': i + 1,
                           'content': line, 'score': score})
    chunks.sort(key=lambda c: -c['score'])
    json.dump({'chunks': chunks[:a.max_results]}, sys.stdout)
    return 0

if __name__ == '__main__':
    sys.exit(main())
