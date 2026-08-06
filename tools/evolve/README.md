# AlphaEvolve Integration — Use Case 3: Evolving the Retrieval Layer

This directory hosts the harness for evolving the **code-search tool**
that agents use to retrieve context from large codebases.

## Why

Agents today delegate code lookup to whatever the host CLI provides —
usually `grep`/`find` — and routinely pull 1,000+-line files into the
context window when a 15-line function would do. That "context-resend
tax" can account for the majority of inference cost on complex coding
tasks and degrades reasoning through needle-in-a-haystack dilution.

Use case 3 of the AlphaEvolve integration strategy targets exactly this:
evolve the retrieval layer once, centrally, and every agent that uses
the tool inherits the win. No end-user compute required.

## What's here

| File | Role in the loop |
|------|------------------|
| `retrieval/search_tool.py` | **The target tool.** A self-contained code-search program. The ranking algorithm sits inside `# EVOLVE-BLOCK-START/END` markers; the CLI, chunk schema, and token-budget enforcement are a frozen contract outside the block. This is the file AlphaEvolve mutates. |
| `retrieval/monorepo.py` | **The environment.** Deterministic mock-monorepo generator: 6 packages × N modules (~270 lines each, with distractor code), service files with cross-package usage sites, YAML configs, and oversized vendored noise. Also emits the benchmark manifest — 100+ retrieval tasks (definition / usage / config lookups) with line-accurate ground-truth spans. The answer key is written **outside** the searchable tree so candidates can't cheat. |
| `retrieval/evaluator.py` | **The LocalEvaluator.** Runs a candidate program over every task as a sandboxed subprocess (hard timeout; crashes and hangs score zero, never kill the loop) and computes the fitness metrics. |
| `retrieval/evaluate.py` | **The local fitness harness.** `evaluate(program_path) -> metrics` with `combined_score` as the fitness signal, plus a `evaluate_stage1` / `evaluate_stage2` cascade for cheap early rejection of broken mutants. |
| `retrieval/alpha_evolve_adapter.py` | **Official-contract adapter.** `retrieval_evaluation(program_candidate)` implements the AlphaEvolve client library's evaluation contract ([codelab examples](https://github.com/Google-Cloud-AI/alphaevolve-on-googlecloud)): candidate code in `content.files[0].content`, an `AlphaEvolveProgramEvaluation`-shaped payload back, all metrics maximization-friendly, and **insights** (runtime failures, token bloat, weakest task kind, ranking quality) that steer the next round of mutations. |
| `retrieval/run_evolution.py` | **The experiment runner.** Mirrors the official codelab wiring: `AlphaEvolveClient` → `AlphaEvolveExperiment` → seed program → `run_controller_loop`. Needs the `alpha_evolve` client library + a Gemini Enterprise app with AlphaEvolve access. |
| `retrieval/config.yaml` | Alternative run configuration for the open-source OpenEvolve runner (no Gemini Enterprise access required). |
| `retrieval/log_mining.py` | **Real-workload miner.** Extracts *retrieval episodes* (maximal runs of read/grep/glob/read-only-shell calls, ended by an edit/write/run) from agent-generator benchmark transcripts, with each episode's measured cost: steps, tokens (chars/4), wall seconds. |
| `retrieval/real_eval.py` | **Real-log eval-set builder.** Turns replayable episodes into `LocalEvaluator`-compatible cases: `sdk-symbol` (AST-located definition spans in the installed SDK the agent was reverse-engineering) and `project-file` (whole-file spans of the scaffold files the agent re-read). |
| `retrieval/real_replay.py` | **Real-log measurement harness.** Replays a real-log manifest against candidate tools, timing every query, and reports recall/precision/MRR + tokens/steps/wall-seconds vs. the real agent's observed cost. |
| `retrieval/real_data/` | **The mined eval set** (episodes, 175-case manifest, replay report) with provenance + repro commands. Headline: the mock-monorepo evolution winner scores 1.0 on mock and 0.03 recall on real logs — see `real_data/README.md`. |

## Fitness function

Per task, the LocalEvaluator scores the returned chunks:

- **recall** (weight 0.35) — coverage-weighted: each ground-truth span
  scores `covered_lines / span_lines` (union over all verified chunks).
  A 1-line pointer at a 20-line function earns 0.05, not 1.0 — this
  defeats the "degenerate 1-line tool" exploit found in the red-team
  audit.
- **precision** (weight 0.35) — fraction of returned *tokens* that fall
  inside ground-truth spans. Grep-style whole-file returns are crushed
  here (verified in the test suite).
- **mrr** (weight 0.15) — reciprocal rank of the first chunk that covers
  >= 50% of some ground-truth span on its own; rewards putting a
  *usable* answer first.
- **economy** (weight 0.15) — `min(1, 384/total_tokens)`: an absolute
  token-cost penalty that stops "return everything" strategies from
  hiding behind high recall.

`combined_score` is the weighted mean over all tasks (0..1, higher is
better). `avg_tokens_returned`, `failures`, and per-metric means are
reported alongside for MAP-elites style archives and human inspection.

### Anti-reward-hacking measures

Each countermeasure defeats a specific exploit demonstrated by the
red-team audit (28-agent adversarial sweep, 22 confirmed findings):

| Exploit | Countermeasure |
|---------|---------------|
| **Answer-key leak**: candidate reads `<repo>.tasks.json` → perfect 1.0 | Answer key never written to disk; `_ensure_benchmark()` deletes any stale key; subprocess runs with empty cwd + minimal env |
| **Content fabrication**: claim huge spans with empty/fake content → free recall | Chunks verified against disk; mismatching content earns 0 relevance but still pays token cost |
| **Degenerate 1-line tool**: return one best-matching line per file → 0.88 recall | Coverage-weighted recall: `covered_lines/span_lines` per span, not binary overlap |

All exploits are codified as regression tests in `test_reward_hacking.py`.

### Evolution results

A 3-generation × 4-mutant evolution run produced an AST-based winner:

| Metric | Seed | Default (grep) | Winner | Winner lift vs default |
|--------|------|-----------------|--------|----------------------|
| combined_score | 0.74 | 0.44 | **1.0** | +125.3% |
| recall | 0.85 | 0.89 | **1.0** | +11.2% |
| precision | 0.47 | 0.01 | **1.0** | +98.7% |
| mrr | 0.85 | 0.78 | **1.0** | +22.2% |
| economy | 0.71 | 0.08 | **1.0** | — |
| avg tokens/call | 735.9 | 10,262.4 | **81.9** | -99.2% |

## Running

```bash
# One-off fitness score for the current baseline:
PYTHONPATH=tools uv run python tools/evolve/retrieval/evaluate.py \
    tools/evolve/retrieval/search_tool.py

# Regenerate the benchmark environment by hand:
PYTHONPATH=tools uv run python tools/evolve/retrieval/monorepo.py /tmp/mock-repo

# Query the search tool directly (the same contract agents use):
uv run python tools/evolve/retrieval/search_tool.py \
    --repo /tmp/mock-repo --query "Where is class AuthMiddleware0 defined?"

# Full evolution run with the official AlphaEvolve client
# (requires the alpha_evolve library from
# github.com/Google-Cloud-AI/alphaevolve-on-googlecloud and a Gemini
# Enterprise app; set PROJECT_ID, GE_APP_ID, ... in the environment):
PYTHONPATH=tools python tools/evolve/retrieval/run_evolution.py

# Alternative: open-source OpenEvolve runner, no enterprise access needed:
PYTHONPATH=tools python -m openevolve.run \
    tools/evolve/retrieval/search_tool.py \
    tools/evolve/retrieval/evaluate.py \
    --config tools/evolve/retrieval/config.yaml
```

The benchmark environment is generated once per workdir (deterministic
seed → identical tree every time) and reused across all candidate
evaluations, so scores are comparable between generations. Set
`EVOLVE_RETRIEVAL_WORKDIR` to control where it lives.

## Workflow (tooling team)

1. **Tag the target tool** — done: `search_tool.py` exists and its
   algorithm is wrapped in EVOLVE-BLOCK markers.
2. **Set up the environment** — done: `monorepo.py` provisions the mock
   monorepo + 100-task benchmark deterministically.
3. **Configure the evaluator** — done: `LocalEvaluator` penalizes
   bloated returns and low-ranked answers; cascade stage 1 rejects
   broken mutants cheaply.
4. **Run the loop** — `run_evolution.py` with the official AlphaEvolve
   client, or point OpenEvolve at `search_tool.py` + `evaluate.py` with
   `config.yaml`.
5. **Ship** — the winning `search_tool.py` replaces the baseline in a
   PR; every agent using the tool inherits the improvement.

## Guard rails

- The frozen wrapper re-validates whatever the evolved block returns
  (schema, repo-relative paths, result cap, token budget), so a mutant
  cannot "win" by violating the interface.
- Candidates run as subprocesses in a sandboxed environment (empty cwd,
  minimal env vars) with hard timeouts — pathological mutations cost one
  task's score, not the run.
- The task manifest only lives in process memory during evaluation; no
  `*.tasks.json` ever touches disk (red-team audit demonstrated a
  candidate reading a manifest file to score a perfect 1.0).
- Chunk content is verified against the actual file on disk — fabricated
  or mismatched content earns zero relevance but still pays token cost.

## Tests

```bash
uv run pytest tests/unittests/evolve/
```

34 tests covering:
- Generator determinism and span accuracy (5 tests)
- Search tool frozen contract — schema, budgets, CLI (5 tests)
- Evaluator fitness ordering — baseline > grep, crash/hang = zero (5 tests)
- Evaluate entrypoint — metrics, determinism, stage-1 subset (3 tests)
- AlphaEvolve adapter — official contract compliance (4 tests)
- Lift measurement — default proxy, math, caching (3 tests)
- Agent-in-the-loop — answer extraction, correctness, stratification (4 tests)
- **Reward-hacking regressions** — degenerate tool, fabrication, key leak, key reader, stratification (5 tests)
