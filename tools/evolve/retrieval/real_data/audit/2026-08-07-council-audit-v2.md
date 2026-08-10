# Council-of-Experts Audit: Real-Log Retrieval Mining Pipeline (v2)

**Date:** 2026-08-07
**Auditors:** 8 independent expert lenses (all complete)
**Pipeline:** `tools/evolve/retrieval/` in `agents-cli` worktree `alphaevolve-retrieval`
**Scope:** Statistical methodology, episode segmentation, taxonomy, motivation tagging, ground-truth/corpus, eval design, comparison fairness, docs/reproducibility

## Executive Summary

The pipeline's intellectual framework is sound: episode mining, family taxonomy,
content-addressed corpus store, and hermetic eval schema are well-designed.
However, the audit surfaced **7 critical, 10 major, and 9 minor** findings
across 8 lenses that collectively mean:

1. **The eval is not safe for evolution fitness** — a candidate can read the
   answer key via `/proc/<ppid>/environ` or PATH-inferred filesystem traversal
   (Unit 6, F1-F2), and 95% of tasks reward whole-file retrieval (Unit 5, C2).
   Keep `EVOLVE_REAL_WEIGHT=0` until the sandbox is hardened.
2. **No causal claims can be made** — N=1 run per arm (Unit 1, S3), model jumped
   ~3 capability tiers between runs (Unit 7, F1), and 6 co-varying factors make
   isolation impossible.
3. **Cross-harness token comparisons are invalid** — Gemini `read_file` outputs
   are empty strings (~21% undercount, Unit 2, F1), and Gemini skill-injection
   tokens report zero (Unit 7, F2).
4. **The eval set is a single-file benchmark** — 71% of cases target
   `app/agent.py`, 81% of validated tasks share one span (Units 1+5+6).
   Evolution will converge on "always return agent.py."
5. **Documentation is ~40% stale** — family-name schism across manifests,
   reproduction commands produce different numbers, corpus section undocuments
   2 of 4 archives (Unit 8).

Within-harness comparisons (Claude baseline vs Claude+skills) are directionally
trustworthy for episode counts with heavy caveats. Absolute numbers should not
be cited as measuring general retrieval capability.

---

## Consolidated Findings

### Critical (7)

| ID | Unit | Finding | Evidence |
|----|------|---------|----------|
| C1 | 6-Eval | **`/proc/<ppid>/environ` leaks answer key** — no PID-namespace isolation; candidate reads parent env, navigates to manifest, returns perfect answers | Reproduced live: `open(f'/proc/{os.getppid()}/environ')` returns HOME, cwd reaches manifest |
| C2 | 6-Eval | **PATH forwards home directory** — `/home/ivanmkc/.local/bin` in PATH lets candidate infer home, walk to manifest | PATH verified verbatim in `evaluator.py:74-77` |
| C3 | 1-Stats | **Median-only headlines hide CV=338%** — token IQR spans 22x, worst episode costs 83x median. No dispersion reported anywhere in human-facing docs | Re-derived: median=878, mean=1495, std=5060, max/median=83x |
| C4 | 1-Stats | **N=1 run — zero statistical power** — all causal claims ("skills reduced retrieval") are unsupported at any significance level | Single run per arm; between-run variance unmeasurable |
| C5 | 7-Compare | **Model jumped ~3 tiers between runs** — April: Haiku 4.5 + Gemini 3-flash; v3: Opus 4-7 + Gemini 3.5-flash. Skills effect inseparable from model effect | Verified from `run_metadata.json` in both runs |
| C6 | 7-Compare | **Gemini skill-token accounting returns zero** — `skill_injection_tokens()` checks wrong event type; 223 `activate_skill` calls with ~16.7k uncounted tokens | `log_mining.py:488-508` checks `message` events; Gemini uses `skill_activation`+`tool_result` |
| C7 | 8-Docs | **Family-name schism** — original manifest: `sdk-symbol`/`project-file`; validated: `dependency-symbol`/`workspace-file`. Joining silently produces empty results | Two manifests use incompatible family names and corpus keys |

### Major (10)

| ID | Unit | Finding | Evidence |
|----|------|---------|----------|
| M1 | 5-GT | **95% of tasks have whole-file ground truth** — precision/MRR structurally inflated; only 3 tasks (4.8%) have sub-file spans | 60/63 validated tasks have start_line=1; only 10 unique spans across 85 references |
| M2 | 1-Stats | **71% of cases target `app/agent.py`** — headline recall is effectively single-file recall; evolution converges on trivial strategy | 125/175 cases; 35/63 validated tasks have agent.py as only span |
| M3 | 6-Eval | **81% of validated tasks share one span** — `app/agent.py:1-79` dominates the eval set | 51/63 tasks; corroborates M1 and M2 |
| M4 | 6-Eval | **`steps_median=1` hardcoded** — steps_reduction always reports 66.7%, a tautology | `real_replay.py:107` hardcodes 1; observed median is 3 |
| M5 | 1-Stats | **`chars/4` underestimates code tokens by 15-30%** — absolute counts are lower bounds; relative comparisons unaffected | Code tokenizes at ~3-3.5 chars/token; corpus is 39% code |
| M6 | 1-Stats | **"36 unique case IDs" is actually 65** — EXP variants collapsed into base names | 36 base + 29 EXP = 65; docs misleading, data correct |
| M7 | 4-Motiv | **`_ORIENTATION_WINDOW=5` uncalibrated** — orientation vs pre-write shares swing 4x across values 3-15 | Sensitivity table: orient% ranges from 10% (window=1) to 47% (window=20). Safe only via `motivation_group` |
| M8 | 7-Compare | **Category drift creates non-comparable buckets** — `skill-docs` (54 eps) and `tool-internals` (20 eps) exist only in v3 | Gemini episode rate increased 1.61→4.03; "retrieval reduced" only holds for adk-sdk bucket |
| M9 | 8-Docs | **Reproduction commands produce different numbers** — 211 eps vs committed 236; 144 cases vs committed 175 | Code updated 4 commits since committed artifacts; no doc explanation |
| M10 | 8-Docs | **"18-case comparison suite" is wrong** — actual: 36 base case IDs; "18" from run dir name | 36 BASE + 35 EXP = 71 per model; x2 = 142 files |

### Minor (9)

| ID | Unit | Finding | Evidence |
|----|------|---------|----------|
| m1 | 2-Seg | **Gemini `read_file` 0-char outputs → ~21% token undercount** — transcript-level data issue; within-harness diffs unaffected | 95% zero-output rate April, 72% v3; ~255K missing tokens |
| m2 | 3-Tax | **Classifier accuracy 85%** with 10% borderline (minority-marker precedence) and 5% wrong (single-call marker overrides dominant category) | 40-episode hand-label; 6 disagreements on category assignment |
| m3 | 4-Motiv | **`_FAILURE_SIGNATURE` 3.1% FP rate** — 7/228 episodes tagged failure due to "error"/"failed" in code/data; zero false negatives | All 7 FPs are structured data or type annotations |
| m4 | 5-GT | **`locate_definition` alphabetical tie risk** — theoretical for current data (0 collisions), growing risk with larger corpora | All 3 current symbols have unique definition sites |
| m5 | 6-Eval | **No held-out split for real-log eval** — compounds with C1/C2 if `EVOLVE_REAL_WEIGHT>0` | `evaluate.py:249` defaults to 0.0 |
| m6 | 6-Eval | **Economy term creates ceiling differences** but no perverse truncation incentive — scoring math is sound | 0.15 weight × ceiling diff; recall dominates |
| m7 | 7-Compare | **Smoke report is stale** — shows 3 AGY transcripts when 29 exist | Generated mid-run |
| m8 | 8-Docs | **Test count "112+" is stale** — actual: 123 | Verified via pytest |
| m9 | 8-Docs | **Docs list 2 corpora but 4 archives committed** — `agents-cli-src` and `adk-sdk-2.6.2` undocumented | `corpus/index.json` has 4 entries |

---

## Unit 3 (Taxonomy): Reconstructed from Scratch Data

Unit 3 completed its analysis but did not return a synthesized report. Findings reconstructed from its hand-labeling results (40 episodes, stratified by harness × category):

- **Accuracy: 85%** (34/40 correct)
- **Borderline: 10%** (4/40) — classifier fires on minority marker due to precedence rules. E.g., episode with 1/3 `--help` calls classified as `cli-help` when dominant activity is `workspace`.
- **Wrong: 5%** (2/40) — single-call marker at episode end overrides dominant category. E.g., 1/7 calls is `--help` but episode classified as `cli-help`.
- **Root cause:** Category precedence assigns based on *any* matching marker, not majority-vote across calls.
- **76 unmapped AGY domain tools** default to `neutral` — no actual episode bridging found (domain tools occur during simulation phases, not search).

---

## Verdicts

### (a) v3 Skills-Impact Comparison

**Directionally informative with heavy caveats.** Within-harness Claude episode counts (95→90, -5%) and token totals (-15%) are the most trustworthy signal, but:
- Model confound (Haiku→Opus) co-varies with skills introduction
- Gemini skill-token accounting is broken (reports 0)
- Category drift creates v3-only buckets that inflate episode counts
- Absolute precision/recall numbers are inflated by single-file concentration
- All numbers need dispersion measures (IQR or std) alongside medians

Claims that can be made: "We observed N% fewer Claude retrieval episodes in v3"
Claims that cannot be made: "Skills caused a N% reduction in retrieval"

### (b) Evolution Fitness

**Not safe in current form.** Three compounding problems:
1. **Sandbox escape** — candidate can read the answer key (C1, C2). Fix: PID-namespace or seccomp isolation.
2. **Degenerate fitness landscape** — 71% single-file concentration + 95% whole-file ground truth rewards "always return agent.py" (M1, M2, M3). Fix: stratified scoring or case rebalancing.
3. **No held-out split** — same 63 tasks for fitness and reporting (m5). Fix: train/test partition.

**Recommendation:** Keep `EVOLVE_REAL_WEIGHT=0` (current default). The mock benchmark is the load-bearing fitness signal. Real-log eval adds coverage but not discrimination until the eval set is diversified and the sandbox hardened.

---

## Prioritized Fix Queue

| Priority | Fix | Blocks | Effort |
|----------|-----|--------|--------|
| P0 | Sandbox: add `/proc` isolation + PATH sanitization | Evolution fitness | Low — seccomp or mount-namespace |
| P0 | Add dispersion measures to all headline reports | v3 comparison credibility | Low — median already computed |
| P1 | Diversify eval set — more sub-file spans, reduce agent.py concentration | Evolution fitness | Medium — requires re-mining + manual span annotation |
| P1 | Fix Gemini `skill_injection_tokens()` event type | Cross-harness comparison | Low — check `skill_activation` not `message` |
| P1 | Resolve family-name schism — deprecate old manifest | Tooling reliability | Low — delete old manifest or add adapter |
| P2 | Add train/test split to validated manifest | Evolution fitness | Medium |
| P2 | Stratified scoring (per-family, then weighted) | Evolution fitness | Medium |
| P2 | Update all docs to match current code/data | Reproducibility | Medium |
| P3 | Classifier: majority-vote instead of any-match precedence | Taxonomy accuracy | Low |
| P3 | Gemini read_file token correction factor | Cross-harness accuracy | Low — but transcript-level issue |

---

## Corrections to Prior Audit (2026-08-06)

Unit 8 verified three prior audit findings were **false positives**:
- ~~D5: "README says corpora not committed"~~ — README correctly says "committed"
- ~~D7: "PROGRESS uses EVOLVE_REAL_PROJECT_ROOT"~~ — PROGRESS correctly uses `EVOLVE_REAL_ROOTS`
- ~~D1: "real_replay.py CLI flags wrong"~~ — METHODOLOGY §10 step 3 correctly uses `--root NAME=PATH`
