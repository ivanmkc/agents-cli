"""Run the retrieval-layer evolution experiment with the AlphaEvolve client.

Mirrors the official example structure from the AlphaEvolve-on-Google
Cloud codelabs (github.com/Google-Cloud-AI/alphaevolve-on-googlecloud):
``AlphaEvolveClient`` -> ``AlphaEvolveExperiment`` -> seed program ->
``run_controller_loop``. Requires the ``alpha_evolve`` client library
and a Gemini Enterprise app with AlphaEvolve access; configure via env
vars (PROJECT_ID, GE_APP_ID, ...) or a .env file.

Usage::

    PYTHONPATH=tools python tools/evolve/retrieval/run_evolution.py
"""

from __future__ import annotations

import asyncio
import functools
import logging
import os
import sys
from pathlib import Path

try:
    from evolve.retrieval.alpha_evolve_adapter import (
        PRIMARY_METRIC,
        retrieval_evaluation,
    )
except ImportError:  # invoked standalone
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from evolve.retrieval.alpha_evolve_adapter import (
        PRIMARY_METRIC,
        retrieval_evaluation,
    )

# Gemini Enterprise / AlphaEvolve connection settings.
PROJECT_ID = os.getenv("PROJECT_ID", "gcp-project-id")
LOCATION = os.getenv("LOCATION", "global")
COLLECTION = os.getenv("COLLECTION", "default_collection")
GE_APP_ID = os.getenv("GE_APP_ID", "your-engine-id")
ASSISTANT = os.getenv("ASSISTANT", "default_assistant")
BASE_URL = os.getenv("BASE_URL", "discoveryengine.googleapis.com")

# Mutation models: weighted mixture of a fast and a strong model.
MODEL_1 = os.getenv("MODEL_1", "gemini-3.5-flash")
MODEL_2 = os.getenv("MODEL_2", "gemini-3.1-pro-preview")
MODEL_1_WEIGHT = float(os.getenv("MODEL_1_WEIGHT", "0.7"))
MODEL_2_WEIGHT = float(os.getenv("MODEL_2_WEIGHT", "0.3"))

MAX_PROGRAMS_GENERATED = int(os.getenv("MAX_PROGRAMS_GENERATED", "50"))
MAX_PROGRAMS_EVALUATED = int(os.getenv("MAX_PROGRAMS_EVALUATED", "50"))
CONCURRENCY = int(os.getenv("CONCURRENCY", "4"))

# Benchmark environment settings (see evaluate.py / monorepo.py).
BENCH_SEED = int(os.getenv("BENCH_SEED", "0"))
BENCH_SCALE = int(os.getenv("BENCH_SCALE", "3"))

SEED_PROGRAM_PATH = Path(__file__).with_name("search_tool.py")

PROBLEM_DESCRIPTION = """\
Evolve the code-search tool that coding agents use to retrieve context
from large monorepos. Only the region between # EVOLVE-BLOCK-START and
# EVOLVE-BLOCK-END in search_tool.py may change; the CLI, chunk schema,
and token-budget enforcement outside the block are frozen scaffolding.

Maximize combined_score = 0.5*recall + 0.3*token-precision + 0.2*mrr on
a benchmark of definition/usage/config retrieval tasks. Returning whole
files is heavily penalized via precision; the first relevant chunk
should be ranked first. Ideas with headroom: AST-aware chunking,
import-graph traversal for usage queries, camelCase/snake_case sub-token
matching, two-pass filtering.
"""


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    try:
        from alpha_evolve.client import AlphaEvolveClient
        from alpha_evolve.controller import run_controller_loop
        from alpha_evolve.experiment import AlphaEvolveExperiment
    except ImportError:
        print(
            "The 'alpha_evolve' client library is not installed. Install it "
            "from the AlphaEvolve-on-Google-Cloud repository "
            "(github.com/Google-Cloud-AI/alphaevolve-on-googlecloud) and "
            "configure Gemini Enterprise access. For a runner-free fitness "
            "check, use: python tools/evolve/retrieval/evaluate.py "
            "tools/evolve/retrieval/search_tool.py",
            file=sys.stderr,
        )
        return 1

    client = AlphaEvolveClient(
        project_id=PROJECT_ID,
        location=LOCATION,
        collection=COLLECTION,
        engine=GE_APP_ID,
        assistant=ASSISTANT,
        base_url=BASE_URL,
    )

    evaluation_fn = functools.partial(
        retrieval_evaluation, seed=BENCH_SEED, scale=BENCH_SCALE
    )
    experiment = AlphaEvolveExperiment(
        client, evaluation_fn, MAX_PROGRAMS_EVALUATED
    )

    experiment.create_experiment(
        {
            "title": "Agent Retrieval Layer",
            "problem_description": PROBLEM_DESCRIPTION,
            "program_language": "python",
            "run_settings": {
                "max_programs": MAX_PROGRAMS_GENERATED,
                "concurrency": CONCURRENCY,
            },
            "generation_settings": {
                "models": [
                    {"name": MODEL_1, "weight": MODEL_1_WEIGHT},
                    {"name": MODEL_2, "weight": MODEL_2_WEIGHT},
                ],
            },
        }
    )

    seed_code = SEED_PROGRAM_PATH.read_text(encoding="utf-8")
    seed_candidate = {
        "content": {"files": [{"path": "search_tool.py", "content": seed_code}]}
    }
    # Score the baseline for real so the seed enters the population with
    # its true fitness instead of a sentinel.
    seed_evaluation = evaluation_fn(seed_candidate)
    experiment.create_initial_program(
        {**seed_candidate, "evaluation": seed_evaluation}
    )

    experiment.start_experiment()
    asyncio.run(run_controller_loop(experiment))

    response = experiment.list_programs(
        params={"order_by": f"{PRIMARY_METRIC} desc"}
    )
    programs = (response or {}).get("alphaEvolvePrograms", [])
    print(f"Programs evaluated: {len(programs)}")
    if programs:
        best = programs[0]
        print(f"Best program: {best.get('name', 'unknown')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
