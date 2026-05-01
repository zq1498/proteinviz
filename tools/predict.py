"""Prediction backend wrappers (ColabFold via WSL, ESMFold API, Boltz-1).

ColabFold is invoked through WSL because the local install lives in the
WSL filesystem. Invocation requires MSYS_NO_PATHCONV=1 to prevent Git Bash
from mangling Linux paths, and Windows paths are translated to /mnt/c/...
form before being passed to colabfold_batch.

Environment variables (set in .env or shell):
    COLABFOLD_BIN   Full path to colabfold_batch inside WSL.
                    Default: /home/zach/localcolabfold/.pixi/envs/default/bin/colabfold_batch
    WSL_DISTRO      WSL distro name. Default: Ubuntu
    PREDICT_BACKEND colabfold | esmfold_api. Default: colabfold
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

_DEFAULT_COLABFOLD_BIN = (
    "/home/zach/localcolabfold/.pixi/envs/default/bin/colabfold_batch"
)
_DEFAULT_COLABFOLD_SEARCH_BIN = (
    "/home/zach/localcolabfold/.pixi/envs/default/bin/colabfold_search"
)
_DEFAULT_WSL_DISTRO = "Ubuntu"


@dataclass
class PredictionResult:
    success: bool
    backend: str
    output_dir: Path
    runtime_s: float
    pdb_paths: list[Path] = field(default_factory=list)
    a3m_paths: list[Path] = field(default_factory=list)  # populated by msa_only runs
    error: str | None = None


# ---------------------------------------------------------------------------
# Path utilities
# ---------------------------------------------------------------------------

def to_wsl_path(windows_path: Path) -> str:
    """Convert a Windows absolute path to its WSL /mnt/ equivalent.

    e.g. C:\\Users\\Zach\\foo -> /mnt/c/Users/Zach/foo
    """
    p = windows_path.resolve()
    drive = p.drive.rstrip(":").lower()          # 'C:' -> 'c'
    rest = p.as_posix().split(":", 1)[-1]        # '/Users/Zach/foo'
    return f"/mnt/{drive}{rest}"


# ---------------------------------------------------------------------------
# Backend: ColabFold via WSL
# ---------------------------------------------------------------------------

def _colabfold_bin() -> str:
    return os.environ.get("COLABFOLD_BIN", _DEFAULT_COLABFOLD_BIN)


def _colabfold_search_bin() -> str:
    return os.environ.get("COLABFOLD_SEARCH_BIN", _DEFAULT_COLABFOLD_SEARCH_BIN)


def _colabfold_db() -> str:
    """Local WSL path to the MMseqs2 database directory. Required for msa_only runs."""
    return os.environ.get("COLABFOLD_DB", "")


def _wsl_distro() -> str:
    return os.environ.get("WSL_DISTRO", _DEFAULT_WSL_DISTRO)


def _wsl_env() -> dict[str, str]:
    """Subprocess env with MSYS_NO_PATHCONV=1 to suppress Git Bash path mangling."""
    return {**os.environ, "MSYS_NO_PATHCONV": "1"}


def check_backend(backend: str) -> tuple[bool, str]:
    """Verify the selected backend is reachable. Returns (ok, message)."""
    if backend == "colabfold":
        bin_path = _colabfold_bin()
        try:
            result = subprocess.run(
                ["wsl.exe", "-d", _wsl_distro(), "--", bin_path, "--help"],
                env=_wsl_env(),
                capture_output=True,
                text=True,
                timeout=30,
            )
            if result.returncode == 0 or "usage:" in result.stdout.lower():
                return True, f"colabfold_batch found via WSL ({bin_path})"
            return False, f"colabfold_batch exited {result.returncode}: {result.stderr[:200]}"
        except FileNotFoundError:
            return False, "wsl.exe not found — is WSL installed?"
        except subprocess.TimeoutExpired:
            return False, "wsl.exe timed out on --help check"
    if backend == "esmfold_api":
        return True, "ESM Atlas API (network check not performed)"
    return False, f"unknown backend: {backend}"


def run_colabfold_msa_only(fasta: Path, out_dir: Path) -> PredictionResult:
    """Run colabfold_search via WSL to produce a .a3m without structure prediction.

    Requires COLABFOLD_DB env var pointing to a local MMseqs2 database (WSL path).
    On the DCC workflow, prefer tools/msa.py which submits a SLURM job instead.
    This function is for local use when the MMseqs2 DB is present on the WSL filesystem.

    On success, a3m_paths in the returned PredictionResult contains the .a3m file(s).
    """
    db = _colabfold_db()
    if not db:
        return PredictionResult(
            success=False, backend="colabfold", output_dir=out_dir, runtime_s=0.0,
            error="COLABFOLD_DB env var not set — WSL path to MMseqs2 DB required for msa_only",
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    wsl_fasta = to_wsl_path(fasta)
    wsl_out = to_wsl_path(out_dir)
    cmd = [
        "wsl.exe", "-d", _wsl_distro(), "--",
        _colabfold_search_bin(), wsl_fasta, wsl_out, db,
    ]
    print(f"[predict] msa-only: {' '.join(cmd)}", flush=True)
    start = time.time()
    try:
        proc = subprocess.run(
            cmd, env=_wsl_env(), capture_output=True, text=True,
            encoding="utf-8", errors="replace",
        )
    except FileNotFoundError as e:
        return PredictionResult(
            success=False, backend="colabfold", output_dir=out_dir,
            runtime_s=time.time() - start, error=str(e),
        )
    runtime = time.time() - start
    print(f"[predict] msa-only exit code {proc.returncode}, {runtime:.1f}s", flush=True)
    if proc.returncode != 0:
        return PredictionResult(
            success=False, backend="colabfold", output_dir=out_dir,
            runtime_s=runtime, error=proc.stderr or proc.stdout,
        )
    return PredictionResult(
        success=True, backend="colabfold", output_dir=out_dir,
        runtime_s=runtime, a3m_paths=sorted(out_dir.glob("*.a3m")),
    )


def run_colabfold(
    fasta: Path,
    out_dir: Path,
    fast: bool = False,
    target_a3m: Path | None = None,
) -> PredictionResult:
    """Run ColabFold via WSL.

    fast=True adds --msa-mode single_sequence --num-recycle 1, cutting runtime
    from hours to ~5 minutes at the cost of MSA-derived accuracy. Use for
    pipeline development and testing; run without --fast for final results.

    target_a3m: when supplied, build_paired_a3m() is called to combine the
    peptide (single-sequence) with the pre-computed target MSA into a paired
    A3M file, which is then passed directly to colabfold_batch as the input.
    This skips the MSA search step for the target, reducing wall time by
    roughly N-1 MSA waits when running a peptide panel against one fixed target.
    The fasta argument is still used to read the sequence and name when building
    the paired A3M; fast and target_a3m are mutually exclusive (fast already
    implies no MSA, so there is nothing to save).
    """
    from tools.msa import build_paired_a3m  # local import avoids module-level circularity

    out_dir.mkdir(parents=True, exist_ok=True)
    bin_path = _colabfold_bin()

    if target_a3m is not None:
        # Parse sequence and name from the FASTA
        fasta_text = fasta.read_text()
        lines = [l.strip() for l in fasta_text.splitlines() if l.strip()]
        peptide_id = lines[0].lstrip(">") if lines else fasta.stem
        peptide_seq = "".join(l for l in lines[1:] if not l.startswith(">"))
        paired = build_paired_a3m(peptide_seq, peptide_id, target_a3m, out_dir)
        input_path = to_wsl_path(paired)
        print(f"[predict] using paired A3M (target MSA reuse): {paired.name}", flush=True)
    else:
        input_path = to_wsl_path(fasta)

    wsl_out = to_wsl_path(out_dir)
    cmd = ["wsl.exe", "-d", _wsl_distro(), "--", bin_path, input_path, wsl_out]
    if fast:
        cmd += ["--msa-mode", "single_sequence", "--num-recycle", "1"]
    print(f"[predict] running: {' '.join(cmd)}", flush=True)

    start = time.time()
    try:
        proc = subprocess.run(
            cmd,
            env=_wsl_env(),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except FileNotFoundError as e:
        return PredictionResult(
            success=False,
            backend="colabfold",
            output_dir=out_dir,
            runtime_s=time.time() - start,
            error=str(e),
        )

    runtime = time.time() - start
    print(f"[predict] exit code {proc.returncode}, {runtime:.1f}s", flush=True)

    if proc.returncode != 0:
        return PredictionResult(
            success=False,
            backend="colabfold",
            output_dir=out_dir,
            runtime_s=runtime,
            error=proc.stderr or proc.stdout,
        )

    return PredictionResult(
        success=True,
        backend="colabfold",
        output_dir=out_dir,
        runtime_s=runtime,
        pdb_paths=sorted(out_dir.glob("*.pdb")),
    )


# ---------------------------------------------------------------------------
# Backend: ESMFold API
# ---------------------------------------------------------------------------

def run_esmfold_api(fasta: Path, out_dir: Path) -> PredictionResult:
    raise NotImplementedError("ESMFold API wrapper not yet implemented")


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

def predict(
    fasta: Path,
    out_dir: Path,
    backend: str | None = None,
    fast: bool = False,
    target_a3m: Path | None = None,
    msa_only: bool = False,
) -> PredictionResult:
    backend = backend or os.environ.get("PREDICT_BACKEND", "colabfold")
    if backend == "colabfold":
        if msa_only:
            return run_colabfold_msa_only(fasta, out_dir)
        return run_colabfold(fasta, out_dir, fast=fast, target_a3m=target_a3m)
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
    p = argparse.ArgumentParser(description="Run structure prediction on a FASTA input.")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--fasta", type=Path, help="Input FASTA file")
    group.add_argument("--sequence", help="Inline amino acid sequence (creates a temporary FASTA)")
    p.add_argument("--name", default="query", help="Sequence name (used with --sequence)")
    p.add_argument("--out", type=Path, help="Output directory")
    p.add_argument("--backend", default=None, help="colabfold | esmfold_api")
    p.add_argument("--fast", action="store_true",
                   help="Skip MSA (single_sequence mode) + 1 recycle. ~5 min vs hours. For dev/testing only.")
    p.add_argument("--target-a3m", type=Path, default=None, metavar="PATH",
                   help=(
                       "Pre-computed target MSA (.a3m). When supplied, the peptide chain uses "
                       "single-sequence mode and the target A3M is reused directly, skipping the "
                       "MSA search step. Use this when running a peptide panel against one fixed "
                       "target: compute the target .a3m once with --msa-only, then pass it here "
                       "for all subsequent peptide runs."
                   ))
    p.add_argument("--msa-only", action="store_true",
                   help=(
                       "Run colabfold_search to produce a .a3m without structure prediction. "
                       "Requires COLABFOLD_DB env var (WSL path to MMseqs2 DB). "
                       "On DCC, prefer tools/msa.py which submits a SLURM job instead."
                   ))
    p.add_argument("--check", action="store_true", help="Verify backend availability and exit")
    args = p.parse_args()

    backend = args.backend or os.environ.get("PREDICT_BACKEND", "colabfold")

    if args.check:
        ok, msg = check_backend(backend)
        print(f"[{backend}] {'OK' if ok else 'FAIL'}: {msg}")
        raise SystemExit(0 if ok else 1)

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
        target_a3m=args.target_a3m, msa_only=args.msa_only,
    )
    print(f"success={result.success} backend={result.backend} runtime={result.runtime_s:.1f}s")
    if result.success:
        for pdb in result.pdb_paths:
            print(f"  pdb: {pdb}")
        for a3m in result.a3m_paths:
            print(f"  a3m: {a3m}")
    else:
        print(f"error: {result.error}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
