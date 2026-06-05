"""Task-agnostic orchestrator. Walks Jobs from a Roadmap, dispatches each
JobStep through the capability registry, isolates per-job failures, supports
done.flag-based resume.

The orchestrator owns no domain knowledge — which tools to call and what they
mean is decided by the Roadmap (built in preflight.reasoning) and the registry
(preflight/capabilities/). It owns: the loop, FASTA preparation, working
directory management, propagating step outputs into job inputs, and logging.

Panel tasks (kind="binding_panel"): before the per-job loop, _run_panel_predictions
calls predict_panel once per target (blocking — waits for the SLURM array and
downloads results), then populates data_colabfold_out_dir / data_pdb_complex on
each job so the extraction step has the correct paths. The per-job loop then runs
only the extraction + analysis steps.
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
    outputs_root: Path | None = None,
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
        outputs_root: if set, emit user-facing reports under this directory after
            the run (one aggregated panel report per target; per-job reports for
            monomers). If None, run_batch stays side-effect-free apart from jobs_root.

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

    # Panel pre-processing: submit + wait + download before the per-job loop.
    panel_jobs = [j for j in jobs if j.metadata.get("kind") == "binding_panel"]
    if panel_jobs:
        _run_panel_predictions(spec, jobs, jobs_root)

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

    result = BatchResult(
        spec=spec, roadmap=roadmap, jobs=jobs,
        started_at=started, finished_at=finished,
    )

    if outputs_root is not None:
        paths = emit_reports(
            result, outputs_root=outputs_root, task_description=spec.objective
        )
        print(f"[batch] wrote {len(paths)} report(s) under {outputs_root}/", flush=True)

    return result


# ---------------------------------------------------------------------------
# Panel pre-processing
# ---------------------------------------------------------------------------

def _run_panel_predictions(
    spec: Any,
    jobs: list[Job],
    jobs_root: Path,
) -> None:
    """Call predict_panel once per target, then populate inputs on each panel job.

    Groups binding_panel jobs by target_idx, submits one predict_panel call per
    target (which blocks until the SLURM array completes and downloads results),
    then sets data_colabfold_out_dir / data_pdb_complex / data_pdb_file on every
    job so the extraction step has the correct paths.

    Resume: if jobs_root/panel_{target_id}/done.flag exists, skips submission and
    reconstructs local_output_dirs from the filesystem.

    Jobs whose peptide produced no DCC output are marked failed before the loop.
    """
    from collections import defaultdict
    from tools.predict import predict_panel, write_fasta

    by_target: dict[int, list[Job]] = defaultdict(list)
    for job in jobs:
        if job.metadata.get("kind") == "binding_panel":
            by_target[job.metadata["target_idx"]].append(job)

    for ti, target_jobs in sorted(by_target.items()):
        target_id = f"target_{ti}"
        panel_dir = jobs_root / f"panel_{target_id}"
        panel_dir.mkdir(parents=True, exist_ok=True)
        done_flag = panel_dir / "done.flag"

        peptides_ordered = sorted(target_jobs, key=lambda j: j.metadata["peptide_idx"])
        peptide_list: list[tuple[str, str]] = [
            (j.metadata["peptide_id"], spec.peptides[j.metadata["peptide_idx"]])
            for j in peptides_ordered
        ]

        if done_flag.exists():
            print(f"[batch] panel_{target_id}: skipped (done.flag present)", flush=True)
            local_output_dirs: dict[str, Path] = {}
            for pid, _ in peptide_list:
                d = panel_dir / pid
                if d.exists() and any(d.glob("*.pdb")):
                    local_output_dirs[pid] = d
        else:
            target_fasta = panel_dir / f"{target_id}.fasta"
            write_fasta(spec.targets[ti], target_id, target_fasta)
            print(
                f"[batch] panel_{target_id}: submitting {len(peptide_list)} peptide(s)",
                flush=True,
            )
            panel_result = predict_panel(
                target_fasta=target_fasta,
                peptides=peptide_list,
                jobs_root=panel_dir,
                job_name=target_id,
            )
            local_output_dirs = panel_result.local_output_dirs
            done_flag.touch()
            print(
                f"[batch] panel_{target_id}: {len(local_output_dirs)}/{len(peptide_list)} "
                "peptide(s) downloaded",
                flush=True,
            )

        for job in target_jobs:
            pid = job.metadata["peptide_id"]
            out_dir = local_output_dirs.get(pid)
            if out_dir is None:
                job.status = "failed"
                job.error = f"predict_panel produced no output for {pid}"
                print(f"[batch] {job.job_id}: marked failed (no panel output for {pid})", flush=True)
                continue
            job.inputs["data_colabfold_out_dir"] = out_dir
            pdb_paths = sorted(out_dir.glob("*.pdb"))
            if pdb_paths:
                job.inputs["data_pdb_complex"] = pdb_paths[0]
                job.inputs["data_pdb_file"] = pdb_paths[0]


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
        _run_post_plan_analysis(job)
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
# Post-plan analysis (non-fatal supplemental step)
# ---------------------------------------------------------------------------

def _run_post_plan_analysis(job: Job) -> None:
    """Call analyze_structure and analyze_sequence after a successful plan.

    Results land in job.results["analyze_structure"] and job.results["analyze_sequence"]
    so build_report_inputs can pull them into ReportInputs without re-running analysis.
    Failures are logged and swallowed — analysis is supplemental, not load-bearing.
    """
    try:
        from tools.analyze import analyze_sequence, analyze_structure
        pdb_path = job.inputs.get("data_pdb_complex") or job.inputs.get("data_pdb_file")
        if pdb_path is not None:
            job.results["analyze_structure"] = analyze_structure(Path(pdb_path))

        seqs = job.inputs.get("data_aa_sequence")
        if seqs is not None:
            if isinstance(seqs, str):
                job.results["analyze_sequence"] = analyze_sequence(seqs)
            elif isinstance(seqs, list):
                job.results["analyze_sequence"] = [analyze_sequence(s) for s in seqs]
    except Exception as e:
        _log(job, f"post-plan analysis skipped: {type(e).__name__}: {e}")


def build_report_inputs(
    job: Job,
    task_description: str,
    methods: dict[str, Any] | None = None,
    flags: list[str] | None = None,
    follow_up: list[str] | None = None,
    weights: dict[str, float] | None = None,
) -> "ReportInputs":
    """Assemble a ReportInputs from a completed Job.

    Pulls ExtractedMetrics from tool_extract_colabfold, StructureFeatures from
    analyze_structure, and SequenceFeatures from analyze_sequence — all of which
    are stored in job.results by _run_post_plan_analysis after the plan succeeds.
    For binding tasks (data_aa_sequence is a list), uses the peptide (index 1) as
    the primary sequence; for monomer tasks uses index 0.
    """
    from tools.report import ReportInputs

    metrics = job.results.get("tool_extract_colabfold") or []
    if not isinstance(metrics, list):
        metrics = [metrics]

    structure = job.results.get("analyze_structure")

    sequences = job.results.get("analyze_sequence")
    sequence = None
    if isinstance(sequences, list) and sequences:
        sequence = sequences[1] if len(sequences) > 1 else sequences[0]
    elif sequences is not None:
        sequence = sequences

    output_paths: dict[str, Path] = {}
    pdb = job.inputs.get("data_pdb_complex") or job.inputs.get("data_pdb_file")
    if pdb is not None:
        output_paths["rank-1 PDB"] = Path(pdb)
    if job.job_dir is not None:
        output_paths["job directory"] = job.job_dir

    return ReportInputs(
        job_id=job.job_id,
        task_description=task_description,
        metrics=metrics,
        methods=methods or {},
        flags=flags or [],
        follow_up=follow_up or [],
        output_paths=output_paths,
        weights=weights,
        structure=structure,
        sequence=sequence,
    )


def build_panel_report_inputs(
    target_jobs: list[Job],
    target_id: str,
    task_description: str,
    *,
    methods: dict[str, Any] | None = None,
    flags: list[str] | None = None,
    follow_up: list[str] | None = None,
    weights: dict[str, float] | None = None,
) -> "PanelReportInputs":
    """Aggregate a target's per-peptide Jobs into one ranked panel report input.

    For each done job, picks the best model (max composite_score) from
    job.results["tool_extract_colabfold"] and pulls its structure/sequence analysis.
    Jobs with no extracted metrics are skipped (logged upstream as failures).
    """
    from tools.extract import composite_score
    from tools.report import PanelEntry, PanelReportInputs

    score_kw: dict = {"weights": weights} if weights else {}
    entries: list[PanelEntry] = []
    for job in target_jobs:
        metrics = job.results.get("tool_extract_colabfold") or []
        if not isinstance(metrics, list):
            metrics = [metrics]
        if not metrics:
            continue
        best = max(metrics, key=lambda m: composite_score(m, **score_kw))

        seqs = job.results.get("analyze_sequence")
        sequence = None
        if isinstance(seqs, list) and seqs:
            sequence = seqs[1] if len(seqs) > 1 else seqs[0]
        elif seqs is not None:
            sequence = seqs

        entries.append(PanelEntry(
            peptide_id=job.metadata.get("peptide_id", job.job_id),
            metrics=best,
            structure=job.results.get("analyze_structure"),
            sequence=sequence,
            label=job.metadata.get("peptide_id", job.job_id),
            job_dir=job.job_dir,
        ))

    return PanelReportInputs(
        target_id=target_id,
        task_description=task_description,
        complexes=entries,
        methods=methods or {},
        flags=flags or [],
        follow_up=follow_up or [],
        weights=weights,
    )


def emit_reports(
    batch_result: BatchResult,
    *,
    outputs_root: Path,
    task_description: str,
    methods: dict[str, Any] | None = None,
    weights: dict[str, float] | None = None,
) -> list[Path]:
    """Write user-facing reports for a completed run under outputs_root/.

    Binding / binding_panel jobs are grouped by target into one aggregated panel
    report (outputs_root/{target_id}/panel_report.md). Monomer jobs get a per-job
    report (outputs_root/{job_id}/report.md). Returns the paths written.
    """
    from collections import defaultdict
    from tools.report import write_panel_report, write_report

    done = [j for j in batch_result.jobs if j.status == "done"]
    by_target: dict[str, list[Job]] = defaultdict(list)
    for job in done:
        by_target[job.metadata.get("target_id", job.job_id)].append(job)

    written: list[Path] = []
    for target_id, tjobs in sorted(by_target.items()):
        kinds = {j.metadata.get("kind") for j in tjobs}
        if kinds & {"binding", "binding_panel"}:
            panel_inputs = build_panel_report_inputs(
                tjobs, target_id, task_description, methods=methods, weights=weights
            )
            if panel_inputs.complexes:
                out = outputs_root / target_id / "panel_report.md"
                written.append(write_panel_report(panel_inputs, out))
        else:  # monomer / other single-structure tasks
            for job in tjobs:
                ri = build_report_inputs(job, task_description, methods=methods, weights=weights)
                if ri.metrics:
                    out = outputs_root / job.job_id / "report.md"
                    written.append(write_report(ri, out))
    return written


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
    p.add_argument("--outputs-root", type=Path, default=Path("outputs"),
                   help="Directory for user-facing reports (panel/per-job). "
                        "Pass '' to skip report emission.")
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

    outputs_root = args.outputs_root if str(args.outputs_root) else None
    result = run_batch(spec, roadmap, jobs_root=args.jobs_root,
                       abort_on_failure=args.abort_on_failure,
                       outputs_root=outputs_root)
    print(f"[batch] result: done={result.n_done} failed={result.n_failed} skipped={result.n_skipped}")


if __name__ == "__main__":
    main()
