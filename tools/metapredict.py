"""metapredict V3 disorder prediction — caller side, runs in the proteinviz env (Python 3.12).

metapredict is only available in the proteinviz_metapredict conda env (Python 3.11).
This module bridges the version gap via subprocess, mirroring the rosetta.py pattern:
each function serializes arguments to JSON, spawns metapredict_worker.py under the
metapredict interpreter, and deserializes the result.

Protocol (stdin → stdout):
  {"function": "<name>", "kwargs": {...}} → {"result": <data>, "error": null}

To add a new analysis:
  1. Implement _my_function(**kwargs) -> dict in tools/metapredict_worker.py
  2. Add it to _FUNCTIONS in that file
  3. Add a dataclass for the return type here (if the result has named fields)
  4. Add a public wrapper below that calls _call_worker("my_function", ...)
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_METAPREDICT_PYTHON = Path("/home/zach/miniconda3/envs/proteinviz_metapredict/bin/python")
_WORKER = Path(__file__).parent / "metapredict_worker.py"


@dataclass
class MetapredictResult:
    """Per-residue disorder prediction from metapredict V3.

    disorder_scores: per-residue values in [0, 1]; higher = more disordered.
        Threshold of 0.5 is the conventional disordered/ordered boundary.
    percent_disorder: percentage of residues with disorder_score > 0.5.
    disordered_domain_boundaries: list of [start, end] indices (0-indexed, inclusive)
        for each predicted disordered domain.
    folded_domain_boundaries: list of [start, end] indices for predicted folded domains.
    """
    sequence: str
    disorder_scores: list[float]
    percent_disorder: float
    disordered_domain_boundaries: list[list[int]] = field(default_factory=list)
    folded_domain_boundaries: list[list[int]] = field(default_factory=list)


def _call_worker(function: str, **kwargs: Any) -> Any:
    import os
    request = json.dumps({"function": function, "kwargs": kwargs})
    env = {**os.environ, "CUDA_VISIBLE_DEVICES": ""}
    proc = subprocess.run(
        [str(_METAPREDICT_PYTHON), str(_WORKER)],
        input=request,
        capture_output=True,
        text=True,
        timeout=60,
        env=env,
    )
    try:
        # metapredict may emit warnings to stdout before the JSON; strip the prefix.
        raw = proc.stdout
        start = raw.find("{")
        response = json.loads(raw[start:] if start >= 0 else raw)
    except json.JSONDecodeError:
        raise RuntimeError(
            f"metapredict_worker returned non-JSON output (exit {proc.returncode}).\n"
            f"stdout: {proc.stdout[:500]}\nstderr: {proc.stderr[:500]}"
        )
    if response.get("error"):
        raise RuntimeError(
            f"metapredict_worker error in {function!r}: {response['error']}\n"
            f"stderr: {proc.stderr[:500]}"
        )
    return response["result"]


def predict_disorder(seq: str) -> MetapredictResult:
    """Per-residue disorder scores and domain boundaries via metapredict V3.

    Returns disorder_scores [0, 1] for every residue, percent_disorder,
    and predicted disordered and folded domain boundaries (0-indexed [start, end] pairs).

    First call spawns a subprocess; subsequent calls also spawn (no persistent worker).
    Typical runtime: ~0.5 s for sequences up to ~500 residues.
    """
    data = _call_worker("predict_disorder", sequence=seq)
    return MetapredictResult(
        sequence=data["sequence"],
        disorder_scores=data["disorder_scores"],
        percent_disorder=data["percent_disorder"],
        disordered_domain_boundaries=data["disordered_domain_boundaries"],
        folded_domain_boundaries=data["folded_domain_boundaries"],
    )
