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


def _parse_a3m(path: Path) -> tuple[str, list[tuple[str, str]]]:
    """Return (query_seq, [(header, seq), ...]) from a single-chain A3M file.

    Preserves per-row >header lines. ColabFold requires a header before every
    MSA row; discarding headers causes the entire file to be read as one giant
    concatenated sequence when the paired A3M is parsed.
    """
    lines = path.read_text().splitlines()
    if not lines or not lines[0].startswith(">"):
        raise ValueError(f"Not a valid A3M file (no leading header): {path}")

    rows: list[tuple[str, str]] = []
    query_seq: str | None = None
    current_header: str = ""
    current: list[str] = []
    is_first = True
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(">"):
            if current:
                seq = "".join(current)
                if is_first:
                    query_seq = seq
                    is_first = False
                else:
                    rows.append((current_header, seq))
                current = []
            current_header = stripped
        elif stripped == "#":
            continue  # chain-break markers only appear in multimer A3Ms
        else:
            current.append(stripped)
    if current:
        seq = "".join(current)
        if is_first:
            query_seq = seq
        else:
            rows.append((current_header, seq))

    if query_seq is None:
        raise ValueError(f"No sequences found in {path}")
    return query_seq, rows


def build_paired_a3m(
    peptide_seq: str,
    peptide_id: str,
    target_a3m: Path,
    out_dir: Path,
    complex_name: str | None = None,
    n_target_copies: int = 1,
) -> Path:
    """Build a ColabFold multimer A3M for a target:peptide complex from a cached target MSA.

    Produces the ColabFold multimer A3M metadata format that unserialize_msa() in
    colabfold/batch.py expects. The _split_by_aln function splits each MSA row into
    per-chain segments by counting aligned (non-lowercase) positions against the chain
    lengths encoded in the metadata header:

        #<target_len>,<pep_len>\t1,1   ← metadata: chain lengths TAB cardinalities
        >complex_name                   ← query header
        TARGET_SEQ + PEPTIDE_SEQ        ← concatenated query (no ':' separator)
        >UniRef100_HOM1_header
        HOM1_SEQ                        ← target MSA rows; chain B segment empty → unpaired A
        ...
        >peptide_id
        <target_len gaps>PEPTIDE_SEQ    ← chain A = gaps (no aa) → unpaired B

    The paired block is effectively empty for peptide:protein complexes because short
    peptides have no co-evolving homologs that pair with the target.

    Args:
        peptide_seq:   Amino acid string for the peptide (chain B).
        peptide_id:    Short identifier used in the filename and header.
        target_a3m:    Pre-computed single-chain A3M for the target protein (chain A).
        out_dir:       Directory to write the paired A3M into.
        complex_name:  Header name for the combined complex. Defaults to
                       '{target_stem}_{peptide_id}'.

    Returns:
        Path to the written paired A3M file.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    name = complex_name or f"{target_a3m.stem}_{peptide_id}"
    out_path = out_dir / f"{name}.a3m"

    target_seq, msa_rows = _parse_a3m(target_a3m)
    # TODO multi-chain targets (e.g. homodimer target): target_seq would need to come from
    # a multimer A3M and len(target_seq) would need to reflect both chains' lengths.
    # For now, target_seq is always a single-chain sequence.
    target_gap = "-" * len(target_seq)

    with out_path.open("w") as f:
        # Metadata: chain lengths TAB cardinalities.
        # n_target_copies > 1 for homo-oligomeric targets (e.g. VEGF homodimer → 2).
        f.write(f"#{len(target_seq)},{len(peptide_seq)}\t{n_target_copies},1\n")
        f.write(f">{name}\n")
        # Concatenated query sequence — no ':' separator; _split_by_aln splits by length
        f.write(f"{target_seq}{peptide_seq}\n")
        for header, row in msa_rows:
            f.write(f"{header}\n")
            # Write just the target sequence; _split_by_aln sees an empty chain B (→ unpaired A)
            f.write(f"{row}\n")
        # Peptide single-sequence: target_gap fills chain A aligned positions so
        # _split_by_aln assigns the peptide to chain B (→ unpaired B)
        f.write(f">{peptide_id}\n")
        f.write(f"{target_gap}{peptide_seq}\n")

    print(
        f"[msa] wrote paired A3M → {out_path} "
        f"(target {len(target_seq)} aa ×{n_target_copies}, "
        f"peptide {len(peptide_seq)} aa, {len(msa_rows)} target MSA rows)",
        flush=True,
    )
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
