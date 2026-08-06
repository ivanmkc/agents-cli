# Progress — Real-Log Retrieval Eval Set & Skills Impact Measurement

Last updated: 2026-08-06 ~09:45 UTC
Branch: `alphaevolve-retrieval-real-eval` (pushed to `origin`)
Worktree: `/home/ivanmkc/agents-cli/.claude/worktrees/alphaevolve-retrieval`

---

## Goal

Measure whether agents-cli's skills (especially the adk-python reference
and samples.md clone-and-study protocol) eliminate the SDK source-grepping
and project-orientation reading that baseline agents do — quantified as
retrieval steps, tokens dragged into context, and wall-clock seconds.

## What's been done

### 1. Real-log eval set built and committed (DONE)

**Commit** `861e2cc` on branch `alphaevolve-retrieval-real-eval`.

Mined 236 retrieval episodes from the pre-skills benchmark run
(`2026-04-13_18-02-34_daily-pip-18-agents-cli-sandbox` — 142 transcripts,
Claude + Gemini, 36 unique case IDs each, 71 files per model). Built 175
replayable cases in the same `LocalEvaluator` manifest format the
evolution loop scores against.

Key files:
- `tools/evolve/retrieval/log_mining.py` — episode miner
- `tools/evolve/retrieval/real_eval.py` — case builder (sdk-symbol + project-file)
- `tools/evolve/retrieval/real_replay.py` — measurement harness
- `tools/evolve/retrieval/real_data/` — episodes, manifest, replay report
- `tools/evolve/retrieval/real_data/METHODOLOGY.md` — full methodology (10 sections)
- `tests/unittests/evolve/test_log_mining.py` + `test_real_eval.py` + `test_real_replay.py` — 50 tests, all passing

### 2. Trajectory analysis (DONE)

Traced complete agent search trajectories through raw transcripts to
understand *what* agents are looking for and *how*:

- **4 search archetypes**: API-contract hunts (triggered by runtime failure),
  pre-write API verification, project orientation, verification reads
- **Success signal**: retrieved span contains the signature/definition +
  agent verbalizes success ("Perfect! Now I understand...") + immediately Edits
- **The root cause**: agents grep SDK source because there are NO samples
  or API docs in their sandbox — only `CLAUDE.md` (process guide, zero API
  content) and the scaffold's `app/agent.py` (minimal function-tool demo)

### 3. Skills analysis (DONE)

Confirmed that agents-cli's fork **does** have strong adk-samples
instructions (`skills/google-agents-cli-adk-code/references/samples.md`
+ workflow skill Phase 1) — but they **post-date** the mined transcripts:
- Initial skills import: `104c944` (2026-04-14 17:56 UTC) — 1 day after the run
- `samples.md` with clone-and-study protocol: v1.3.1 (2026-08-04)
- The mined run: 2026-04-13 18:02 UTC

### 4. Consensus validation (IN PROGRESS)

3-validator pass over every eval-set row: 2 Claude subagent lenses
(query-faithfulness + ground-truth skeptic) + Gemini 3.1 Pro.

**Status:**
- **Claude track**: DONE — 175/175 rows, both lenses. Saved to
  `/home/ivanmkc/.claude/jobs/8c477bb8/tmp/claude_verdicts.json`
- **Gemini track**: IN PROGRESS — 1/12 batches complete, 3 concurrent
  Gemini CLI processes running. Script:
  `/home/ivanmkc/.claude/jobs/8c477bb8/tmp/gemini_validate.py`.
  Verdicts save to: `/home/ivanmkc/.claude/jobs/8c477bb8/tmp/gemini_verdicts/`
- **Merger**: `/home/ivanmkc/.claude/jobs/8c477bb8/tmp/merge_consensus.py`
  — run once both tracks finish.

### 5. agents-cli upgraded to v1.3.1 (DONE)

- Upgraded: `uv tool install --upgrade --prerelease allow google-agents-cli`
- Skills reinstalled: `agents-cli setup --skip-auth` — 51 skills including
  `google-agents-cli-adk-code` (with `samples.md` and `adk-python.md`)
- Verified: `agents-cli --version` → `1.3.1`

### 6. Benchmark re-run (NOT STARTED — needs launch)

Two partial runs were killed:
- v0.3.0 run (wrong version): `~/.agent_generator/benchmark_runs/2026-08-06_09-19-27_*` — 3 cases done, killed
- v1.3.1 Claude-only run: `~/.agent_generator/benchmark_runs/2026-08-06_09-26-01_*` — 2 cases done, killed

Both were Claude-only. The termchart book used **Claude + Gemini + AGY**
across all its analyses, so the re-run must include all available backends
for a comparable dataset.

## Data scale comparison

### What the termchart book used (the baseline to match)

| | Golden suite | Comparison suite | Total |
|---|---|---|---|
| **Unique tasks** | 6 | 18 (36 with EXP variants) | 24 base / 42 with variants |
| **Arms** | BASE, CLI, HINT | BASE, CLI | 2–3 |
| **Models** | Claude, Gemini, AGY | Claude, Gemini | 2–3 |
| **N** | 1 per cell (2 runs: A and B) | 1 | — |
| **Transcripts per run** | ~36 | ~72 | ~108 per run |

The mined run (`daily-pip-18`) produced **142 transcript files**
(71 Claude + 71 Gemini), yielding 236 retrieval episodes → 175 eval cases.

### What we need for a comparable v1.3.1 dataset

The `agents-cli-sandbox` case set resolves to **37 cases** (the same
36 base tasks + EXP variants as the mined run, plus one new case).
Running with multiple backends:

| generator-set | Models included | Expected transcripts |
|---|---|---|
| `agents-cli-claude` | Claude Haiku only | ~37 |
| `agents-cli` | Claude Haiku + Gemini Flash | ~74 |
| `full` or custom | + AGY, Codex, etc. | ~111+ |

**Minimum for comparability**: `agents-cli` (Claude + Gemini) → ~74
transcripts against the same 37 cases. This matches the mined run's
model coverage and is directly comparable.

## What's NOT done yet (TODOs)

### TODO 1: Launch the v1.3.1 benchmark with all backends

```bash
cd /home/ivanmkc/agent-generator
benchmark-runner \
  --case-set agents-cli-sandbox \
  --generator-set agents-cli \
  --name "skills-v1.3.1-retrieval-eval" \
  --concurrency 4 \
  --description "v1.3.1 skills impact: Claude + Gemini on agents-cli-sandbox"
```

`agents-cli` generator set = `Interactive_gemini_agents-cli` +
`Interactive_claude_agents-cli` (Gemini Flash + Claude Haiku).

Expected: ~74 transcripts, several hours at concurrency 4.

Results land in: `~/.agent_generator/benchmark_runs/<run-name>/results_detail/`

### TODO 2: Finish consensus validation and commit results

Once Gemini track finishes (check: `ls ~/.claude/jobs/8c477bb8/tmp/gemini_verdicts/ | wc -l` → should be 12):
```bash
python3 /home/ivanmkc/.claude/jobs/8c477bb8/tmp/merge_consensus.py
```
Commit results to `tools/evolve/retrieval/real_data/validation/`.

### TODO 3: Mine new transcripts and compare retrieval behavior

Once the v1.3.1 run completes:
```bash
# Mine episodes from the new run
PYTHONPATH=tools python3 -m evolve.retrieval.log_mining \
  ~/.agent_generator/benchmark_runs/<new-run>/ \
  --min-steps 2 --out new_episodes.jsonl

# Quick comparison
python3 -c "
import json
old = [json.loads(l) for l in open('old_episodes.jsonl')]
new = [json.loads(l) for l in open('new_episodes.jsonl')]
for label, eps in [('pre-skills (Apr 13, no skills)', old), ('post-skills (v1.3.1)', new)]:
    sdk = sum(1 for e in eps if any('site-packages' in a for _,a in e['calls']))
    total_steps = sum(e['steps'] for e in eps)
    total_tokens = sum(e['tokens'] for e in eps)
    print(f'{label}: {len(eps)} episodes, {sdk} SDK hunts, {total_steps} steps, {total_tokens} tokens')
"
```

Key metrics to compare:
- Episode count (fewer = skills reduced search behavior)
- SDK-grepping episodes (should drop to near-zero)
- Total retrieval tokens (the cost the book measures)
- Skill activation count (new signal not in old run)

### TODO 4: Test a MODIFIED agents-cli version

To compare a modified agents-cli fork against vanilla v1.3.1:

**Option A: `uv tool install --editable` (recommended for local dev)**
```bash
# Install your local fork as the active agents-cli
cd /home/ivanmkc/agents-cli
uv tool install --force --editable .

# Re-install skills from the local fork's skills/ directory
agents-cli setup --skip-auth --dev

# Run benchmark — sandbox uses whatever's on host PATH
cd /home/ivanmkc/agent-generator
benchmark-runner \
  --case-set agents-cli-sandbox \
  --generator-set agents-cli \
  --name "modified-cli-retrieval-eval" \
  --concurrency 4
```

**To revert to vanilla after testing:**
```bash
uv tool install --upgrade --prerelease allow google-agents-cli
agents-cli setup --skip-auth
```

**Option B: Via `act` with Podman (full container isolation)**
```bash
cd /home/ivanmkc/agent-generator
act workflow_dispatch \
  -W .github/workflows/eval-presubmit.yml \
  --input case_set=agents-cli-sandbox \
  --input generator_set=agents-cli \
  --input concurrency=4 \
  --container-daemon-socket /run/user/1000/podman/podman.sock \
  --container-options "--volume $HOME/.config/gcloud:/root/.config/gcloud:ro" \
  --env GOOGLE_APPLICATION_CREDENTIALS=/root/.config/gcloud/application_default_credentials.json
```
The composite action (`setup-benchmark-runner`) runs
`uv tool install google-agents-cli` from PyPI + `agents-cli setup --skip-auth`
inside the container. To inject a local fork instead, mount your checkout
and override the install step.

**How the sandbox works** (important for understanding what gets tested):
- The benchmark-runner runs on the host
- Each case runs inside a `bwrap` sandbox with `HOME=/tmp`
- Skills reach the sandbox via bind-mounts:
  `~/.claude/skills` → `/tmp/.claude/skills` (ro),
  `~/.gemini/extensions` → `/tmp/.gemini/extensions` (ro),
  `~/.agents/skills` → `/tmp/.agents/skills` (ro)
- `agents-cli` binary is on `PATH` via the host's uv tool install
- Upgrading the host's agents-cli + skills = upgrading what the sandbox sees

### TODO 5: Fold real-log cases into evolution fitness

The headline finding: the mock-evolved search_tool scores 1.0 on mock
but 0.03 recall on real logs. The next evolution run's fitness function
should include these real-log cases (or be validated against them) to
prevent overfitting. Implementation: extend `evaluate.py`'s
`_ensure_benchmark` to optionally load the real manifest alongside the
mock one, or add a real-log stage to the cascade.

## Architecture / key paths

```
agents-cli repo (this worktree)
├── tools/evolve/retrieval/
│   ├── log_mining.py          # mine episodes from agent-generator transcripts
│   ├── real_eval.py           # build eval cases from episodes
│   ├── real_replay.py         # replay + measure candidate tools
│   ├── real_data/
│   │   ├── METHODOLOGY.md     # full methodology writeup
│   │   ├── PROGRESS.md        # THIS FILE
│   │   ├── README.md          # data provenance + headline results
│   │   ├── episodes.jsonl.gz  # 236 mined episodes (pre-skills baseline)
│   │   ├── real_eval_manifest.json  # 175 eval cases
│   │   └── replay_report.json      # measurement results
│   ├── evaluator.py           # LocalEvaluator (fitness function)
│   ├── evaluate.py            # evolution harness entry point
│   ├── search_tool.py         # the evolvable search tool
│   ├── monorepo.py            # synthetic mock monorepo generator
│   └── baselines/default_grep.py  # grep-then-read-whole-files baseline
├── skills/                    # v1.3.1 skill tree (samples.md, adk-python.md, etc.)
└── tests/unittests/evolve/    # 50 tests, all passing

agent-generator repo (/home/ivanmkc/agent-generator/)
├── definitions/default/
│   ├── case_sets.yaml         # agents-cli-sandbox = 37 cases
│   ├── generator_sets.yaml    # agents-cli = Claude + Gemini
│   └── generators.yaml        # model + backend definitions
├── .github/
│   ├── workflows/_eval_shared.yml     # reusable benchmark workflow (act-compatible)
│   ├── workflows/eval-presubmit.yml   # dispatch entry point
│   └── actions/setup-benchmark-runner/action.yml  # installs agents-cli + skills
├── benchmark_runs/            # old runs (the mined pre-skills run lives here)
│   └── 2026-04-13_18-02-34_daily-pip-18-agents-cli-sandbox.../results_detail/
│       └── 142 transcript files (71 Claude + 71 Gemini)
└── ~/.agent_generator/benchmark_runs/  # new runs from benchmark-runner CLI
    ├── 2026-08-06_09-19-27_*  # killed v0.3.0 run (3 Claude cases, stale)
    └── 2026-08-06_09-26-01_*  # killed v1.3.1 Claude-only run (2 cases, incomplete)

Temp artifacts (current session only — will be lost when job is deleted)
└── /home/ivanmkc/.claude/jobs/8c477bb8/tmp/
    ├── packets.jsonl          # validation packets (episode + row + evidence)
    ├── batches/               # 12 batches of ≤15 for validators
    ├── claude_verdicts.json   # Claude 2-lens results (175/175 rows, DONE)
    ├── gemini_verdicts/       # Gemini 3.1 Pro results (1/12 batches done)
    ├── gemini_validate.py     # Gemini validation script (still running)
    ├── merge_consensus.py     # consensus merger (run after both tracks finish)
    ├── agent-project/         # scaffolded project corpus (for span verification)
    └── episodes.jsonl         # uncompressed episodes (copy of committed .gz)
```

## Key findings so far

1. **Pre-skills agents grep SDK source because there are no samples** —
   zero web access in any transcript, scaffold docs have zero API content,
   the `google/adk/examples/` module is the few-shot examples API (not
   sample code)
2. **Skills (v1.3.1) include strong sample-study instructions** — the
   `samples.md` clone-and-study protocol and `adk-python.md` API reference
   — but they post-date the mined transcripts by 1 day (initial import)
   to 4 months (samples.md)
3. **Early signal from partial re-runs**: in 5 cases completed with skills
   installed (3 at v0.3.0, 2 at v1.3.1), skill activation was observed
   (`Skill google-agents-cli-scaffold`), and **zero SDK source-grepping**
   occurred in any of them
4. **Mock benchmark overfitting**: evolved search_tool scores 1.0 on mock,
   0.03 recall on real logs — real-log cases must inform the next evolution
5. **Agents-cli v0.3.0 → v1.3.1 is a major gap**: the host was running
   v0.3.0 (April 2026) while latest is v1.3.1 (August 4, 2026); now upgraded
6. **Multi-model coverage required**: the termchart book used Claude +
   Gemini + AGY across its analyses; a Claude-only re-run is not comparable

## Known issues / blockers

- **Gemini validation is slow**: Gemini 3.1 Pro via the CLI takes ~5–10 min
  per batch (15 rows); 12 batches at concurrency 3 → ~30–60 min total.
  Still running as a background process (`gemini_validate.py` PID exists).
- **Old benchmark runs are partial**: the two killed runs
  (`09-19-27` and `09-26-01`) have 3 and 2 completed cases respectively.
  They can be mined for early signal but are not complete datasets.
- **`benchmark-runner` output buffering**: when piped through `| tail -5`
  (as the background Bash tool does), no output appears until the full
  run finishes. Run without the pipe, or check `results_detail/` directly.
- **Sandbox offline**: agents in the bwrap sandbox have no internet access,
  so `samples.md`'s `git clone github.com/google/adk-samples` instruction
  will fail. The skills still help (adk-python.md API reference is local),
  but the clone-and-study protocol specifically can't execute. A pre-cloned
  `/tmp/adk-samples` bind-mount would fix this.
