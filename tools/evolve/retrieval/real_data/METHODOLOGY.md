# Methodology — Building the Real-Log Retrieval Eval Set

How the 175-case eval set in `real_eval_manifest.json` was constructed
from real agent transcripts, what its ground truth means, how candidate
retrieval tools are measured against it, and where its limits are.

---

## 1. Motivation and provenance

The *Token Economics of Agent Generation* analysis (termchart book,
Ch. 6½) showed that baseline agents spend a large share of their turns
**searching**: probing the environment, reverse-engineering the
installed SDK from `site-packages`, and re-reading their own half-built
project. Each such detour has a real, measured cost — tool-call steps,
tokens dragged into context, wall-clock seconds — that every later turn
re-pays (the "Central Law": each turn re-sends the whole growing
context).

The retrieval evolution loop in this directory previously optimized
against a **synthetic mock monorepo** (`monorepo.py`). This eval set
replaces / complements that with **replayed real workloads**: the exact
searches real agents ran, with the real costs they paid, so a candidate
retrieval tool's lift is measured against reality rather than a
generator's assumptions. The gap matters empirically: the mock-evolved
winner scores a perfect 1.0 on mock and 0.03 recall here (§8).

## 2. Source data

* **Run**: `agent-generator` benchmark run
  `2026-04-13_18-02-34_daily-pip-18-agents-cli-sandbox-e2001b6_agents-cli-sandbox`
  — the 18-case comparison suite (CLI-DEV/ENH/EVAL/EXT/FLOW/INIT/PLAT/
  STATE tasks), Claude and Gemini interactive harnesses, BASE and EXP
  arms → **36 transcript records** (`results_detail/*.json.gz`).
* **Record shape**: each record contains
  `generation_attempts[i].generation_events.turns[j].agent_events` —
  a flat event stream of `message` / `tool_use` / `tool_result` items
  with timestamps, tool names, inputs, and outputs.
* **Why this run**: it is the only locally available run with full
  `results_detail` transcripts (the book's golden runs A/B live in GCS,
  inaccessible at build time), and it is the same suite the book's
  flailing analysis (Ch. 6½.3) was computed on.

## 3. Stage 1 — Episode mining (`log_mining.py`)

### 3.1 Definitions

An **episode** is a maximal run of consecutive *retrieval-class* tool
calls, tolerating interleaved assistant messages (agents narrate
mid-search), ended by an *action-class* call. Intuition: one episode ≈
one information need pursued to the point of acting on it.

### 3.2 Tool taxonomy

Tool names are normalized across the harnesses (Claude names /
Gemini names / AGY names):

| class | examples | role |
| --- | --- | --- |
| retrieval | `Read`/`read_file`, `Grep`/`grep_search`, `Glob`/`glob`, `LS`/`list_directory`/`list_dir`, `WebFetch`/`web_fetch` | information seeking; extends the episode |
| shell (split) | `Bash`/`run_shell_command` | **classified per command**: read-only commands (`cat`, `grep`, `find`, `ls`, `pip show/list`, `which`, `python -c "import…print…"`, `--help`) are retrieval; everything else is action |
| action | `Edit`/`Write`/`write_file`/`replace`, installs, runs | ends the episode (records `ended_by`) |
| neutral | `TodoWrite`, `ExitPlanMode` | ignored |

Shell classification splits pipelines on `|`, `&&`, `;` and requires
**every stage** to be read-only (a `grep … > file` or `cat … && pip
install` is an action).

### 3.3 Cost accounting (matches the book's conventions)

* **steps** = number of retrieval calls in the episode.
* **tokens** = `chars / 4` over each call's `tool_input` **plus** its
  paired `tool_result.tool_output` (results are matched back to calls
  by `tool_call_id`). This measures what the detour dragged into
  context, which is what later turns re-pay.
* **wall_seconds** = last result timestamp − first call timestamp
  (ISO-8601 and epoch formats both handled).
* **context** = the last assistant message before the first retrieval
  call — the agent's own statement of its information need.

### 3.4 Filters

`min_steps = 2`: single-read episodes (one routine read before an
edit) are not searches and are dropped.

### 3.5 Yield

**236 episodes** from the 36 records. Distribution: median 3 steps /
878 tokens, p90 6 steps / 2,660 tokens, max 16 steps / 72,761 tokens.
Tool mix: `read_file` 185, `Read` 176, `Bash` 163, `run_shell_command`
123, `list_directory` 101, plus glob/grep variants.

## 4. Stage 2 — Trajectory analysis (ground-truth design)

Before deriving cases, full trajectories were traced through the raw
event streams to establish *what the agent's success signal actually
is*. Four recurring goals:

1. **API-contract hunts** (triggered by a runtime failure): e.g.
   Claude's callback hunt — `agents-cli run` fails → `grep -r
   "before_tool_callback"` over site-packages → narrow to
   `_SingleBeforeToolCallback: TypeAlias` in `agents/llm_agent.py` →
   read 6 lines → *"Perfect! Now I understand the signature"* → Edit.
   Success = the retrieved span contains the signature; behavioral
   confirmation = the rerun passes. Sometimes the full answer spans
   **definition + call site** (the same hunt later needed
   `flows/llm_flows/functions.py`'s `callback(tool=…, args=…,
   tool_context=…)` invocation).
2. **Pre-write API verification**: Gemini's A2A campaign — ~25
   interleaved greps, `ls -R`, `__init__.py` reads, and runtime probes
   (`python -c "import m; print(dir(m))"`) to establish where
   `AgentCard` lives before writing code. grep's `file:line` hit is
   immediately followed by a pinpoint read at that line.
3. **Project orientation**: post-scaffold reads of `app/agent.py`,
   `README.md`, guidance files "to understand the default structure".
   The **whole file is the answer**; search ends at the first
   `write_file`.
4. **Verification reads**: re-reading a file the agent itself just
   wrote. Quantified: of 552 project/SDK file reads across the run,
   404 are discovery, **98 (~18%) are verification-of-own-write**, 50
   are SDK reads.

These observations fix the ground-truth rules of Stage 3: symbol hunts
→ definition spans; orientation → whole-file spans; and they motivate
the flags used in Stage 7 (verification reads are not genuine search
workloads; import-probe fragmentation undercounts campaigns — §9).

## 5. Stage 3 — Case construction (`real_eval.py`)

Each episode is mapped to **at most one case**, in the same manifest
schema `LocalEvaluator` already scores (`query`, `expected_spans`,
`kind`), with the episode's measured cost attached as `observed`.

### 5.1 Family assignment

* Any call argument touching `site-packages/google/adk` →
  **sdk-symbol**.
* Otherwise, calls referencing files that exist in the project corpus
  → **project-file**.
* Neither → dropped (unreplayable).

### 5.2 sdk-symbol ground truth

1. **Symbol extraction**: identifier-shaped words are collected from
   call arguments and the context message; path tokens are excluded
   first. A word qualifies if CamelCase or snake_case (bare lowercase
   words are topics, not definitions); a stoplist removes shell verbs
   and generic typing words; dunders are excluded.
2. **AST location**: each candidate symbol is located in the SDK
   corpus by parsing every `*.py` and matching `ClassDef` /
   `FunctionDef` / `AsyncFunctionDef` **and module-level `Assign` /
   `AnnAssign` targets** (the latter added after trajectory analysis
   showed agents hunting `TypeAlias` definitions like
   `_SingleBeforeToolCallback`). The span is
   `(lineno … end_lineno)` of the definition node.
3. Episodes whose symbols all fail to locate produce **no case** (e.g.
   hunts for symbols defined in the external `a2a` package).

### 5.3 project-file ground truth

Call arguments are normalized (leading `/tmp/agent-workspace/`,
`agent-project/`, `./` stripped) and kept if the file exists in the
scaffold corpus. Ground truth is the **whole file**
(`start_line 1 … end_line N`), per the Stage-2 finding that orientation
reads consume entire files.

### 5.4 Query construction

The episode's own context message is the query (it is the agent's
authentic phrasing of the need — conversational, imperfect, exactly
what a deployed tool would receive). If located symbols are missing
from it, they are appended; if the context is empty, a synthetic
"Find the definition of X" query is used.

### 5.5 Skip rules (unreplayability)

Environment probes (`pip show`, `which`), CLI help lookups
(`agents-cli --help`), web fetches, and cases whose targets are absent
from the corpora are skipped: **236 episodes → 175 cases**
(164 project-file, 11 sdk-symbol). The skipped 26% remain in
`episodes.jsonl.gz` for future families (a help-text corpus would make
CLI-flag lookups replayable).

## 6. Corpora

Ground-truth spans are line-addressed against two version-pinned
corpora, committed as content-addressed archives in `corpus/`
(deterministic tar.gz + per-file sha256, verified at materialization —
see `evolve/retrieval/corpus_store.py`; every task additionally pins the
sha256 of each expected-span file). Regeneration recipes are encoded as
machine-readable `pin` entries in the manifest's `corpora` map, and both
regenerated trees were verified byte-for-byte against the validators'
`packets.jsonl.gz` span evidence (243/243 spans) before snapshotting:

* **sdk** — the installed `google-adk == 1.34.1` tree at
  `site-packages/google/adk` (542 `.py` files, ~11 MB) — the *same
  files the agents actually grepped* during the runs.
* **project** — a fresh default scaffold: `agents-cli scaffold create
  agent-project -y -s --agent-guidance-filename CLAUDE.md`
  (agents-cli v0.3.0). Proxy caveat: agents read *their own* evolving
  projects; the pristine scaffold is the common-denominator stand-in.

## 7. Stage 4 — Verification and validation

### 7.1 Mechanical audit (complete)

Every case checked programmatically: query non-empty; observed costs
present; every expected span's file exists in its corpus and
`1 ≤ start ≤ end ≤ file length`. **175/175 pass.** The packet builder
also asserts the manifest is exactly reproducible from the episodes
(same queries, same spans).

### 7.2 Consensus validation (three validators per row)

Because auto-derived ground truth can be systematically wrong in ways
a mechanical audit can't see, every row gets **three independent
judgments**:

* **Packets**: each row is paired with its source episode (context,
  calls, costs) *and* the actual text at each claimed span, so
  validators judge against evidence, not descriptions. 175 packets,
  12 batches of ≤ 15.
* **Validator A (Claude)** — query-faithfulness skeptic: is the query
  a fair, self-contained statement of the episode's information need
  (not vacuous, not leaking the answer's coordinates)?
* **Validator B (Claude)** — ground-truth skeptic: does the span text
  actually contain what the agent was looking for (wrong symbol/file;
  answer split across more spans than captured)?
* **Validator C (Gemini 3.1 Pro)** — independent model family, full
  rubric, same packets, strict-JSON verdicts.
* **Verdicts**: `valid` / `flag` (usable with caveat) / `invalid`
  (would mislead the benchmark). **Majority of 3 decides**; a 3-way
  split becomes `flag`; an issue tag counts as *agreed* when ≥ 2
  validators raise it. Results land in `validation/` next to this
  file.

## 8. Stage 5 — Measurement (`real_replay.py`)

A candidate tool (frozen CLI contract: `--repo --query --max-results
--token-budget` → `{"chunks":[{file,start_line,end_line,content,score}]}`)
is run once per case against the case's corpus, and measured on:

* **Relevance** (via `LocalEvaluator`'s verified scoring — chunk
  content is checked against the file on disk; fabricated content
  earns cost but no credit):
  * *recall* — coverage-weighted span coverage,
  * *precision* — fraction of returned tokens inside ground truth,
  * *MRR* — reciprocal rank of the first chunk that alone covers ≥ 50%
    of a span.
* **Cost**, compared with the observed agent detour:
  * *steps* — 1 for a tool call vs. observed median 3;
  * *tokens* — verified chunk tokens vs. observed chars/4;
  * *wall seconds* — measured per-query subprocess latency vs. the
    episode's timestamp span (episodes with 0-second spans are treated
    as missing timing data, not instant searches).

Snapshot results (also in `replay_report.json`):

| tool | family | recall | precision | MRR | tokens med | wall med |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| default_grep | project-file | 0.73 | 0.13 | 0.23 | 4,676 | 0.03 s |
| default_grep | sdk-symbol | 0.55 | 0.01 | 0.17 | 96,872 | 0.07 s |
| search_tool (evolved) | project-file | 0.00 | 0.05 | 0.00 | 29 | 0.04 s |
| search_tool (evolved) | sdk-symbol | 0.36 | 0.26 | 0.36 | 101 | 0.18 s |

Mock-vs-real contrast: the evolved tool scores combined 1.00 on the
mock monorepo it was evolved on (default_grep: 0.44) and collapses on
real project-file queries — the mock's queries name their targets
cleanly; real queries are conversational fragments.

## 9. Known limitations and threats to validity

1. **Quote-blind command splitting** (miner bug, found in trajectory
   review): `python3 -c "import x; print(dir(x))"` splits at the `;`
   inside quotes, and `grep "a\|b"` at the escaped `|`, so such probes
   are misclassified as actions. Effect: long campaigns fragment into
   several episodes and some search steps are lost → **observed
   steps/tokens are understated**; real detours are longer than the
   eval's baselines. Fix planned (shlex-aware splitting).
2. **Gemini `read_file` outputs are not recorded** in the transcripts
   (0 chars), so Gemini-side episode token costs are undercounted;
   grep/shell outputs are recorded for both harnesses.
3. **Verification reads** (~18% of project reads) are inside the
   project-file family; they are genuine transcript behavior but not
   genuine *search* — the consensus pass tags them so they can be
   split into their own family or excluded.
4. **Single run, two harnesses**: one N=1 run, Claude+Gemini only
   (AGY's baseline is infra-blocked per the book); case-mix follows
   this suite's 18 tasks.
5. **Family imbalance mirrors reality**: 164 project-file vs 11
   sdk-symbol, and 125 cases target `app/agent.py` — deliberate
   frequency weighting, but per-family metrics should be read
   separately (overall averages are dominated by project-file).
6. **`chars/4` token estimate** (book convention) is consistent across
   arms but approximate.
7. **Corpus drift**: spans are pinned to `google-adk 1.34.1` and an
   agents-cli v0.3.0 scaffold; other versions shift line numbers.
   Rebuild the manifest (commands in `README.md`) rather than reusing
   spans across versions.
8. **sdk-symbol captures definitions only**; trajectory analysis shows
   some hunts also need the call-site span — a planned extension.

## 10. Reproduction

```bash
# 1. Mine episodes
PYTHONPATH=tools python3 -m evolve.retrieval.log_mining <run_dir>... \
    --min-steps 2 --out episodes.jsonl

# 2. Build cases against version-pinned corpora
PYTHONPATH=tools python3 -m evolve.retrieval.real_eval episodes.jsonl \
    --sdk-root <site-packages>/google/adk \
    --project-root <scaffolded-project> \
    --out real_eval_manifest.json

# 3. Replay + measure candidate tools
PYTHONPATH=tools python3 -m evolve.retrieval.real_replay real_eval_manifest.json \
    --sdk-root ... --project-root ... --out replay_report.json
```

Tests: `tests/unittests/evolve/test_log_mining.py`,
`test_real_eval.py`, `test_real_replay.py` (TDD; the eval-set builder
was implemented against pre-written failing tests).
