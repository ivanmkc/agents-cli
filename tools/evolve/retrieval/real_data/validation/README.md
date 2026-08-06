# Consensus Validation of the Real-Log Eval Set

3-validator consensus pass over all 175 rows of `../real_eval_manifest.json`,
run 2026-08-06. Every row was judged independently by:

1. **claude_A** — Claude subagent, query-faithfulness lens
2. **claude_B** — Claude subagent, ground-truth skeptic lens
3. **gemini-3.1-pro** — Gemini 3.1 Pro (google-genai SDK, Vertex ADC),
   same rubric, batches of ≤15 rows

Consensus = majority verdict (≥2 of 3); a 3-way split downgrades to `flag`.
An issue tag is "agreed" when ≥2 validators raised it. Merger:
`merge_consensus.py` (paths reference the original job tmp dir; inputs are
preserved here as `packets.jsonl.gz`, `claude_verdicts.json`,
`gemini_verdicts.json`).

## Results

| Consensus | Rows | % |
|---|---|---|
| valid | 63 | 36% |
| flag | 41 | 23% |
| invalid | 71 | 41% |

47/63 valid rows were unanimous. No row was missing votes.

Per family:

| Family | valid | flag | invalid | total |
|---|---|---|---|---|
| project-file | 60 | 41 | 63 | 164 |
| sdk-symbol | 3 | 0 | 8 | 11 |

Agreed issue counts (across all non-valid rows):
`fragmented-episode` 44, `verification-read` 42, `span-wrong` 32,
`span-incomplete` 25, `query-not-faithful` 16, `query-leaks-answer` 14,
`query-vacuous` 10, `observed-mismatch` 3, `other` 2, `family-wrong` 1.

## What this means

- **41% of auto-derived rows would have poisoned the fitness function.**
  The two dominant failure modes are miner artifacts, not judgment calls:
  - `verification-read`: the agent was re-reading a file it had *just
    written* — that is confirmation, not retrieval, and its "ground truth"
    span is whatever the agent happened to write.
  - `fragmented-episode`: the derived query is a mid-sentence fragment of a
    longer hunt, so the query does not express a real information need.
- **Use `../real_eval_manifest.validated.json`** (63 consensus-valid tasks,
  each annotated with `row_id` + `validation`) as the fitness/eval input.
  `flag` rows (41) are usable with caveats — see their `agreed_issues` in
  `consensus_report.json`.
- **Miner follow-ups** (would recover much of the invalid 41%): drop
  read-after-own-write episodes, and segment episodes on assistant-message
  boundaries so queries aren't sentence fragments.

## Files

- `consensus_report.json` — per-row votes, consensus, agreed issues (the
  authoritative output)
- `claude_verdicts.json` — raw Claude A/B lens verdicts (175 rows × 2)
- `gemini_verdicts.json` — raw Gemini 3.1 Pro verdicts, keyed by row_id
- `packets.jsonl.gz` — exact validator inputs (episode + derived row +
  span evidence), for reproducibility
- `merge_consensus.py` — the merger script used
