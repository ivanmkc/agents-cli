"""Merge Claude A/B + Gemini verdicts into per-row consensus."""
import json
from collections import Counter
from pathlib import Path

TMP = Path('/home/ivanmkc/.claude/jobs/8c477bb8/tmp')

packets = [json.loads(l) for l in (TMP / 'packets.jsonl').read_text().splitlines() if l.strip()]
claude = json.loads((TMP / 'claude_verdicts.json').read_text())  # {row_id: {claude_A: {...}, claude_B: {...}}}

gemini = {}
for f in sorted((TMP / 'gemini_verdicts').glob('batch_*.json')):
    for v in json.loads(f.read_text()):
        gemini[int(v['row_id'])] = {
            'verdict': v.get('verdict', 'flag'),
            'issues': v.get('issues', []),
            'note': v.get('note', ''),
        }

ORDER = {'valid': 0, 'flag': 1, 'invalid': 2}
rows, missing = [], []
for p in packets:
    rid = p['row_id']
    votes = {}
    cv = claude.get(str(rid)) or claude.get(rid) or {}
    for lens in ('claude_A', 'claude_B'):
        if lens in cv:
            votes[lens] = cv[lens]
    if rid in gemini:
        votes['gemini-3.1-pro'] = gemini[rid]
    if len(votes) < 3:
        missing.append((rid, sorted(votes)))
    tally = Counter(v['verdict'] for v in votes.values())
    top, top_n = (tally.most_common(1) or [('flag', 0)])[0]
    if top_n >= 2:
        consensus = top
    else:
        consensus = 'flag'  # 3-way split
    all_issues = Counter(i for v in votes.values() for i in v.get('issues', []))
    # An issue is "agreed" if >=2 validators raised it.
    agreed_issues = sorted(i for i, n in all_issues.items() if n >= 2)
    rows.append({
        'row_id': rid,
        'family': p['derived_row']['family'],
        'query': p['derived_row']['query'][:120],
        'consensus': consensus,
        'unanimous': len(tally) == 1 and len(votes) >= 3,
        'agreed_issues': agreed_issues,
        'votes': {k: {'verdict': v['verdict'], 'issues': v.get('issues', []), 'note': v.get('note', '')[:200]} for k, v in votes.items()},
    })

summary = Counter(r['consensus'] for r in rows)
unanimous_valid = sum(1 for r in rows if r['consensus'] == 'valid' and r['unanimous'])
issue_counts = Counter(i for r in rows for i in r['agreed_issues'])
report = {
    'num_rows': len(rows),
    'validators_per_row': 3,
    'consensus_summary': dict(summary),
    'unanimous_valid': unanimous_valid,
    'agreed_issue_counts': dict(issue_counts.most_common()),
    'rows_missing_votes': missing,
    'rows': rows,
}
(TMP / 'consensus_report.json').write_text(json.dumps(report, indent=1))
print('consensus:', dict(summary), '| unanimous valid:', unanimous_valid, '/', len(rows))
print('agreed issues:', dict(issue_counts.most_common()))
print('missing votes:', len(missing))
for r in rows:
    if r['consensus'] == 'invalid':
        print('INVALID', r['row_id'], r['family'], r['agreed_issues'], '|', r['query'][:80])
