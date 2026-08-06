import sys, json, statistics
sys.path.insert(0, 'tools')
from pathlib import Path
from collections import Counter
from evolve.retrieval.monorepo import generate_monorepo

wd = Path('_audit_bench')
repo = wd / 'repo'
if not (wd / 'repo.tasks.json').exists():
    wd.mkdir(exist_ok=True)
    generate_monorepo(repo, seed=0, scale=3)
m = json.loads((wd / 'repo.tasks.json').read_text())
tasks = m['tasks']
print('total tasks:', len(tasks))
print('kinds:', Counter(t['kind'] for t in tasks))
print('stage1 (first 20) kinds:', Counter(t['kind'] for t in tasks[:20]))
print('stage1 symbols:', sorted({t['symbol'] for t in tasks[:20]}))
defspans = [t['expected_spans'][0] for t in tasks if t['kind'] == 'definition']
print('definition span lines median:',
      statistics.median(s['end_line'] - s['start_line'] + 1 for s in defspans))
usp = [t for t in tasks if t['kind'] == 'usage']
print('usage tasks:', len(usp), 'avg spans:',
      sum(len(t['expected_spans']) for t in usp) / len(usp))
