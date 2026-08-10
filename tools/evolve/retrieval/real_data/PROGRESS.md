# Progress — Real-Log Retrieval Eval Set & Skills Impact Measurement

Last updated: 2026-08-06 ~14:30 UTC
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
- `tools/evolve/retrieval/real_eval.py` — case builder (dependency-symbol + workspace-file)
- `tools/evolve/retrieval/real_replay.py` — measurement harness
- `tools/evolve/retrieval/real_data/` — episodes, manifest, replay report
- `tools/evolve/retrieval/real_data/METHODOLOGY.md` — full methodology (10 sections)
- `tests/unittests/evolve/test_log_mining.py` + `test_real_eval.py` + `test_real_replay.py` — tests (112+ total across the evolve suite), all passing

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

### 4. Consensus validation (DONE — committed to `validation/`)

3-validator pass over every eval-set row: 2 Claude subagent lenses
(query-faithfulness + ground-truth skeptic) + Gemini 3.1 Pro.

**Note**: the original Gemini-CLI-based validator hung — `subprocess.run`
timeouts killed the fnm shim but orphaned the node children (24 zombie
gemini-3.1-pro processes saturating quota, 1/12 batches in 68 min). Rewritten
against the google-genai SDK (Vertex ADC) → all 12 batches in ~2 min.

**Results** (see `validation/README.md` for full writeup):
- 175 rows → **63 valid / 41 flag / 71 invalid** (47 unanimous valid)
- Per family: workspace-file 60/41/63; dependency-symbol 3/0/8
- Dominant issues: `fragmented-episode` (44), `verification-read` (42),
  `span-wrong` (32) — mostly miner artifacts, fixable in `log_mining.py`
- **`real_eval_manifest.validated.json`** = the 63 consensus-valid tasks,
  annotated with `row_id` + `validation` — use THIS for fitness/eval

### 5. agents-cli upgraded to v1.3.1 (DONE)

- Upgraded: `uv tool install --upgrade --prerelease allow google-agents-cli`
- Skills reinstalled: `agents-cli setup --skip-auth` — 51 skills including
  `google-agents-cli-adk-code` (with `samples.md` and `adk-python.md`)
- Verified: `agents-cli --version` → `1.3.1`

### 6. Benchmark re-run (DONE — v2/v3, launched 2026-08-06 ~10:45 UTC)

**v2 = the new baseline config (user-confirmed 2026-08-06).** Run:
`skills-v1.3.1-retrieval-eval-v2`, case set `agents-cli-sandbox`
(71 cases), generator set `agents-cli` from MAIN's definitions —
Gemini (gemini-3.5-flash) + Claude (claude-opus-4-7) + Antigravity
(Gemini 3.1 Pro High) = 213 transcripts, concurrency 4. The model bump
vs the April mined run (Haiku→Opus, flash-preview→3.5-flash, +AGY) is
accepted; do NOT restart to chase model parity with April.
Log: `/home/ivanmkc/.claude/jobs/2cfe6680/tmp/benchmark_run_v2.log`;
results: `~/.agent_generator/benchmark_runs/2026-08-06_*retrieval-eval-v2*`.

**Harness gotcha (cost a run):** the host `benchmark-runner` was a stale
2026-05-14 uv-tool snapshot whose in-process AdkProjectWrapper crashes
every conversation against v1.3.1's ADK-2.x scaffolds
(`google.genai.types has no attribute 'TranslationConfig'`, genai 1.x/2.x
clash — fixed on main by PR #688, out-of-process wrapper). If
benchmark-runner ever mass-produces `fail_validation` with
`failure_actor: infrastructure`, reinstall from main:
`uv tool install --force "agent-benchmarks @ git+file:///home/ivanmkc/agent-generator@<main-sha>"`.

Killed runs (do not use): `09-19-27` (v0.3.0 CLI), `09-26-01` (Claude-only),
`09-35-34` (v1 — stale harness, 38 Gemini transcripts all infra-failed).

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

### TODO 1: ~~Launch the v1.3.1 benchmark~~ LAUNCHED (see section 6) — await completion

When it finishes, verify `results_detail/` has ~142 files across both
generators, then proceed to TODO 3.

### TODO 2: ~~Finish consensus validation and commit results~~ DONE

Committed to `tools/evolve/retrieval/real_data/validation/` +
`real_eval_manifest.validated.json` (63 consensus-valid tasks).

### TODO 2b: Fix the miner per validation findings — MINER FIXED, rebuild pending

`log_mining.py` fixes landed (TDD, 6 new tests):
- **verification-read**: retrieval calls that only revisit files the agent
  itself wrote earlier in the stream are skipped (tracked via action-tool
  file paths; rel/abs path matching; shell `cat`-of-own-write too)
- **fragmented-episode**: streamed assistant-message chunks are joined per
  utterance before use as episode context (root cause: Gemini transcripts
  emit one utterance as many `message` events; the old miner kept only the
  last chunk)

Re-mining the baseline run with the fixed miner: 236 → 209 episodes,
fragment-looking contexts 27 → 0, **SDK hunts unchanged (24)**.
`episodes.jsonl.gz` / the 175-row manifest are v1 provenance for the
committed validation — do NOT regenerate them in place. The TODO 3
comparison must mine BOTH runs fresh with the fixed miner.
Still pending (optional): rebuild eval set from v2 episodes + re-validate
(validator inputs/scripts preserved under `validation/`).

### TODO 2c: ~~Deep-dive findings implemented~~ DONE (2026-08-06)

From the v3 single-row deep dive (agent grepping agents-cli's own source
to debug port selection — mislabeled "SDK hunt", stale query, invisible
to the eval set):
- **Motivation tagging** (miner, structural): every episode now carries
  `motivation` — `failure-triggered` (preceding tool result matches a
  failure signature), `pre-write-verification` (mid-stream search ending
  in a write), `orientation` (early-stream sweep, first 5 tool calls, or
  unterminated). Derived from events, never narration.
- **`tool-internals` family** + `agents-cli-src` corpus: real_eval now
  routes `google/agents/cli` hunts to the CLI-source corpus (checked
  BEFORE the site-packages SDK rule), with the corpus snapshotted at
  google-agents-cli==1.3.1 in `real_data/corpus/` (260 files).
- Schema: optional `motivation` field on tasks; `FAMILY_TOOL_INTERNALS`.
- Verified on real data: v3 partial mines 378 episodes
  (107 orientation / 188 pre-write / 83 failure-triggered), builds 224
  cases incl. real tool-internals rows (e.g. `popen_resolved_detached`
  -> `_runner.py:234-271`). Baseline: 209 eps (69/101/39).

### TODO 3: ~~Mine new transcripts and compare retrieval behavior~~ DONE (2026-08-06)

v3 run: 146/213 complete (71 Claude + 71 Gemini + 4 AGY). Claude and
Gemini both complete; AGY still trickling (statistically negligible per
the audit — n=4, no April baseline). Comparison mined via:
```bash
PYTHONPATH=tools uv run python -m evolve.retrieval.compare_runs \
  ~/agent-generator/benchmark_runs/2026-04-13_*_agents-cli-sandbox \
  ~/.agent_generator/benchmark_runs/2026-08-06_*retrieval-eval-v3* \
  --label-a pre-skills --label-b post-skills \
  --out real_data/audit/v3_comparison.json
```

**Key results (Claude — within-harness, directionally trustworthy):**

| Category | April eps | v3 eps | Δ | April tok | v3 tok | Δ |
|----------|-----------|--------|---|-----------|--------|---|
| adk-sdk | 2 | 2 | 0% | 738 | 3,352 | +354% |
| workspace | 82 | 60 | -27% | 123,393 | 67,443 | -45% |
| log-inspection | 0 | 14 | new | 0 | 15,782 | new |
| tool-internals | 0 | 10 | new | 0 | 24,712 | new |
| skill-docs | 0 | 1 | new | 0 | 1,159 | new |
| Total Claude | 95 | 90 | -5% | 137,218 | 117,128 | -15% |
| Skill injection | 0 | 252,395 | +∞ | — | — | — |

**Key results (Gemini — episode counts only; token magnitudes unreliable
per audit Finding C5):**

| Category | April eps | v3 eps | Δ |
|----------|-----------|--------|---|
| adk-sdk | 22 | 8 | -64% |
| skill-docs | 0 | 53 | new |
| tool-internals | 0 | 10 | new |
| cli-help | 23 | 49 | +113% |
| workspace | 66 | 137 | +108% |
| log-inspection | 1 | 20 | new |
| Total Gemini | 114 | 286 | +151% |

**Interpretation (with audit caveats, see `audit/2026-08-06-council-audit.md`):**
- Gemini SDK hunts dropped 64% (22→8), but model confound (flash→3.5-flash),
  CLI change, and ADK version change all co-vary — cannot isolate skills.
- Gemini total episodes INCREASED 151% — new categories (skill-docs +53,
  log-inspection +20, tool-internals +10, workspace +71) outweigh the SDK drop.
  This suggests the newer model does more file-reading overall but shifts
  away from SDK reverse-engineering toward skill reading and workspace exploration.
- Claude retrieval cost dropped 15% in tokens but gained new categories
  (tool-internals +24.7k, log-inspection +15.8k). Workspace reading dropped 45%.
- **Skills are clearly being used** (252k Claude injection, 446 Gemini
  `activate_skill` calls — the latter invisible to the miner, see audit C7).
- **Net retrieval EXCLUDING skill injection**: Claude 137k→117k (-15%);
  **INCLUDING skill injection**: 137k→369k (+169%). Skills move cost from
  agent-initiated search to skill-injected reference material.

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

### TODO 5b: ~~Hermetic + generic manifest schema~~ DONE (2026-08-06)

Per user direction ("make it hermetic by pinning each data row to its
dependencies" + "make schema generic such that we can apply to non-adk"):
- `schema.py` — v1 manifest schema, framework-agnostic, typed stdlib
  dataclasses (repo has no pydantic; evolve subtree is dependency-free).
  Open corpora set w/ machine-readable pins (package/command/repo),
  open family vocabulary (`dependency-symbol`, `workspace-file`),
  `kind` derived at the evaluator boundary (never persisted).
- `corpus_store.py` — content-addressed deterministic archives
  (committed under `real_data/corpus/`, 2.6 MB), archive + per-file
  sha256 verified at materialization.
- Corpora regenerated from pins (google-adk 1.34.1; agents-cli v0.3.0
  scaffold) and verified 243/243 evidence spans byte-for-byte against
  `validation/packets.jsonl.gz` before snapshotting.
- `real_eval_manifest.validated.json` migrated to v1 (63 tasks, per-row
  file pins). `evaluate_real()` resolves corpora arg > `EVOLVE_REAL_ROOTS`
  (JSON env) > pinned store — zero-config hermetic scoring:
  default_grep 0.383 combined / 0.839 recall; mock-evolved search_tool
  0.181 / **0.033 recall** (overfitting now hermetically reproducible).
- 112+ tests passing (schema, store round-trip/tamper, resolution order, and all prior suites).

### TODO 5: ~~Fold real-log cases into evolution fitness~~ DONE

`evaluate.py` now has a real-log stage (TDD, 5 new tests):
- `evaluate_real(program, manifest_path=, corpus_roots=)` — scores a
  candidate against `real_eval_manifest.validated.json` (63 consensus-
  valid cases) per corpus; corpus roots resolved from function args,
  then `EVOLVE_REAL_ROOTS` (a single JSON env var mapping corpus name
  to root dir, e.g. `'{"adk-sdk": "/path/to/google/adk"}'`), then the
  pinned corpus store; unrooted corpora are skipped + counted in
  `num_skipped`
- `evaluate()` blends it when `EVOLVE_REAL_WEIGHT` is set:
  `combined = (1-w)*mock + w*real` (`mock_combined_score` + `real`
  sub-dict kept in the result). Default (env unset) = unchanged mock-only.

To use in the next evolution run: set `EVOLVE_REAL_WEIGHT=0.5` and
`EVOLVE_REAL_ROOTS='{"adk-sdk": "<site-packages>/google/adk", "agent-project": "<scaffolded-project>"}'`.
Alternatively, omit `EVOLVE_REAL_ROOTS` to use the pinned corpus store
(archives committed under `real_data/corpus/`).

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
└── tests/unittests/evolve/    # 112+ tests, all passing

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

Temp artifacts (session-ephemeral — paths below are from the original
build session and will not exist in other sessions; authoritative copies
of validation inputs are committed under `real_data/validation/`)
└── /home/ivanmkc/.claude/jobs/8c477bb8/tmp/  # EPHEMERAL, do not hardcode
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

### 7. Council-of-experts pipeline audit (DONE — 2026-08-06)

8-expert audit, 6/8 complete (stats + motivation pending). Full report:
`real_data/audit/2026-08-06-council-audit.md`. Headline:
- 5 critical, 10 major, 12 minor findings
- **Evolution NOT SAFE**: answer-key leak (proven 2.28× exploit), whole-file
  ground truth on 95.2% of tasks, redirect-write misclassification
- **Cross-harness comparison INVALID**: Gemini read_file outputs not recorded
  (~6-7× token undercount), `activate_skill` missing from `_SKILL_TOOLS`
- **Model confound**: 6 factors changed simultaneously (model, CLI, ADK,
  Python, scaffold, skills) — causal claims not supportable
- Fix queue: 3 P1 blockers → 7 P2 → 6 P3

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
