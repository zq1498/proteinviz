"""Structural comparison: predicted PDB vs reference PDB.

Computes TM-score and Cα RMSD via tmtools (installed in WSL pixi env).
Optionally enriches with pLDDT from a ColabFold score JSON.

Usage:
    python -m tools.compare --pred jobs/3v03/3v03_rank_001.pdb --ref 3V03.pdb
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from tools.predict import _wsl_distro, _wsl_env, to_wsl_path

_PIXI_PYTHON = "/home/zach/localcolabfold/.pixi/envs/default/bin/python"
_INNER_SCRIPT = Path(__file__).parent / "_compare_inner.py"


@dataclass
class ComparisonResult:
    pred_pdb: Path
    ref_pdb: Path
    tmscore_pred: float
    tmscore_ref: float
    rmsd_ca: float
    n_pred: int
    n_ref: int
    mean_plddt: float | None = None
    per_residue_plddt: list[float] | None = None
    ptm: float | None = None


def compare(
    pred_pdb: Path,
    ref_pdb: Path,
    score_json: Path | None = None,
    pred_chain: str | None = None,
    ref_chain: str | None = None,
) -> ComparisonResult:
    """Align pred_pdb to ref_pdb, compute TM-score + RMSD.

    score_json: optional ColabFold scores JSON to attach pLDDT + pTM.
    """
    wsl_pred = to_wsl_path(pred_pdb)
    wsl_ref = to_wsl_path(ref_pdb)
    wsl_script = to_wsl_path(_INNER_SCRIPT)

    cmd = [
        "wsl.exe", "-d", _wsl_distro(), "--",
        _PIXI_PYTHON, wsl_script,
        "--pred", wsl_pred,
        "--ref", wsl_ref,
    ]
    if pred_chain:
        cmd += ["--pred-chain", pred_chain]
    if ref_chain:
        cmd += ["--ref-chain", ref_chain]

    proc = subprocess.run(
        cmd,
        env=_wsl_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if proc.returncode != 0:
        raise RuntimeError(f"comparison failed: {proc.stderr}")

    data = json.loads(proc.stdout)

    result = ComparisonResult(
        pred_pdb=pred_pdb,
        ref_pdb=ref_pdb,
        tmscore_pred=data["tmscore_pred"],
        tmscore_ref=data["tmscore_ref"],
        rmsd_ca=data["rmsd_ca"],
        n_pred=data["n_pred"],
        n_ref=data["n_ref"],
    )

    if score_json and score_json.exists():
        scores = json.loads(score_json.read_text())
        plddt = scores.get("plddt", [])
        result.per_residue_plddt = plddt
        result.mean_plddt = sum(plddt) / len(plddt) if plddt else None
        result.ptm = scores.get("ptm")

    return result


def print_report(r: ComparisonResult) -> None:
    print(f"\nStructural comparison: {r.pred_pdb.name} vs {r.ref_pdb.name}")
    print(f"  Residues  pred={r.n_pred}  ref={r.n_ref}")
    print(f"  TM-score (norm by pred) : {r.tmscore_pred:.4f}")
    print(f"  TM-score (norm by ref)  : {r.tmscore_ref:.4f}")
    print(f"  Ca RMSD                 : {r.rmsd_ca:.2f} A")
    if r.mean_plddt is not None:
        print(f"  Mean pLDDT              : {r.mean_plddt:.1f}")
    if r.ptm is not None:
        print(f"  pTM                     : {r.ptm:.3f}")
    print()
    _interpret(r)


def _interpret(r: ComparisonResult) -> None:
    tm = r.tmscore_ref
    if tm >= 0.90:
        verdict = "Near-identical fold (TM >= 0.90)."
    elif tm >= 0.70:
        verdict = "Same fold, minor differences (0.70 <= TM < 0.90)."
    elif tm >= 0.50:
        verdict = "Likely same topology, significant local deviation (0.50 <= TM < 0.70)."
    else:
        verdict = "Different fold or poor prediction (TM < 0.50)."
    print(f"  Interpretation: {verdict}")
    if r.rmsd_ca > 3.0:
        print(f"  Note: Ca RMSD {r.rmsd_ca:.1f} A is above 3 A — local deviations likely in loops or termini.")


def main() -> None:
    p = argparse.ArgumentParser(description="Compare a predicted PDB to a reference PDB.")
    p.add_argument("--pred", type=Path, required=True, help="Predicted PDB")
    p.add_argument("--ref", type=Path, required=True, help="Reference/experimental PDB")
    p.add_argument("--scores", type=Path, default=None, help="ColabFold score JSON (optional)")
    p.add_argument("--pred-chain", default=None)
    p.add_argument("--ref-chain", default=None)
    args = p.parse_args()

    result = compare(args.pred, args.ref, args.scores, args.pred_chain, args.ref_chain)
    print_report(result)


if __name__ == "__main__":
    main()
