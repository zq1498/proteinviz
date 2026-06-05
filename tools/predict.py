"""Prediction backend wrappers.

ColabFold runs on DCC via an existing SLURM script (reference copy at
cluster/predict.sh; deployed at DCC_PREDICT_SCRIPT on the cluster). Workflow:
  1. Upload FASTA to DCC under DCC_CF_JOBS_DIR/{job_name}/
  2. sbatch --export=ALL,FASTA=...,OUTDIR=...,NUM_RECYCLE=...,NUM_MODELS=... <script>
  3. Poll squeue until the job completes
  4. SCP PDB + score JSON files back to local out_dir

ESMFold API runs via HTTP (no cluster required).

Environment variables (set in .env or shell):
    DCC_HOST            SSH target. Default: zq36@dcc-login.oit.duke.edu
    DCC_KEY             SSH private key. Default: ~/.ssh/id_ed25519
    DCC_WORK            Base work directory on DCC. Default: /work/zq36
    DCC_PREDICT_SCRIPT  Absolute path to the prediction script on DCC.
                        Default: {DCC_WORK}/colabfold/install_scripts/04_run_prediction.sh
    DCC_CF_JOBS_DIR     Base dir for per-job inputs/outputs on DCC.
                        Default: {DCC_WORK}/colabfold/jobs
    PREDICT_BACKEND     colabfold | esmfold_api. Default: colabfold
    CF_NUM_MODELS       Number of ranked models (1–5). Default: 5
    CF_NUM_RECYCLES     AF2 recycling iterations. Default: 3
    CF_CALC_EXTRA_PTM   1 to compute actifpTM and extended pTM metrics. Default: 1
"""

from __future__ import annotations

import argparse
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path


@dataclass
class PredictionResult:
    success: bool
    backend: str
    output_dir: Path
    runtime_s: float
    pdb_paths: list[Path] = field(default_factory=list)
    a3m_paths: list[Path] = field(default_factory=list)
    error: str | None = None


@dataclass
class PanelResult:
    """Outcome of predict_panel(). Blocks until the array completes and downloads results.

    local_output_dirs: peptide_id → local directory containing PDB + score JSON files.
    Missing peptide IDs indicate tasks that failed on DCC.
    """
    primary_job_id: str   # MSA search job (SEARCH_ONLY=1)
    array_job_id: str     # Prediction array job (depends on primary)
    n_peptides: int
    remote_jobs_dir: str  # DCC path; per-peptide outputs are subdirectories here
    local_output_dirs: dict[str, Path] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def _cf_num_models() -> int:
    return int(os.environ.get("CF_NUM_MODELS", "5"))


def _cf_num_recycles() -> int:
    return int(os.environ.get("CF_NUM_RECYCLES", "3"))


def _cf_calc_extra_ptm() -> bool:
    return os.environ.get("CF_CALC_EXTRA_PTM", "1") == "1"


def _dcc_predict_script() -> str:
    from tools.msa import _dcc_work
    return os.environ.get(
        "DCC_PREDICT_SCRIPT",
        f"{_dcc_work()}/colabfold/install_scripts/04_run_prediction.sh",
    )


def _dcc_cf_jobs_dir() -> str:
    from tools.msa import _dcc_work
    return os.environ.get("DCC_CF_JOBS_DIR", f"{_dcc_work()}/colabfold/jobs")


# ---------------------------------------------------------------------------
# Backend: ColabFold on DCC via SLURM
# ---------------------------------------------------------------------------

def check_backend(backend: str) -> tuple[bool, str]:
    """Verify the selected backend is reachable. Returns (ok, message)."""
    if backend == "colabfold":
        try:
            from tools.msa import _ssh, _dcc_host
            result = _ssh("echo ok", check=False)
            if "ok" in result.stdout:
                return True, f"SSH to DCC OK ({_dcc_host()})"
            return False, f"SSH to DCC returned unexpected output: {result.stdout!r}"
        except Exception as e:
            return False, f"SSH to DCC failed: {e}"
    if backend == "esmfold_api":
        return True, "ESMFold API (network check not performed)"
    return False, f"unknown backend: {backend}"


def predict_structure(
    fasta: Path,
    out_dir: Path,
    fast: bool = False,
    num_recycle: int | None = None,
    num_models: int | None = None,
    job_name: str | None = None,
    calc_extra_ptm: bool | None = None,
    pre_msa_dir: Path | None = None,
) -> PredictionResult:
    """Upload FASTA to DCC, submit ColabFold SLURM job, download results.

    fast=True uses 1 model / 1 recycle for quick iteration.
    num_recycle / num_models override CF_NUM_RECYCLES / CF_NUM_MODELS for this call only.
    calc_extra_ptm controls --calc-extra-ptm (actifpTM). Defaults to CF_CALC_EXTRA_PTM
    env var (1 = True). Set False explicitly for monomer runs where it is meaningless.

    pre_msa_dir: local directory of pre-computed .a3m files. When provided, these are
    uploaded to the job's remote msas/ directory before sbatch is called. predict.sh
    detects existing .a3m files and skips colabfold_search, running colabfold_batch
    directly. After completion, .a3m files are always downloaded to out_dir/msas/ and
    returned in PredictionResult.a3m_paths for use in subsequent predictions.
    """
    from tools.msa import _ssh, _scp_to, _scp_from, _wait_for_job, _dcc_host

    n_recycle = 1 if fast else (num_recycle or _cf_num_recycles())
    n_models = 1 if fast else (num_models or _cf_num_models())
    extra_ptm = _cf_calc_extra_ptm() if calc_extra_ptm is None else calc_extra_ptm

    if job_name is None:
        job_name = f"{fasta.stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    jobs_dir = _dcc_cf_jobs_dir()
    remote_job_dir = f"{jobs_dir}/{job_name}"
    remote_fasta = f"{remote_job_dir}/{fasta.name}"
    remote_out = f"{remote_job_dir}/output"
    remote_msa_dir = f"{remote_out}/msas"

    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        _ssh(f"mkdir -p {remote_job_dir}")
        _scp_to(fasta, remote_fasta)
        print(f"[predict] {fasta.name} → {_dcc_host()}:{remote_fasta}", flush=True)

        if pre_msa_dir is not None:
            a3m_files = list(pre_msa_dir.glob("*.a3m"))
            if not a3m_files:
                raise ValueError(f"pre_msa_dir {pre_msa_dir} contains no .a3m files")
            _ssh(f"mkdir -p {remote_msa_dir}")
            for a3m in a3m_files:
                _scp_to(a3m, f"{remote_msa_dir}/{a3m.name}")
            print(
                f"[predict] uploaded {len(a3m_files)} pre-computed A3M(s) → "
                f"{_dcc_host()}:{remote_msa_dir} (colabfold_search will be skipped)",
                flush=True,
            )

        script = _dcc_predict_script()
        sbatch_cmd = (
            f"sbatch --export=ALL,"
            f"FASTA={remote_fasta},"
            f"OUTDIR={remote_out},"
            f"NUM_RECYCLE={n_recycle},"
            f"NUM_MODELS={n_models},"
            f"CALC_EXTRA_PTM={'1' if extra_ptm else '0'} "
            f"{script}"
        )
        result = _ssh(sbatch_cmd)
        match = re.search(r"(\d+)", result.stdout)
        if not match:
            raise RuntimeError(f"Could not parse job ID from sbatch output: {result.stdout!r}")
        job_id = match.group(1)
        print(f"[predict] submitted SLURM job {job_id}", flush=True)

        start = time.time()
        final_state = _wait_for_job(job_id)
        runtime = time.time() - start

        if final_state != "COMPLETED":
            return PredictionResult(
                success=False, backend="colabfold", output_dir=out_dir,
                runtime_s=runtime,
                error=f"SLURM job {job_id} ended with state {final_state}",
            )

        remote_ls = _ssh(f"ls {remote_out}/*.pdb {remote_out}/*.json 2>/dev/null || true")
        for remote_file in [f.strip() for f in remote_ls.stdout.splitlines() if f.strip()]:
            _scp_from(remote_file, out_dir / Path(remote_file).name)

        # Download A3Ms for reuse in subsequent predictions (e.g. next peptide in panel)
        local_msa_dir = out_dir / "msas"
        local_msa_dir.mkdir(exist_ok=True)
        remote_a3m_ls = _ssh(f"ls {remote_msa_dir}/*.a3m 2>/dev/null || true")
        a3m_paths: list[Path] = []
        for remote_file in [f.strip() for f in remote_a3m_ls.stdout.splitlines() if f.strip()]:
            local = local_msa_dir / Path(remote_file).name
            _scp_from(remote_file, local)
            a3m_paths.append(local)

        pdb_paths = sorted(out_dir.glob("*.pdb"))
        print(
            f"[predict] done — {len(pdb_paths)} PDB(s), {len(a3m_paths)} A3M(s) in {out_dir}",
            flush=True,
        )
        return PredictionResult(
            success=True, backend="colabfold", output_dir=out_dir,
            runtime_s=runtime, pdb_paths=pdb_paths, a3m_paths=a3m_paths,
        )

    except Exception as e:
        return PredictionResult(
            success=False, backend="colabfold", output_dir=out_dir,
            runtime_s=0.0, error=str(e),
        )


# ---------------------------------------------------------------------------
# Backend: ESMFold API
# ---------------------------------------------------------------------------

def run_esmfold_api(fasta: Path, out_dir: Path) -> PredictionResult:
    raise NotImplementedError("ESMFold API wrapper not yet implemented")


# ---------------------------------------------------------------------------
# Panel: MSA primary job + parallel prediction array
# ---------------------------------------------------------------------------

def _parse_target_fasta(fasta: Path) -> tuple[str, str, int]:
    """Return (name, first_chain_seq, n_chains) from a (possibly multimer) FASTA.

    For a homodimer FASTA with VEGF:VEGF on the sequence line, returns
    ('vegf_binder_complex', 'VEGF_SEQ', 2). The caller uses n_chains as
    n_target_copies so the paired A3M gets the right cardinality without
    having to run colabfold_search on the full multimer FASTA (which produces
    a paired-format A3M that _parse_a3m cannot consume).
    """
    name, seq = "", ""
    for line in fasta.read_text().splitlines():
        line = line.strip()
        if line.startswith(">"):
            name = line[1:].split()[0]
        elif line:
            seq = line
            break
    chains = [c.strip() for c in seq.split(":") if c.strip()]
    return name, chains[0] if chains else seq, len(chains) if chains else 1


def predict_panel(
    target_fasta: Path,
    peptides: list[tuple[str, str]],
    jobs_root: Path,
    job_name: str | None = None,
    num_recycle: int | None = None,
    num_models: int | None = None,
    calc_extra_ptm: bool | None = None,
) -> PanelResult:
    """Submit a target MSA search then a parallel prediction array for a peptide panel.

    Primary job (SEARCH_ONLY=1): runs colabfold_search on the target FASTA only and
    exits, leaving the target .a3m in the remote msas/ directory.

    Array job (--dependency=afterok:{primary_id}): one SLURM task per peptide. Each
    task calls build_a3m.py on the DCC node to construct its paired A3M from the
    primary job's cached MSA, then runs colabfold_batch independently. No data
    round-trips between local machine and DCC for the per-peptide A3Ms.

    Blocks until the array job reaches a terminal SLURM state, then downloads
    per-peptide outputs to jobs_root/{peptide_id}/. Returns PanelResult with
    local_output_dirs populated for every peptide whose DCC output directory
    contained at least one PDB file. Peptides whose array task failed will be
    absent from local_output_dirs (logged to stdout, not raised).
    """
    from tools.msa import _ssh, _scp_to, _scp_from, _wait_for_job, _dcc_host

    if not peptides:
        raise ValueError("peptides list is empty")

    if job_name is None:
        job_name = f"{target_fasta.stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    # For multimer targets (e.g. homodimers), extract the unique chain so the primary
    # MSA job produces a single-chain A3M that _parse_a3m can read. n_target_copies
    # carries the chain count into the paired A3M metadata (cardinality field).
    target_name, monomer_seq, n_target_copies = _parse_target_fasta(target_fasta)
    if n_target_copies > 1:
        monomer_fasta = jobs_root / f"{target_fasta.stem}_monomer_chain.fasta"
        monomer_fasta.write_text(f">{target_name}\n{monomer_seq}\n")
        primary_fasta = monomer_fasta
        print(
            f"[panel] multimer target detected ({n_target_copies} copies of {target_name}); "
            f"running primary MSA on monomer chain → {primary_fasta.name}",
            flush=True,
        )
    else:
        primary_fasta = target_fasta

    jobs_dir = _dcc_cf_jobs_dir()
    remote_panel_dir = f"{jobs_dir}/{job_name}"
    remote_fasta       = f"{remote_panel_dir}/{primary_fasta.name}"
    remote_primary_out = f"{remote_panel_dir}/primary"
    remote_msa_dir     = f"{remote_primary_out}/msas"
    remote_manifest    = f"{remote_panel_dir}/manifest.tsv"
    remote_build_py    = f"{remote_panel_dir}/build_a3m.py"
    remote_array_sh    = f"{remote_panel_dir}/array_predict.sh"
    remote_jobs_dir    = f"{remote_panel_dir}/jobs"

    n_recycle  = num_recycle or _cf_num_recycles()
    n_models   = num_models or _cf_num_models()
    extra_ptm  = _cf_calc_extra_ptm() if calc_extra_ptm is None else calc_extra_ptm

    jobs_root.mkdir(parents=True, exist_ok=True)
    _ssh(f"mkdir -p {remote_panel_dir} {remote_jobs_dir} /work/zq36/colabfold/logs")

    # Upload target FASTA (monomer chain if target is a homo-oligomer)
    _scp_to(primary_fasta, remote_fasta)
    print(f"[panel] {primary_fasta.name} → {_dcc_host()}:{remote_fasta}", flush=True)

    # Write manifest locally, upload to DCC
    manifest_local = jobs_root / f"{job_name}_manifest.tsv"
    manifest_local.write_text(
        "\n".join(f"{pid}\t{pseq}" for pid, pseq in peptides) + "\n"
    )
    _scp_to(manifest_local, remote_manifest)
    print(f"[panel] manifest ({len(peptides)} peptides) → {_dcc_host()}:{remote_manifest}", flush=True)

    # Upload cluster scripts
    cluster_dir = Path(__file__).parent.parent / "cluster"
    _scp_to(cluster_dir / "build_a3m.py", remote_build_py)
    _scp_to(cluster_dir / "array_predict.sh", remote_array_sh)
    _ssh(f"chmod +x {remote_array_sh}")

    # Submit primary MSA search job (SEARCH_ONLY=1, no GPU partition needed)
    predict_script = _dcc_predict_script()
    primary = _ssh(
        f"sbatch --export=ALL,"
        f"FASTA={remote_fasta},"
        f"OUTDIR={remote_primary_out},"
        f"SEARCH_ONLY=1 "
        f"{predict_script}"
    )
    match = re.search(r"(\d+)", primary.stdout)
    if not match:
        raise RuntimeError(f"Could not parse primary job ID: {primary.stdout!r}")
    primary_job_id = match.group(1)
    print(f"[panel] primary MSA job {primary_job_id} submitted", flush=True)

    # Submit array job — starts only after primary succeeds
    n = len(peptides)
    array = _ssh(
        f"sbatch "
        f"--dependency=afterok:{primary_job_id} "
        f"--array=0-{n - 1} "
        f"--export=ALL,"
        f"PRIMARY_MSA_DIR={remote_msa_dir},"
        f"MANIFEST={remote_manifest},"
        f"JOBS_DIR={remote_jobs_dir},"
        f"BUILD_A3M_SCRIPT={remote_build_py},"
        f"N_TARGET_COPIES={n_target_copies},"
        f"NUM_RECYCLE={n_recycle},"
        f"NUM_MODELS={n_models},"
        f"CALC_EXTRA_PTM={'1' if extra_ptm else '0'} "
        f"{remote_array_sh}"
    )
    match = re.search(r"(\d+)", array.stdout)
    if not match:
        raise RuntimeError(f"Could not parse array job ID: {array.stdout!r}")
    array_job_id = match.group(1)
    print(
        f"[panel] array job {array_job_id} submitted "
        f"({n} tasks, depends on {primary_job_id})",
        flush=True,
    )

    # Wait for array to reach a terminal state, then download per-peptide results.
    # Downloads proceed even if final_state != COMPLETED so partial successes are kept.
    final_state = _wait_for_job(array_job_id)
    print(f"[panel] array job {array_job_id} finished: {final_state}", flush=True)

    local_output_dirs: dict[str, Path] = {}
    for pid, _ in peptides:
        remote_out = f"{remote_jobs_dir}/{pid}/output"
        local_out = jobs_root / pid
        local_out.mkdir(parents=True, exist_ok=True)
        try:
            remote_ls = _ssh(
                f"ls {remote_out}/*.pdb {remote_out}/*.json 2>/dev/null || true",
                check=False,
            )
            files = [f.strip() for f in remote_ls.stdout.splitlines() if f.strip()]
            if not files:
                print(f"[panel] {pid}: no output files (array task may have failed)", flush=True)
                continue
            for remote_file in files:
                _scp_from(remote_file, local_out / Path(remote_file).name)
            n_pdb = len(list(local_out.glob("*.pdb")))
            print(f"[panel] {pid}: downloaded {len(files)} files ({n_pdb} PDB(s))", flush=True)
            local_output_dirs[pid] = local_out
        except Exception as e:
            print(f"[panel] {pid}: download failed — {e}", flush=True)

    n_ok = len(local_output_dirs)
    print(f"[panel] downloaded {n_ok}/{len(peptides)} peptide result(s)", flush=True)

    return PanelResult(
        primary_job_id=primary_job_id,
        array_job_id=array_job_id,
        n_peptides=n,
        remote_jobs_dir=remote_jobs_dir,
        local_output_dirs=local_output_dirs,
    )


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def predict(
    fasta: Path,
    out_dir: Path,
    backend: str | None = None,
    fast: bool = False,
    num_recycle: int | None = None,
    num_models: int | None = None,
    job_name: str | None = None,
    calc_extra_ptm: bool | None = None,
    pre_msa_dir: Path | None = None,
) -> PredictionResult:
    backend = backend or os.environ.get("PREDICT_BACKEND", "colabfold")
    if backend == "colabfold":
        return predict_structure(
            fasta, out_dir, fast=fast,
            num_recycle=num_recycle, num_models=num_models, job_name=job_name,
            calc_extra_ptm=calc_extra_ptm, pre_msa_dir=pre_msa_dir,
        )
    if backend == "esmfold_api":
        return run_esmfold_api(fasta, out_dir)
    raise ValueError(f"unknown backend: {backend}")


def write_fasta(sequence: str, name: str, path: Path) -> Path:
    """Write a single sequence to a FASTA file. Returns the path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f">{name}\n{sequence}\n")
    return path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    p = argparse.ArgumentParser(description="Run ColabFold structure prediction on DCC via SLURM.")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--fasta", type=Path, help="Input FASTA file")
    group.add_argument("--sequence", help="Inline amino acid sequence")
    p.add_argument("--name", default="query", help="Sequence name (used with --sequence)")
    p.add_argument("--out", type=Path, help="Local directory for downloaded results")
    p.add_argument("--backend", default=None, help="colabfold | esmfold_api")
    p.add_argument("--fast", action="store_true", help="1 model, 1 recycle. Dev/testing only.")
    p.add_argument("--num-recycle", type=int, default=None, help="Override CF_NUM_RECYCLES")
    p.add_argument("--num-models", type=int, default=None, help="Override CF_NUM_MODELS")
    p.add_argument("--job-name", default=None, help="SLURM job label and remote directory name")
    p.add_argument("--no-extra-ptm", action="store_true",
                   help="Disable --calc-extra-ptm (actifpTM). Use for monomer runs.")
    p.add_argument("--pre-msa-dir", type=Path, default=None,
                   help="Local dir of pre-computed .a3m files. Uploaded to the job's "
                        "remote msas/ dir before submitting, causing predict.sh to skip "
                        "colabfold_search and run colabfold_batch directly.")
    p.add_argument("--panel", action="store_true",
                   help="Panel mode: submit primary MSA job then a parallel prediction array. "
                        "Requires --fasta (target) and --manifest.")
    p.add_argument("--manifest", type=Path, default=None,
                   help="TSV file: peptide_id<TAB>peptide_seq per line. Required with --panel.")
    p.add_argument("--jobs-root", type=Path, default=Path("jobs"),
                   help="Local dir for panel metadata files (manifest, etc.). Default: jobs/")
    p.add_argument("--check", action="store_true", help="Verify backend connectivity and exit")
    args = p.parse_args()

    backend = args.backend or os.environ.get("PREDICT_BACKEND", "colabfold")

    if args.check:
        ok, msg = check_backend(backend)
        print(f"[{backend}] {'OK' if ok else 'FAIL'}: {msg}")
        raise SystemExit(0 if ok else 1)

    # Panel mode: primary MSA job + array prediction
    if args.panel:
        if not args.fasta:
            p.error("--panel requires --fasta (target protein FASTA)")
        if not args.manifest:
            p.error("--panel requires --manifest (TSV: peptide_id<TAB>peptide_seq)")
        peptides: list[tuple[str, str]] = []
        for line in args.manifest.read_text().splitlines():
            if not line.strip():
                continue
            parts = line.strip().split("\t")
            if len(parts) < 2:
                p.error(f"Manifest line has fewer than 2 columns: {line!r}")
            peptides.append((parts[0], parts[1]))
        if not peptides:
            p.error(f"Manifest {args.manifest} is empty")
        result = predict_panel(
            args.fasta, peptides,
            jobs_root=args.jobs_root,
            job_name=args.job_name,
            num_recycle=args.num_recycle,
            num_models=args.num_models,
            calc_extra_ptm=False if args.no_extra_ptm else None,
        )
        print(
            f"panel submitted: primary={result.primary_job_id} "
            f"array={result.array_job_id} n={result.n_peptides}"
        )
        print(f"outputs (on DCC): {result.remote_jobs_dir}")
        raise SystemExit(0)

    if not args.out:
        p.error("--out is required")

    if args.sequence:
        fasta = args.out / f"{args.name}.fasta"
        write_fasta(args.sequence, args.name, fasta)
    elif args.fasta:
        fasta = args.fasta
    else:
        p.error("one of --fasta or --sequence is required")

    result = predict(
        fasta, args.out, backend=backend, fast=args.fast,
        num_recycle=args.num_recycle, num_models=args.num_models,
        job_name=args.job_name,
        calc_extra_ptm=False if args.no_extra_ptm else None,
        pre_msa_dir=args.pre_msa_dir,
    )
    print(f"success={result.success} backend={result.backend} runtime={result.runtime_s:.1f}s")
    if result.success:
        for pdb in result.pdb_paths:
            print(f"  pdb: {pdb}")
    else:
        print(f"error: {result.error}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
