# Council-of-Experts Audit: Real-Log Retrieval Mining Pipeline

**Date:** 2026-08-06
**Auditors:** 8 independent expert lenses (6 complete, 2 pending)
**Pipeline:** `tools/evolve/retrieval/` in `agents-cli` worktree `alphaevolve-retrieval`
**Scope:** Episode mining, eval schema, corpus integrity, comparison fairness, docs

## Executive Summary

The pipeline's intellectual framework is sound: the episode-mining concept,
family taxonomy, content-addressed corpus store, and hermetic eval schema are
well-designed and internally consistent where they overlap. However, the audit
surfaced **5 critical, 10 major, and 12 minor** findings across the 6 completed
lenses that collectively mean:

1. **The eval is not safe for evolution** in its current form — a candidate tool
   can read the answer key and inflate its score 2.28x (proven exploit), and
   95.2% of tasks reward whole-file retrieval (the behavior the project exists to
   eliminate).
2. **Cross-harness token comparisons are invalid** — Gemini `read_file` outputs
   are not recorded (~6-7x token undercount), and Gemini/AGY skill-injection
   tokens are invisible (`activate_skill` missing from `_SKILL_TOOLS`).
3. **The pre/post-skills comparison cannot isolate the skills effect** — the model,
   CLI version, ADK version, Python version, and scaffold command all changed
   simultaneously between April and v3.
4. **Documentation is ~40% stale** — family names, CLI flags, transcript counts,
   test counts, and corpus-commitment status are inconsistent across 5 docs.

Within-harness comparisons (Claude baseline vs. Claude+skills) are directionally
trustworthy for episode counts and steps, with caveats about differential failure
rates. The pipeline is a strong foundation that needs targeted fixes before its
outputs can drive evolution fitness or public claims.

## Findings Table

Findings are deduplicated across audit units. Where multiple units flagged the
same issue, all are credited. Ordered by impact on (a) evolution fitness and
(b) v3 comparison validity.

### Tier 1 — Evolution Fitness Blockers

| # | Severity | Finding | Units | Impact |
|---|----------|---------|-------|--------|
| C1 | CRITICAL | **Answer-key leak**: candidate subprocess can read the manifest via HOME/VIRTUAL_ENV/sys.executable (proven: 0.873 combined vs 0.383 baseline = 2.28x inflation). `test_reward_hacking.py` has zero coverage of the real-eval path. | evaldesign | Exploit invalidates any evolution run on the real eval set. |
| C2 | CRITICAL | **Whole-file ground truth**: `_file_span()` always returns start_line=1, end_line=N. 60/63 tasks (95.2%) are whole-file spans. Evolution is trained to drag whole files — the exact behavior the project exists to eliminate. Only 3 dependency-symbol tasks have sub-file spans. | groundtruth | Fitness signal rewards the wrong behavior on 95.2% of tasks. |
| C3 | MAJOR | **Redirect-write misclassification**: `_shell_is_readonly` does not detect `>` / `>>` redirects. `cat > file`, `echo >> file` classified as retrieval instead of action. 57 commands affected across both runs; each one extends an episode instead of ending it. | segmentation, taxonomy | Inflates episode length and token counts; biases fitness if fitness penalizes long episodes. |
| C4 | MAJOR | **Quote-blind shell splitting**: `re.split(r"\||&&|;")` splits inside quoted arguments. `grep -E "a|b"` and `tree -I ".venv|__pycache__"` are split into stages and misclassified as action. 78 commands (5.9% of shell commands in April) misclassified as action when they are retrieval. | segmentation, taxonomy | Causes premature episode breaks; fitness sees shorter, more numerous episodes than ground truth. |

### Tier 2 — Comparison Validity

| # | Severity | Finding | Units | Impact |
|---|----------|---------|-------|--------|
| C5 | CRITICAL | **Gemini token undercount ~6-7x**: Gemini `read_file` tool_output is recorded as empty string (April) or summary stub (v3), never actual content. Claude tokens are 97.6% output-derived; Gemini are only 15%. Cross-harness token comparisons are meaningless. | segmentation, fairness | Any Claude-vs-Gemini token comparison is an artifact of recording, not behavior. |
| C6 | CRITICAL | **Model confound**: Claude Haiku 4.5→Opus 4-7, Gemini flash→3.5-flash, agents-cli v0.3→v1.3.1, ADK v1.20→v1.34.1, Python 3.11→3.12, scaffold command changed — all simultaneously. No single factor can be isolated as the cause of any observed difference. | fairness | No causal "skills reduced retrieval" claim is supportable. |
| C7 | CRITICAL | **Gemini skill-injection tokens invisible**: Gemini's skill tool is `activate_skill` (446 calls in v3), not in `_SKILL_TOOLS`. Reported skill-injection total (252k tokens) is Claude-only. Gemini's skill bill is unknown. | fairness | Cross-harness skill-cost comparison is one-sided. |
| C8 | MAJOR | **AGY skill paths miscategorized**: AGY reads skills from `/tmp/.gemini/antigravity-cli/skills/...`, not matching any `_SKILL_DIR_MARKERS`. AGY skill reads fall through to workspace/other. AGY skill tokens also invisible (same `_SKILL_TOOLS` gap). | fairness | AGY skill usage completely invisible to comparison. |
| C9 | MAJOR | **Pass-rate confound in per-transcript normalization**: v3 Claude pass rate 37/71 vs April 48/71. Failed cases have fewer simulation streams, mechanically reducing v3 episode counts independently of skills. Per-transcript normalization does not account for this. | fairness | A portion of apparent retrieval reduction is a failure-rate artifact. |
| C10 | MAJOR | **Category precedence collisions**: 24.8% of episodes (146/588) match 2+ categories. Multi-step episodes where 1 call reads an `--help` page and 9 calls read workspace files are categorized entirely as cli-help. Per-category token shares are upper bounds, not precise measurements. | taxonomy | "45% of tokens are SDK hunts" overstates; per-category token shares conflate dominant with incidental activity. |

### Tier 3 — Pipeline Correctness (noise, not systematic distortion)

| # | Severity | Finding | Units | Impact |
|---|----------|---------|-------|--------|
| C11 | MAJOR | **Per-row pins stripped at eval time**: `to_evaluator_dict()` drops `pins` — sha256 file hashes are documentation-only, never verified at scoring time. | groundtruth | Wrong-corpus scoring is undetectable at runtime. |
| C12 | MAJOR | **Wrong-generation corpus routing possible**: `snapshot()` overwrites `index[name]`; no version enforcement. Manifest declares `corpus: "adk-sdk"` — a re-snapshot under the same name silently replaces the entry. | groundtruth | Latent design flaw; correct only by naming convention today. |
| C13 | MINOR | **`sed -n` dead entry** in `_READONLY_PROGRAMS`: `_leading_program` returns single token `"sed"`, so `"sed -n"` never matches. All sed commands fall to action. Zero impact in current data (no sed usage). | segmentation | Latent bug; no current impact. |
| C14 | MINOR | **`min_steps` default inconsistency**: `segment_episodes` defaults 1; `mine_record`/`mine_run_dir`/CLI default 2. API trap for direct callers. | segmentation | No current callers affected; API inconsistency. |
| C15 | MINOR | **AGY `run_command` key mismatch**: AGY stores command in `CommandLine`, not `command`/`cmd`. All 23 AGY shell calls classified as action. 2 are `--help` lookups that should be retrieval. | taxonomy | 2 misclassified calls across 3 AGY transcripts; negligible. |
| C16 | MINOR | **`read_url_content` missing from `_RETRIEVAL_TOOLS`**: AGY URL-fetch tool falls through to neutral. 2 calls lost. | taxonomy, segmentation | Negligible; only 2 calls observed. |
| C17 | MINOR | **Path traversal on Python < 3.12**: `tarfile.data_filter` only applied when available. On 3.11, `extractall()` runs without a filter. Low practical risk (archives are self-created, hash-checked). | groundtruth | Defense-in-depth gap; no current exploit path. |
| C18 | MINOR | **`locate_definition` first-match risk**: 224/2,689 SDK symbols have multiple definitions. Current 3 eval tasks use unambiguous symbols, but method names like `run_async` (37 defs) would hit wrong file. | groundtruth | Latent; not triggered by current eval set. |

### Tier 4 — Documentation Drift

| # | Severity | Finding | Units | Impact |
|---|----------|---------|-------|--------|
| D1 | CRITICAL | **`real_replay.py` CLI flags wrong in METHODOLOGY §10 and README**: docs say `--sdk-root`/`--project-root`; code accepts `--root NAME=PATH`. Reproduction step 3 fails with unrecognized-argument error. | docs | Blocks reproducibility. |
| D2 | CRITICAL | **Family names differ**: docs + legacy manifest say `sdk-symbol`/`project-file`; validated manifest + `schema.py` say `dependency-symbol`/`workspace-file`. Code that uses schema constants will hit silent mismatches against docs. | docs | New contributor confusion; code/doc mismatch. |
| D3 | MAJOR | **"36 transcripts"** in METHODOLOGY §2 and README: actual count is 142 files (71 Claude + 71 Gemini). 36 is unique case IDs, not transcript files. | docs | Misleading count in canonical methodology doc. |
| D4 | MAJOR | **Episode/case counts stale**: METHODOLOGY says 236 episodes / 175 cases; today's miner produces 209 / 141. PROGRESS notes the divergence but METHODOLOGY reads as current. | docs | Reproduction produces different numbers with no in-doc explanation. |
| D5 | MAJOR | **README says "Corpora not committed"** but `corpus/` contains 4 committed archives (7.0 MB). METHODOLOGY §6 correctly says committed. | docs | Reader tries to regenerate existing corpora. |
| D6 | MAJOR | **6 different test counts** across docs (34, 50, 56, 76, 86, 91). None cross-reference each other. Actual: 91. | docs | Trust erosion; unclear which number is current. |
| D7 | MAJOR | **PROGRESS references non-existent env vars** `EVOLVE_REAL_PROJECT_ROOT`/`EVOLVE_REAL_SDK_ROOT`. Code only defines `EVOLVE_REAL_ROOTS` (single JSON var). | docs | Reader sets ignored env vars. |
| D8 | MINOR | **`--cli-src-root` flag undocumented** in any reproduction recipe. Added in TODO 2c for tool-internals family. | docs | Reader wanting all 3 families misses this flag. |
| D9 | MINOR | **PROGRESS §6 still says "RUNNING"** for v2 run. Stale status. | docs | Misleading active-run indicator. |
| D10 | MINOR | **Ephemeral job paths** in PROGRESS architecture tree and committed `merge_consensus.py`. | docs | Paths unreachable; script unreproducible from committed location. |

## Pending Audit Units

| Unit | Status | Notes |
|------|--------|-------|
| Statistical methodology | Agent ran, scratch files present | Report not yet received |
| Motivation-tagging validity | Agent ran, extensive scratch files (1,246 lines) | Report not yet received |

These will be incorporated as an addendum when received. Findings from these units
may add to Tiers 1-4 above.

## Verdict by Use Case

### (a) v3 Skills-Impact Comparison

**Conditionally trustworthy for within-Claude directional claims only.** The report
can say "Claude episode counts and steps decreased between April and v3" with
prominent caveats that 6 factors changed simultaneously (model, CLI, ADK, Python,
scaffold, skills) and that the lower v3 pass rate (37/71 vs 48/71) mechanically
reduces episodes. Cross-harness comparisons (Claude vs Gemini tokens) are invalid
due to the Gemini recording gap. Any "skills reduced retrieval" headline must list
all co-varying factors; a causal claim is not supportable from this design.

### (b) Evolution Fitness

**Not safe in current form.** Three blockers must be resolved first:

1. **Answer-key leak** (C1): candidate subprocess can read the manifest and score
   2.28x above the honest baseline. Fix: strip HOME/VIRTUAL_ENV from the env
   whitelist, or load the manifest into memory and delete from disk before running
   candidates.
2. **Whole-file ground truth** (C2): 95.2% of tasks reward dragging whole files.
   Fix: derive sub-file spans from the agent's actual read targets (episode calls
   and validation packet `span_evidence` contain the needed information).
3. **Redirect-write misclassification** (C3): write commands extend episodes
   instead of ending them. Fix: detect `>` / `>>` redirects in `_shell_is_readonly`.

After those three, the fitness signal is usable with ~5% noise from quote-blind
splitting (C4).

## Fix Queue (ordered by impact)

Priority 1 — before any evolution run:
- [ ] C1: Strip HOME/VIRTUAL_ENV/PYTHONHOME from evaluator env whitelist; add real-eval exploit tests to `test_reward_hacking.py`
- [ ] C2: Replace whole-file spans with sub-file spans derived from episode call targets and validation evidence
- [ ] C3: Detect `>` / `>>` redirects in `_shell_is_readonly` (classify as action, not retrieval)

Priority 2 — before publishing v3 comparison:
- [ ] C5/C7/C8: Add `activate_skill` to `_SKILL_TOOLS`; add AGY skill-path markers to `_SKILL_DIR_MARKERS`
- [ ] C6: Document all co-varying factors prominently in any comparison output
- [ ] C4: Quote-aware shell splitting (use `shlex` to parse, then classify each stage)
- [ ] C10: Consider per-call category assignment instead of per-episode first-match
- [ ] D1-D7: Fix all doc/code drift (family names, CLI flags, counts, env vars)

Priority 3 — hardening:
- [ ] C11: Wire per-row pin verification into `evaluate_real()` at scoring time
- [ ] C12: Include corpus version in index key (e.g., `adk-sdk@1.34.1`)
- [ ] C15/C16: Add AGY tool names (`read_url_content`, `CommandLine` key) to taxonomy

## Cross-Reference: Shared Findings Across Units

Several findings were independently discovered by multiple units, which increases
confidence:

- **Gemini token undercount**: segmentation (#1) and fairness (#2) both quantified
  this; segmentation estimated ~113% undercount using Claude proxy, fairness
  measured ~6-7x by comparing output-derived token fractions.
- **Redirect-write bug**: segmentation (#3) and taxonomy (#1) both found `cat > file`
  misclassified as retrieval, both counting 55-57 affected commands.
- **`_SKILL_TOOLS` gap**: fairness (#1) found `activate_skill` missing, verified
  446 Gemini calls; coordinator independently confirmed.
- **Whole-file spans + economy**: groundtruth (#1) showed 95.2% whole-file spans;
  evaldesign (#4) computed optimal strategies proving economy never makes partial
  returns optimal on the current set.
