"""Task-agnostic orchestrator. Walks Jobs from a Roadmap, dispatches each
JobStep through the capability registry, isolates per-job failures, supports
done.flag-based resume.

The orchestrator owns no domain knowledge — which tools to call and what they
mean is decided by the Roadmap (built in preflight.reasoning) and the registry
(preflight/capabilities/). It owns: the loop, FASTA preparation, working
directory management, propagating step outputs into job inputs, and logging.

V1 scope: predict + extract chain for the binding/monomer branch. MSA caching
is deferred to v2 — for testing, run with `fast=True` on the prediction step
to skip MSA entirely.
"""

from __future__ import annotations

import argparse
import json
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from preflight.jobs import BatchResult, Job, JobStep
from preflight.reasoning import (
    Roadmap, TaskSpec, FeasibilityReport,
    build_roadmap, estimate_runtime, resolve_wrapper,
)


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------

def run_batch(
    spec: TaskSpec,
    roadmap: Roadmap,
    *,
    jobs_root: Path = Path("jobs"),
    abort_on_failure: bool = False,
) -> BatchResult:
    """Walk every Job in `roadmap.jobs` through its plan.

    Args:
        spec: the original TaskSpec — carried through for traceability.
        roadmap: must have `roadmap.jobs` populated by build_roadmap.
        jobs_root: parent directory for per-job working dirs. Each job lives
            under jobs_root/{job_id}/.
        abort_on_failure: if True, re-raise the first job's exception instead
            of marking failed and continuing. Default False per project policy
            ("flag rather than omit").

    Returns:
        BatchResult with all jobs marked done / skipped / failed.
    """
    started = _now()
    jobs = roadmap.jobs

    if not jobs:
        return BatchResult(
            spec=spec, roadmap=roadmap, jobs=[],
            started_at=started, finished_at=_now(),
        )

    print(f"[batch] starting run with {len(jobs)} job(s) under {jobs_root}/", flush=True)
    for job in jobs:
        _run_one_job(job, jobs_root, abort_on_failure)

    finished = _now()
    n_done = sum(1 for j in jobs if j.status == "done")
    n_skip = sum(1 for j in jobs if j.status == "skipped")
    n_fail = sum(1 for j in jobs if j.status == "failed")
    print(
        f"[batch] finished: {n_done} done, {n_skip} skipped, {n_fail} failed "
        f"({started} → {finished})",
        flush=True,
    )

    return BatchResult(
        spec=spec, roadmap=roadmap, jobs=jobs,
        started_at=started, finished_at=finished,
    )


# ---------------------------------------------------------------------------
# Per-job loop
# ---------------------------------------------------------------------------

def _run_one_job(job: Job, jobs_root: Path, abort_on_failure: bool) -> None:
    job.job_dir = jobs_root / job.job_id

    # Resume: skip work if previously completed
    if (job.job_dir / "done.flag").exists():
        job.status = "skipped"
        job.results = _load_existing_summary(job.job_dir)
        _log(job, "skipped (done.flag present)")
        return

    job.job_dir.mkdir(parents=True, exist_ok=True)
    job.status = "running"
    _log(job, f"starting (plan: {[s.tool_id for s in job.plan]})")

    # Preparation: derive a FASTA file from sequences if the plan needs one
    job.inputs = _ensure_fasta(job.inputs, job.job_dir)

    start = time.time()
    try:
        for i, step in enumerate(job.plan):
            wrapper = resolve_wrapper(step.tool_id)
            kwargs = _bind_inputs(step, job.inputs, job.job_dir)
            _log(job, f"step {i + 1}/{len(job.plan)}: {step.tool_id}({_shorten(kwargs)})")
            result = wrapper(**kwargs)
            job.results[step.tool_id] = result
            _propagate_outputs(job, result)
        job.status = "done"
        (job.job_dir / "done.flag").touch()
    except Exception as e:
        job.status = "failed"
        job.error = f"{type(e).__name__}: {e}"
        _log_failure(job, e)
        if abort_on_failure:
            raise
    finally:
        job.runtime_s = time.time() - start
        _log(job, f"finished status={job.status} runtime={job.runtime_s:.1f}s")
        _save_job_metadata(job)


# ---------------------------------------------------------------------------
# Input binding and preparation
# ---------------------------------------------------------------------------

def _bind_inputs(step: JobStep, job_inputs: dict[str, Any], job_dir: Path) -> dict[str, Any]:
    """Resolve a JobStep's input_bindings into actual call kwargs.

    Reserved binding values:
        _job_dir → resolves to the job's working directory.
    Non-reserved values are used as keys into job_inputs.

    Static step.kwargs are merged in (input_bindings win on collision).
    """
    kwargs = dict(step.kwargs)
    for kwarg_name, source_key in step.input_bindings.items():
        if source_key == "_job_dir":
            kwargs[kwarg_name] = job_dir
        else:
            kwargs[kwarg_name] = job_inputs.get(source_key)
    return kwargs


def _ensure_fasta(inputs: dict[str, Any], job_dir: Path) -> dict[str, Any]:
    """Derive `data_fasta_file` from `data_aa_sequence` if missing.

    Conventions:
        str          → monomer FASTA, single >query record.
        list[str]    → ColabFold multimer FASTA, chains joined with ':'.
    """
    if inputs.get("data_fasta_file") is not None:
        return inputs
    seqs = inputs.get("data_aa_sequence")
    if seqs is None:
        return inputs
    fasta_path = job_dir / "input.fasta"
    if isinstance(seqs, str):
        fasta_path.write_text(f">query\n{seqs}\n")
    elif isinstance(seqs, list):
        # ColabFold multimer convention: one record, chains separated by ':'
        fasta_path.write_text(f">query\n{':'.join(seqs)}\n")
    else:
        raise TypeError(
            f"data_aa_sequence must be str or list[str], got {type(seqs).__name__}"
        )
    inputs["data_fasta_file"] = fasta_path
    return inputs


def _propagate_outputs(job: Job, result: Any) -> None:
    """Expose common step outputs as job.inputs entries so later steps can consume them.

    Conservative: handles the cases we know about. Easy to extend as new
    output shapes appear.
    """
    pdb_paths = getattr(result, "pdb_paths", None)
    if pdb_paths:
        # Treat the rank-1 PDB as the canonical output for downstream steps.
        if "data_pdb_complex" not in job.inputs or job.inputs["data_pdb_complex"] is None:
            job.inputs["data_pdb_complex"] = pdb_paths[0]
        if "data_pdb_file" not in job.inputs or job.inputs["data_pdb_file"] is None:
            job.inputs["data_pdb_file"] = pdb_paths[0]


# ---------------------------------------------------------------------------
# Persistence + logging
# ---------------------------------------------------------------------------

def _load_existing_summary(job_dir: Path) -> dict[str, Any]:
    meta_path = job_dir / "job_meta.json"
    if not meta_path.exists():
        return {}
    try:
        return json.loads(meta_path.read_text()).get("results_summary", {})
    except json.JSONDecodeError:
        return {}


def _save_job_metadata(job: Job) -> None:
    if job.job_dir is None:
        return
    meta = {
        "job_id": job.job_id,
        "status": job.status,
        "runtime_s": job.runtime_s,
        "error": job.error,
        "metadata": job.metadata,
        "plan_tool_ids": [s.tool_id for s in job.plan],
        "results_summary": {k: _summarize(v) for k, v in job.results.items()},
    }
    (job.job_dir / "job_meta.json").write_text(json.dumps(meta, indent=2, default=str))


def _summarize(result: Any) -> Any:
    """Best-effort JSON-serializable summary of a step result for job_meta.json."""
    if isinstance(result, list):
        return [_summarize(r) for r in result]
    if hasattr(result, "__dict__"):
        out: dict[str, Any] = {}
        for k, v in result.__dict__.items():
            if k.startswith("_"):
                continue
            if isinstance(v, Path):
                out[k] = str(v)
            elif isinstance(v, (str, int, float, bool, type(None))):
                out[k] = v
            elif isinstance(v, list) and all(isinstance(x, Path) for x in v):
                out[k] = [str(x) for x in v]
            elif isinstance(v, list) and all(isinstance(x, (int, float)) for x in v):
                out[k] = {"len": len(v), "min": min(v) if v else None, "max": max(v) if v else None}
            # else: skip — too complex to summarize blindly
        return out
    return str(result)


def _log(job: Job, msg: str) -> None:
    line = f"{_now()} {msg}\n"
    print(f"[batch] {job.job_id}: {msg}", flush=True)
    if job.job_dir is not None and job.job_dir.exists():
        with (job.job_dir / "batch.log").open("a") as f:
            f.write(line)


def _log_failure(job: Job, exc: Exception) -> None:
    _log(job, f"FAILED: {type(exc).__name__}: {exc}")
    if job.job_dir is not None:
        (job.job_dir / "failure.log").write_text(traceback.format_exc())


def _shorten(d: dict[str, Any]) -> str:
    """Compact representation of kwargs for log lines."""
    parts = []
    for k, v in d.items():
        if isinstance(v, Path):
            parts.append(f"{k}={v.name}")
        elif isinstance(v, str) and len(v) > 30:
            parts.append(f"{k}=<str len {len(v)}>")
        elif isinstance(v, list):
            parts.append(f"{k}=<list len {len(v)}>")
        else:
            parts.append(f"{k}={v!r}")
    return ", ".join(parts)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(description="Run an end-to-end batch from a TaskSpec.")
    p.add_argument("--targets", nargs="+", required=True,
                   help="Target sequences (one per --targets value).")
    p.add_argument("--peptides", nargs="*", default=[],
                   help="Peptide sequences (omit for monomer-only run).")
    p.add_argument("--objective", default="batch run",
                   help="Free-text objective shown in the report.")
    p.add_argument("--jobs-root", type=Path, default=Path("jobs"),
                   help="Parent directory for per-job working dirs.")
    p.add_argument("--abort-on-failure", action="store_true",
                   help="Stop the batch on the first failure instead of continuing.")
    args = p.parse_args()

    spec = TaskSpec(targets=args.targets, peptides=args.peptides, objective=args.objective)
    avg_len = sum(len(s) for s in spec.targets + spec.peptides) // max(1, len(spec.targets) + len(spec.peptides))
    feas = FeasibilityReport(
        estimated_runtime_s=estimate_runtime(len(spec.targets), len(spec.peptides), avg_len),
        gpu_required=True,
    )
    roadmap = build_roadmap(spec, feas)

    print("[batch] roadmap steps:")
    for s in roadmap.steps:
        print(f"  - {s}")
    if roadmap.decision_points:
        print("[batch] decision points:")
        for d in roadmap.decision_points:
            print(f"  ? {d}")

    result = run_batch(spec, roadmap, jobs_root=args.jobs_root,
                       abort_on_failure=args.abort_on_failure)
    print(f"[batch] result: done={result.n_done} failed={result.n_failed} skipped={result.n_skipped}")


if __name__ == "__main__":
    main()
