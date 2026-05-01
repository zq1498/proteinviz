"""DCC-based MSA search via SLURM.

Workflow:
  1. SCP local FASTA → DCC
  2. Render and submit cluster/msa_search.sh via sbatch over SSH
  3. Poll squeue until job completes or fails
  4. SCP the .a3m back to local out_dir
  5. Caller hands the .a3m to predict.py via --msa-path

Environment variables (or set in .env):
  DCC_HOST      SSH target, default: zq36@dcc-login.oit.duke.edu
  DCC_KEY       Path to SSH private key, default: ~/.ssh/id_dcc
  DCC_WORK      Base work directory on DCC, default: /work/zq36
  DCC_CF_BIN    colabfold_search binary on DCC
  DCC_DB_DIR    MMseqs2 database directory on DCC
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Config (all overridable via env)
# ---------------------------------------------------------------------------

def _cfg(key: str, default: str) -> str:
    return os.environ.get(key, default)

def _dcc_host() -> str:
    return _cfg("DCC_HOST", "zq36@dcc-login.oit.duke.edu")

def _dcc_key() -> str:
    return _cfg("DCC_KEY", str(Path.home() / ".ssh" / "id_dcc"))

def _dcc_work() -> str:
    return _cfg("DCC_WORK", "/work/zq36")

def _dcc_cf_bin() -> str:
    return _cfg("DCC_CF_BIN", "/work/zq36/localcolabfold/.pixi/envs/default/bin/colabfold_search")

def _dcc_db_dir() -> str:
    return _cfg("DCC_DB_DIR", "/work/zq36/colabfold_db")

_TEMPLATE = Path(__file__).parent.parent / "cluster" / "msa_search.sh"


# ---------------------------------------------------------------------------
# SSH / SCP helpers
# ---------------------------------------------------------------------------

def _ssh_opts() -> list[str]:
    return [
        "-i", _dcc_key(),
        "-o", "BatchMode=yes",           # fail immediately if key auth unavailable
        "-o", "StrictHostKeyChecking=no",
        "-o", "ConnectTimeout=15",
    ]


def _ssh(cmd: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run a shell command on DCC over SSH. Returns CompletedProcess."""
    full = ["ssh"] + _ssh_opts() + [_dcc_host(), cmd]
    return subprocess.run(full, capture_output=True, text=True, check=check,
                          encoding="utf-8", errors="replace")


def _scp_to(local: Path, remote: str) -> None:
    cmd = ["scp"] + _ssh_opts() + [str(local), f"{_dcc_host()}:{remote}"]
    subprocess.run(cmd, check=True)


def _scp_from(remote: str, local: Path) -> None:
    local.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["scp"] + _ssh_opts() + [f"{_dcc_host()}:{remote}", str(local)]
    subprocess.run(cmd, check=True)


# ---------------------------------------------------------------------------
# SLURM helpers
# ---------------------------------------------------------------------------

def _submit_job(job_name: str, remote_fasta: str, remote_out: str) -> str:
    """Render the SLURM template and submit it. Returns the SLURM job ID."""
    template = _TEMPLATE.read_text()
    script = (template
              .replace("__JOB_NAME__", job_name)
              .replace("__FASTA__", remote_fasta)
              .replace("__OUT_DIR__", remote_out)
              .replace("__DB_DIR__", _dcc_db_dir()))

    # Write rendered script to a temp file, SCP it, then sbatch it
    with tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False) as f:
        f.write(script)
        tmp_path = Path(f.name)

    remote_script = f"{_dcc_work()}/msa_logs/cf_msa_{job_name}.sh"
    _ssh(f"mkdir -p {_dcc_work()}/msa_logs")
    _scp_to(tmp_path, remote_script)
    tmp_path.unlink()

    result = _ssh(f"sbatch {remote_script}")
    # sbatch output: "Submitted batch job 12345678"
    match = re.search(r"(\d+)", result.stdout)
    if not match:
        raise RuntimeError(f"Could not parse job ID from sbatch output: {result.stdout}")
    return match.group(1)


def _job_status(job_id: str) -> str:
    """Return SLURM job state string, or 'UNKNOWN' if not found."""
    result = _ssh(f"squeue -j {job_id} -h -o %T", check=False)
    state = result.stdout.strip()
    if not state:
        # Job no longer in queue — query sacct for final state
        result2 = _ssh(
            f"sacct -j {job_id} --format=State --noheader -X", check=False
        )
        state = result2.stdout.strip().split()[0] if result2.stdout.strip() else "UNKNOWN"
    return state


def _wait_for_job(job_id: str, poll_interval_s: int = 30) -> str:
    """Block until job leaves the queue. Returns final state."""
    terminal = {"COMPLETED", "FAILED", "CANCELLED", "TIMEOUT", "NODE_FAIL", "OUT_OF_MEMORY"}
    print(f"[msa] waiting for SLURM job {job_id}...", flush=True)
    while True:
        state = _job_status(job_id)
        print(f"[msa] job {job_id}: {state}", flush=True)
        if state in terminal:
            return state
        time.sleep(poll_interval_s)


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------

@dataclass
class MSAResult:
    success: bool
    job_id: str
    local_a3m: Path | None
    final_state: str
    error: str | None = None


def run_msa(fasta: Path, out_dir: Path, job_name: str | None = None) -> MSAResult:
    """Upload FASTA, run colabfold_search on DCC, download .a3m.

    Args:
        fasta:    Local FASTA file (single sequence).
        out_dir:  Local directory to write the .a3m into.
        job_name: Short label for the SLURM job. Defaults to stem of fasta + date.

    Returns MSAResult. On success, local_a3m points to the downloaded file.
    """
    if job_name is None:
        job_name = f"{fasta.stem}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    work = _dcc_work()
    remote_fasta = f"{work}/msa_inputs/{job_name}.fasta"
    remote_out   = f"{work}/msa_outputs/{job_name}"

    try:
        # 1. Upload FASTA
        _ssh(f"mkdir -p {work}/msa_inputs")
        _scp_to(fasta, remote_fasta)
        print(f"[msa] uploaded {fasta} → {_dcc_host()}:{remote_fasta}", flush=True)

        # 2. Submit SLURM job
        job_id = _submit_job(job_name, remote_fasta, remote_out)
        print(f"[msa] submitted job {job_id}", flush=True)

        # 3. Wait
        final_state = _wait_for_job(job_id)
        if final_state != "COMPLETED":
            return MSAResult(success=False, job_id=job_id, local_a3m=None,
                             final_state=final_state,
                             error=f"Job ended with state {final_state}")

        # 4. Download .a3m
        result = _ssh(f"ls {remote_out}/*.a3m 2>/dev/null | head -1")
        remote_a3m = result.stdout.strip()
        if not remote_a3m:
            return MSAResult(success=False, job_id=job_id, local_a3m=None,
                             final_state=final_state,
                             error="Job completed but no .a3m file found in output dir")

        local_a3m = out_dir / Path(remote_a3m).name
        _scp_from(remote_a3m, local_a3m)
        print(f"[msa] downloaded {remote_a3m} → {local_a3m}", flush=True)

        return MSAResult(success=True, job_id=job_id, local_a3m=local_a3m,
                         final_state=final_state)

    except Exception as e:
        return MSAResult(success=False, job_id="unknown", local_a3m=None,
                         final_state="ERROR", error=str(e))


def build_paired_a3m(
    peptide_seq: str,
    peptide_id: str,
    target_a3m: Path,
    out_dir: Path,
) -> Path:
    """Produce a ColabFold-compatible paired A3M for one peptide against a pre-built target MSA.

    Format written:
        >{peptide_id}
        {peptide_seq}
        *                   ← ColabFold multimer chain-break separator
        {target A3M verbatim}

    The resulting file is passed directly as the positional input to colabfold_batch
    (instead of a FASTA), which causes ColabFold to skip the MSA lookup step.
    This eliminates N-1 redundant MSA searches when running a peptide panel against
    a single fixed target.

    Args:
        peptide_seq:  Amino acid string for the peptide (chain A, single-sequence).
        peptide_id:   Short identifier used in the filename and A3M header.
        target_a3m:   Pre-computed A3M file for the target (chain B).
        out_dir:      Directory to write the paired A3M into.

    Returns:
        Path to the written paired A3M file.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{peptide_id}_paired.a3m"
    target_content = target_a3m.read_text()
    with out_path.open("w") as f:
        f.write(f">{peptide_id}\n{peptide_seq}\n*\n")
        f.write(target_content)
    print(f"[msa] wrote paired A3M → {out_path}", flush=True)
    return out_path


def check_connection() -> tuple[bool, str]:
    """Verify SSH to DCC is working. Returns (ok, message)."""
    try:
        result = _ssh("echo ok")
        if "ok" in result.stdout:
            return True, f"SSH to {_dcc_host()} OK"
        return False, f"Unexpected response: {result.stdout}"
    except subprocess.CalledProcessError as e:
        return False, f"SSH failed: {e.stderr}"
    except FileNotFoundError:
        return False, "ssh not found on PATH"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import sys

    p = argparse.ArgumentParser(description="Run MSA search on DCC via SLURM.")
    p.add_argument("--fasta", type=Path, help="Local FASTA file")
    p.add_argument("--out", type=Path, help="Local directory for .a3m output")
    p.add_argument("--job-name", default=None)
    p.add_argument("--check", action="store_true", help="Test SSH connection and exit")
    args = p.parse_args()

    if args.check:
        ok, msg = check_connection()
        print(f"[dcc] {'OK' if ok else 'FAIL'}: {msg}")
        sys.exit(0 if ok else 1)

    if not args.fasta or not args.out:
        p.error("--fasta and --out are required")

    result = run_msa(args.fasta, args.out, job_name=args.job_name)
    print(f"success={result.success} state={result.final_state} a3m={result.local_a3m}")
    if not result.success:
        print(f"error: {result.error}", file=sys.stderr)
        sys.exit(1)
