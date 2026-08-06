# Real-log retrieval eval set

An eval set for the retrieval layer built from **real agent transcripts**,
not the synthetic mock monorepo. It measures the three costs the
token-economics analysis showed baseline agents pay when they search:
**steps** (tool calls per information need), **tokens** (chars/4 of
everything the detour dragged into context), and **wall seconds**.

## Files

| file | what it is |
| --- | --- |
| `episodes.jsonl.gz` | 236 retrieval episodes mined by `log_mining.py` from the 36 transcripts of benchmark run `2026-04-13_18-02-34_daily-pip-18-agents-cli-sandbox` (agent-generator, 18 comparison-suite cases × Claude/Gemini interactive, BASE + EXP arms). Each episode: the assistant message that opened the search, the retrieval tool calls, and the measured steps / tokens / wall seconds. |
| `real_eval_manifest.json` | 175 replayable cases built by `real_eval.py` — same `query` / `expected_spans` / `kind` schema `LocalEvaluator` scores, with the real agent's `observed` cost attached. |
| `replay_report.json` | `real_replay.py` output: both reference tools replayed over all 175 cases, vs. the observed agent cost. |

## Case families

* **sdk-symbol** (11 cases) — the agent reverse-engineering the installed
  SDK (`grep "class LlmAgent" .../site-packages/google/adk/...`). Ground
  truth is the AST-located definition span of the hunted symbol.
* **project-file** (164 cases) — the agent orienting inside its scaffolded
  project (reads of `app/agent.py`, `pyproject.toml`, tests). Ground truth
  is whole-file spans: the agent's need was the file's content.

Case frequency mirrors the real workload distribution (125 of the
project-file cases touch `app/agent.py` because that is what agents
actually re-read); the set is deliberately **not** deduplicated.

Unreplayable episodes (26%) — environment probes (`pip show`, `which`),
web fetches, symbols/files absent from the corpora — are kept in
`episodes.jsonl.gz` but produce no case.

## Corpora (not committed — regenerate)

Spans reference two corpora pinned by version:

* **sdk** — `google-adk == 1.34.1` install tree, rooted at
  `site-packages/google/adk` (542 py files).
* **project** — a default scaffold from `agents-cli scaffold create
  agent-project -y -s --agent-guidance-filename CLAUDE.md`
  (agents-cli v0.3.0).

## Reproduce / extend

```bash
# 1. Mine episodes from one or more agent-generator run dirs
PYTHONPATH=tools python3 -m evolve.retrieval.log_mining \
    <run_dir>... --min-steps 2 --out episodes.jsonl

# 2. Build the manifest against your corpora
PYTHONPATH=tools python3 -m evolve.retrieval.real_eval episodes.jsonl \
    --sdk-root <site-packages>/google/adk \
    --project-root <scaffolded-project> \
    --out real_eval_manifest.json

# 3. Replay + measure (default: default_grep + search_tool)
PYTHONPATH=tools python3 -m evolve.retrieval.real_replay real_eval_manifest.json \
    --sdk-root ... --project-root ... --out replay_report.json
```

## Headline result (this snapshot)

Observed agent cost per retrieval episode (median): **3 steps,
878 tokens, 3.8 s** (sdk-symbol episodes are pricier: 1.6k tokens median,
8.6k mean — the long tail is agents `cat`-ing SDK files).

| tool | family | recall | precision | MRR | tokens (med) | wall (med) |
| --- | --- | --- | --- | --- | ---: | ---: |
| default_grep | project-file | 0.73 | 0.13 | 0.23 | 4,676 | 0.03 s |
| default_grep | sdk-symbol | 0.55 | 0.01 | 0.17 | 96,872 | 0.07 s |
| search_tool (evolved) | project-file | 0.00 | 0.05 | 0.00 | 29 | 0.04 s |
| search_tool (evolved) | sdk-symbol | 0.36 | 0.26 | 0.36 | 101 | 0.18 s |

The two families have **opposite winners**: whole-file workloads reward
grep-and-read (that *is* the answer), while symbol hunts reward precise
chunking — the evolved tool answers them at ~1/1000th of default_grep's
token cost. The evolved tool's near-zero recall on project-file cases is
the gap between the mock-monorepo training distribution and real
conversational queries ("Let me verify the updated agent file:").

## Mock benchmark vs. real logs (why this set exists)

Same two tools, both benchmarks (mock = `evaluate_stage2`, seed 0,
scale 3, 108 tasks):

| tool | mock combined | mock recall | real recall (sdk / project) |
| --- | ---: | ---: | ---: |
| search_tool (evolved) | **1.00** | 1.00 | 0.36 / 0.00 |
| default_grep | 0.44 | 0.89 | 0.55 / 0.73 |

The evolution winner is *perfect* on the distribution it was evolved on
and collapses on the real workload — the mock benchmark's queries name
their target symbols cleanly, while real queries are conversational
fragments, and its ground truth never includes "the whole file is the
answer". Fitness for the next evolution run should include (or be
validated against) these real-log cases.
