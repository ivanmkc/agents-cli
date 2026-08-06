# Progress — Real-Log Retrieval Eval Set & Skills Impact Measurement

Last updated: 2026-08-06 ~09:30 UTC
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
(`2026-04-13_18-02-34_daily-pip-18-agents-cli-sandbox` — 36 transcripts,
Claude + Gemini, 18 comparison cases × BASE + EXP arms). Built 175
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

### 5. Skills-v1.3.1 benchmark re-run (IN PROGRESS — NEEDS RESTART)

**PROBLEM**: the running benchmark uses **agents-cli v0.3.0** (host
install), not the latest v1.3.1. Must be upgraded and restarted.

Run location:
`/home/ivanmkc/.agent_generator/benchmark_runs/2026-08-06_09-19-27_skills-v1_3_1-retrieval-eval_agents-cli-sandbox/`

Early results (3 cases completed with v0.3.0) already show the skill
difference: `Skill google-agents-scaffold` activated, zero SDK
source-grepping. But the version is wrong.

**To fix and restart:**
```bash
# 1. Kill the running benchmark
kill $(ps aux | grep benchmark-runner | grep -v grep | awk '{print $2}')

# 2. Upgrade agents-cli to latest
uv tool install --upgrade --prerelease allow google-agents-cli

# 3. Re-install skills at latest version
agents-cli setup --skip-auth

# 4. Verify
agents-cli --version   # should say 1.3.1

# 5. Restart the benchmark
cd /home/ivanmkc/agent-generator
benchmark-runner \
  --case-set agents-cli-sandbox \
  --generator-set agents-cli-claude \
  --name "skills-v1.3.1-retrieval-eval" \
  --concurrency 2 \
  --description "Re-run with v1.3.1 skills installed to measure retrieval behavior change"
```

## What's NOT done yet (TODOs)

### TODO 1: Finish consensus validation and commit results

Once Gemini track finishes:
```bash
python3 /home/ivanmkc/.claude/jobs/8c477bb8/tmp/merge_consensus.py
```
Commit results to `tools/evolve/retrieval/real_data/validation/`.

### TODO 2: Restart benchmark with latest agents-cli v1.3.1

See "To fix and restart" above. This is the vanilla (latest release)
baseline. The sandbox bind-mounts the host's `agents-cli` binary and
`~/.claude/skills`, so upgrading the host upgrades the sandbox.

### TODO 3: Test a MODIFIED agents-cli version

To compare a modified agents-cli fork against vanilla:

**Option A: `uv tool install --editable` (recommended)**
```bash
# Install your local fork as the active agents-cli
cd /home/ivanmkc/agents-cli
uv tool install --force --editable .

# Re-install skills from the local fork's skills/ directory
agents-cli setup --skip-auth --dev
# or: agents-cli setup --skip-auth --skills-source ./skills

# Run benchmark — it will use the local fork
cd /home/ivanmkc/agent-generator
benchmark-runner \
  --case-set agents-cli-sandbox \
  --generator-set agents-cli-claude \
  --name "modified-cli-retrieval-eval" \
  --concurrency 2
```

**Option B: Via `act` with Docker/Podman container (full isolation)**
```bash
cd /home/ivanmkc/agent-generator
act workflow_dispatch \
  -W .github/workflows/eval-presubmit.yml \
  --input case_set=agents-cli-sandbox \
  --input generator_set=agents-cli-claude \
  --input concurrency=2 \
  --container-daemon-socket /run/user/1000/podman/podman.sock \
  --container-options "--volume $HOME/.config/gcloud:/root/.config/gcloud:ro" \
  --env GOOGLE_APPLICATION_CREDENTIALS=/root/.config/gcloud/application_default_credentials.json
```
The composite action runs `uv tool install google-agents-cli` from PyPI
inside the container. To inject a local fork, override the install step
or pre-mount the fork.

### TODO 4: Mine new transcripts and compare retrieval behavior

Once a run completes with v1.3.1:
```bash
# Mine episodes from the new run
PYTHONPATH=tools python3 -m evolve.retrieval.log_mining \
  /home/ivanmkc/.agent_generator/benchmark_runs/<new-run>/ \
  --min-steps 2 --out new_episodes.jsonl

# Compare: skill activations, sdk-grepping volume, episode counts
python3 -c "
import json
old = [json.loads(l) for l in open('episodes.jsonl')]
new = [json.loads(l) for l in open('new_episodes.jsonl')]
for label, eps in [('pre-skills', old), ('post-skills', new)]:
    sdk = sum(1 for e in eps if any('site-packages' in a for _,a in e['calls']))
    print(f'{label}: {len(eps)} episodes, {sdk} SDK hunts')
"
```

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
│   │   ├── episodes.jsonl.gz  # 236 mined episodes
│   │   ├── real_eval_manifest.json  # 175 eval cases
│   │   └── replay_report.json      # measurement results
│   ├── evaluator.py           # LocalEvaluator (fitness function)
│   ├── evaluate.py            # evolution harness entry point
│   ├── search_tool.py         # the evolvable search tool
│   ├── monorepo.py            # synthetic mock monorepo generator
│   └── baselines/default_grep.py  # grep-then-read-whole-files baseline
├── skills/                    # v1.3.1 skill tree (samples.md, adk-python.md, etc.)
└── tests/unittests/evolve/    # 50 tests

agent-generator repo
├── /home/ivanmkc/agent-generator/
│   ├── benchmark_runs/        # historical + active run output
│   ├── definitions/default/   # case definitions, generator sets, case sets
│   └── .github/workflows/     # act-compatible CI workflows
└── ~/.agent_generator/benchmark_runs/  # benchmark-runner output directory

Temp artifacts (current session only)
└── /home/ivanmkc/.claude/jobs/8c477bb8/tmp/
    ├── packets.jsonl          # validation packets (episode + row + evidence)
    ├── batches/               # 12 batches of 15 for validators
    ├── claude_verdicts.json   # Claude 2-lens results (175 rows)
    ├── gemini_verdicts/       # Gemini 3.1 Pro results (1/12 done)
    └── merge_consensus.py     # merger script
```

## Key findings so far

1. **Pre-skills agents grep SDK source because there are no samples** —
   zero web access, scaffold docs have zero API content, the `adk/examples/`
   module is the few-shot API not code samples
2. **Skills (v1.3.1) include strong sample-study instructions** but
   post-date the mined transcripts by 1 day (initial import) to 4 months
   (samples.md)
3. **Early signal from v0.3.0 re-run**: skill activation observed, zero
   SDK source-grepping in 3 completed cases — the shift is real even with
   the older version
4. **Mock benchmark overfitting**: evolved search_tool scores 1.0 on mock,
   0.03 recall on real logs; real-log cases must inform the next evolution
