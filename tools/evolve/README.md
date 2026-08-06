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

## Fitness function

Per task, the LocalEvaluator scores the returned chunks:

- **recall** (weight 0.5) — fraction of ground-truth spans overlapped by
  at least one chunk.
- **precision** (weight 0.3) — fraction of returned *tokens* that fall
  inside ground-truth spans. This is the anti-bloat term: grep-style
  whole-file returns are crushed here (verified in the test suite).
- **mrr** (weight 0.2) — reciprocal rank of the first relevant chunk;
  rewards putting the answer first so agents don't spend extra turns.

`combined_score` is the mean over all tasks (0..1, higher is better).
`avg_tokens_returned`, `failures`, and per-metric means are reported
alongside for MAP-elites style archives and human inspection.

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
- Candidates run as subprocesses with hard timeouts — pathological
  mutations cost one task's score, not the run.
- The task manifest lives outside the searchable tree, so reading the
  answer key is impossible by construction.

## Tests

```bash
uv run pytest tests/unittests/evolve/
```

Covers: generator determinism and span accuracy, the search tool's
frozen contract (schema, budgets, CLI), and the evaluator's fitness
ordering — baseline > whole-file grep, and crash/hang candidates
score zero.
