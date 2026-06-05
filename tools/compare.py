"""Structural comparison: predicted PDB vs reference PDB.

Computes TM-score and Cα RMSD via tmtools. Optionally enriches with pLDDT
from a ColabFold score JSON.

Usage:
    python -m tools.compare --pred jobs/3v03/3v03_rank_001.pdb --ref 3V03.pdb
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from tmtools import tm_align


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


_AA3TO1 = {
    "ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C",
    "GLN": "Q", "GLU": "E", "GLY": "G", "HIS": "H", "ILE": "I",
    "LEU": "L", "LYS": "K", "MET": "M", "PHE": "F", "PRO": "P",
    "SER": "S", "THR": "T", "TRP": "W", "TYR": "Y", "VAL": "V",
    "SEC": "U", "PYL": "O", "MSE": "M",
}


def parse_ca_coords(pdb_path: Path, chain: str | None = None) -> tuple[np.ndarray, str]:
    """Extract Cα coordinates and one-letter sequence from a PDB file.

    Returns (coords, sequence) where coords is (N, 3) float32.
    If chain is None, uses the first chain encountered.
    """
    coords: list[list[float]] = []
    seq: list[str] = []
    seen_chain = chain
    last_res_num: int | None = None

    with open(pdb_path) as f:
        for line in f:
            if not line.startswith("ATOM"):
                continue
            atom_name = line[12:16].strip()
            res_name = line[17:20].strip()
            c = line[21]
            res_num = int(line[22:26])
            if seen_chain is None:
                seen_chain = c
            if c != seen_chain or atom_name != "CA" or res_num == last_res_num:
                continue
            last_res_num = res_num
            coords.append([float(line[30:38]), float(line[38:46]), float(line[46:54])])
            seq.append(_AA3TO1.get(res_name, "X"))

    return np.array(coords, dtype=np.float32), "".join(seq)


def compare(
    pred_pdb: Path,
    ref_pdb: Path,
    score_json: Path | None = None,
    pred_chain: str | None = None,
    ref_chain: str | None = None,
) -> ComparisonResult:
    """Align pred_pdb to ref_pdb, compute TM-score + Cα RMSD."""
    pred_coords, pred_seq = parse_ca_coords(pred_pdb, pred_chain)
    ref_coords, ref_seq = parse_ca_coords(ref_pdb, ref_chain)

    alignment = tm_align(pred_coords, ref_coords, pred_seq, ref_seq)

    result = ComparisonResult(
        pred_pdb=pred_pdb,
        ref_pdb=ref_pdb,
        tmscore_pred=float(alignment.tm_norm_chain1),
        tmscore_ref=float(alignment.tm_norm_chain2),
        rmsd_ca=float(alignment.rmsd),
        n_pred=len(pred_seq),
        n_ref=len(ref_seq),
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
    print(f"  Ca RMSD                 : {r.rmsd_ca:.2f} Å")
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
        print(f"  Note: Cα RMSD {r.rmsd_ca:.1f} Å — local deviations likely in loops or termini.")


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
