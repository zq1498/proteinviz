"""DCC connectivity helpers and MSA utilities for ColabFold.

SSH/SCP helpers (_ssh, _scp_to, _scp_from, _dcc_host, _dcc_key, _dcc_work) are
imported by tools.predict for SLURM submission.

build_paired_a3m() constructs a ColabFold multimer A3M from a pre-computed target
MSA and a peptide sequence, enabling MSA reuse across peptide panels.

run_msa() is a legacy stub — the standalone cluster/msa_search.sh was removed when
MSA search was folded into cluster/predict.sh. Use predict_structure() on a monomer
FASTA to obtain a target A3M, then pass result.a3m_paths to build_paired_a3m().

Environment variables (or set in .env):
  DCC_HOST      SSH target, default: zq36@dcc-login.oit.duke.edu
  DCC_KEY       Path to SSH private key, default: ~/.ssh/id_ed25519
  DCC_WORK      Base work directory on DCC, default: /work/zq36
  DCC_DB_DIR    MMseqs2 database directory on DCC
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from tools._a3m import build_paired_a3m as build_paired_a3m  # noqa: F401 — re-exported; canonical impl in tools/_a3m.py

# ---------------------------------------------------------------------------
# Config (all overridable via env)
# ---------------------------------------------------------------------------

def _cfg(key: str, default: str) -> str:
    return os.environ.get(key, default)

def _dcc_host() -> str:
    return _cfg("DCC_HOST", "zq36@dcc-login.oit.duke.edu")

def _dcc_key() -> str:
    return _cfg("DCC_KEY", str(Path.home() / ".ssh" / "id_ed25519"))

def _dcc_work() -> str:
    return _cfg("DCC_WORK", "/work/zq36")


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

    DEPRECATED: cluster/msa_search.sh was removed when MSA search was folded into
    cluster/predict.sh. This function will raise immediately.

    To obtain a target A3M for reuse with build_paired_a3m(), run predict_structure()
    on a monomer FASTA — the result's a3m_paths field contains the downloaded A3Ms.
    """
    raise FileNotFoundError(
        "cluster/msa_search.sh has been removed. "
        "MSA search now runs inside cluster/predict.sh. "
        "Call predict_structure() on a monomer FASTA to get an A3M, "
        "then pass result.a3m_paths[0] to build_paired_a3m()."
    )


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
