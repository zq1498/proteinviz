"""Inner comparison script — runs inside WSL using the pixi Python env.

Invoked by compare.py via subprocess. Outputs a single JSON object to stdout.
Do not import this directly from Windows-side code; use compare.py instead.
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np
from tmtools import tm_align


def parse_ca_coords(pdb_path: str, chain: str | None = None) -> tuple[np.ndarray, str]:
    """Extract Cα coordinates and sequence from a PDB file.

    Returns (coords, sequence) where coords is (N, 3) float32.
    If chain is None, uses the first chain encountered.
    """
    _aa3to1 = {
        "ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C",
        "GLN": "Q", "GLU": "E", "GLY": "G", "HIS": "H", "ILE": "I",
        "LEU": "L", "LYS": "K", "MET": "M", "PHE": "F", "PRO": "P",
        "SER": "S", "THR": "T", "TRP": "W", "TYR": "Y", "VAL": "V",
        "SEC": "U", "PYL": "O", "MSE": "M",
    }
    coords = []
    seq = []
    seen_chain = chain
    last_res_num = None

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
            if c != seen_chain:
                continue
            if atom_name != "CA":
                continue
            if res_num == last_res_num:
                continue
            last_res_num = res_num
            x = float(line[30:38])
            y = float(line[38:46])
            z = float(line[46:54])
            coords.append([x, y, z])
            seq.append(_aa3to1.get(res_name, "X"))

    return np.array(coords, dtype=np.float32), "".join(seq)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--pred", required=True, help="Predicted PDB (WSL path)")
    p.add_argument("--ref", required=True, help="Reference PDB (WSL path)")
    p.add_argument("--pred-chain", default=None)
    p.add_argument("--ref-chain", default=None)
    args = p.parse_args()

    pred_coords, pred_seq = parse_ca_coords(args.pred, args.pred_chain)
    ref_coords, ref_seq = parse_ca_coords(args.ref, args.ref_chain)

    result = tm_align(pred_coords, ref_coords, pred_seq, ref_seq)

    output = {
        "tmscore_pred": float(result.tm_norm_chain1),
        "tmscore_ref": float(result.tm_norm_chain2),
        "rmsd_ca": float(result.rmsd),
        "n_pred": len(pred_seq),
        "n_ref": len(ref_seq),
        "pred_seq": pred_seq[:20] + "...",
        "ref_seq": ref_seq[:20] + "...",
    }
    json.dump(output, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
