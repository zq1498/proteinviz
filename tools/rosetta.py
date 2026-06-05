"""PyRosetta analysis functions — caller side, runs in the proteinviz env (Python 3.12).

PyRosetta is only available in the proteinviz_pyrosetta conda env (Python 3.14,
py314_0 wheel from conda.graylab.jhu.edu). This module bridges the version gap via
subprocess: each function serializes arguments to JSON, spawns pyrosetta_worker.py
under the pyrosetta interpreter, and deserializes the result. The two environments
never share memory — all data crosses the boundary as plain JSON.

Protocol (stdin → stdout):
  {"function": "<name>", "kwargs": {...}} → {"result": <data>, "error": null}

To add a new analysis:
  1. Implement _my_function(...) -> dict | list[dict] in tools/pyrosetta_worker.py
  2. Add it to _FUNCTIONS in that file
  3. Add a dataclass for the return type here (if the result has named fields)
  4. Add a public wrapper below that calls _call_worker("my_function", ...)
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Absolute path to the pyrosetta env's interpreter.
# proteinviz_pyrosetta is Python 3.14; changing this to the proteinviz python (3.12)
# will fail at import time inside the worker because pyrosetta has no py312 build.
_PYROSETTA_PYTHON = Path("/home/zach/miniconda3/envs/proteinviz_pyrosetta/bin/python")
_WORKER = Path(__file__).parent / "pyrosetta_worker.py"


@dataclass
class InterfaceScore:
    interface: str
    total_score_reu: float
    dG_separated: float
    dSASA: float
    n_interface_residues: int


@dataclass
class ResidueEnergy:
    rosetta_resnum: int
    pdb_resnum: int
    chain: str
    resname: str
    total_energy: float


@dataclass
class AlaScanEntry:
    rosetta_resnum: int
    pdb_resnum: int
    chain: str
    resname: str
    ddG: float  # positive = wildtype contributes favorably (hot-spot candidate)


@dataclass
class ConfidentHotSpot:
    rosetta_resnum: int
    pdb_resnum: int
    chain: str
    resname: str
    ddG: float
    plddt: float
    confident: bool  # plddt >= caller-supplied threshold


def _plddt_map_from_pdb(pdb_path: Path | str) -> dict[tuple[str, int], float]:
    """Read per-residue pLDDT from ColabFold PDB B-factor column.

    ColabFold stores pLDDT as B-factors; all atoms of a residue share the same
    value. Returns {(chain, pdb_resnum): plddt} for every ATOM record residue.
    """
    from collections import defaultdict

    buckets: dict[tuple[str, int], list[float]] = defaultdict(list)
    with open(pdb_path) as fh:
        for line in fh:
            if not line.startswith("ATOM"):
                continue
            chain = line[21]
            resnum = int(line[22:26].strip())
            bfactor = float(line[60:66].strip())
            buckets[(chain, resnum)].append(bfactor)
    return {key: sum(vals) / len(vals) for key, vals in buckets.items()}


def _call_worker(function: str, **kwargs: Any) -> Any:
    request = json.dumps({"function": function, "kwargs": kwargs})
    proc = subprocess.run(
        [str(_PYROSETTA_PYTHON), str(_WORKER)],
        input=request,
        capture_output=True,
        text=True,
    )
    try:
        # PyRosetta's C extension prints a license banner to stdout before the
        # worker writes its JSON. Strip any prefix that precedes the first '{'.
        raw = proc.stdout
        start = raw.find("{")
        response = json.loads(raw[start:] if start >= 0 else raw)
    except json.JSONDecodeError:
        raise RuntimeError(
            f"pyrosetta_worker returned non-JSON output (exit {proc.returncode}).\n"
            f"stdout: {proc.stdout[:500]}\nstderr: {proc.stderr[:500]}"
        )
    if response.get("error"):
        raise RuntimeError(
            f"pyrosetta_worker error in {function!r}: {response['error']}\n"
            f"stderr: {proc.stderr[:500]}"
        )
    return response["result"]


def score_interface(pdb_path: Path | str, interface: str = "A_B") -> InterfaceScore:
    """REF2015 interface score: dG_separated, dSASA, and interface residue count.

    interface: Rosetta chain-jump string, e.g. "A_B" for chain A vs chain B,
    "AB_C" for chains A+B as one body vs chain C. Defaults to "A_B" (target vs peptide
    in standard ColabFold output where target is chain A and peptide is chain B).
    For homodimer panel screens (predict_panel with a homo-oligomeric target), ColabFold
    assigns chains A+B to the dimer and C to the peptide — use interface="AB_C".
    """
    data = _call_worker("score_interface", pdb_path=str(pdb_path), interface=interface)
    return InterfaceScore(**data)


def per_residue_energies(pdb_path: Path | str) -> list[ResidueEnergy]:
    """Per-residue REF2015 total energy for every residue in the complex.

    Useful for identifying high-energy regions (clashes, strain) or comparing
    residue-level contributions across models. Both Rosetta and PDB residue numbers
    are returned; prefer PDB numbers when cross-referencing with ColabFold outputs.
    """
    data = _call_worker("per_residue_energies", pdb_path=str(pdb_path))
    return [ResidueEnergy(**r) for r in data]


def alanine_scan(
    pdb_path: Path | str,
    interface: str = "A_B",
    rigorous: bool = False,
) -> list[AlaScanEntry]:
    """Single-point alanine scan across interface residues, sorted by ΔΔG descending.

    Entries with the largest positive ΔΔG are hot-spot candidates — mutating them
    to alanine is most destabilising. Ala and Gly residues are skipped (no sidechain
    to remove).

    rigorous=False (default): fast mode — ΔΔG is the full-complex score difference.
        Good for relative ranking within a single peptide; conflates folding stability
        with binding affinity.
    rigorous=True: binding ddG — ΔG_separated(mut) − ΔG_separated(wt) with FastRelax
        applied to each mutant. Use for post-screening hit characterisation where
        hot-spot precision matters more than throughput (~2–5× slower per residue).
    """
    data = _call_worker(
        "alanine_scan", pdb_path=str(pdb_path), interface=interface, rigorous=rigorous
    )
    return [AlaScanEntry(**r) for r in data]


def confidence_filter_scan(
    scan: list[AlaScanEntry],
    plddt_pdb: Path | str,
    plddt_threshold: float = 70.0,
) -> list[ConfidentHotSpot]:
    """Cross-reference alanine scan results with per-residue pLDDT confidence.

    PyRosetta ΔΔG values are computed from ColabFold coordinates — residues with
    low pLDDT have poorly-constrained geometry, making their ΔΔG estimates
    unreliable. This function flags each hit as confident (pLDDT >= threshold)
    or uncertain, then returns them sorted confident-first, then by ΔΔG descending
    within each tier so the most actionable candidates surface at the top.

    Takes existing scan results rather than re-running alanine_scan internally,
    so rigorous-mode scans are not repeated.

    plddt_pdb: MUST be the unrelaxed ColabFold PDB (e.g. *_unrelaxed_rank_001_*.pdb).
        Amber relaxation zeroes out B-factors, so relaxed PDBs carry no pLDDT
        information. Pass the relaxed PDB to alanine_scan for better scoring geometry,
        and the corresponding unrelaxed PDB here for confidence annotation.

    plddt_threshold: 70.0 follows the RCSB/UniProt "confident" cutoff. Lower to
        50.0 to include "low confidence" residues in the confident tier (not
        recommended for hot-spot reporting).
    """
    plddt_map = _plddt_map_from_pdb(plddt_pdb)
    results = []
    for entry in scan:
        plddt = plddt_map.get((entry.chain, entry.pdb_resnum), 0.0)
        results.append(ConfidentHotSpot(
            rosetta_resnum=entry.rosetta_resnum,
            pdb_resnum=entry.pdb_resnum,
            chain=entry.chain,
            resname=entry.resname,
            ddG=entry.ddG,
            plddt=plddt,
            confident=plddt >= plddt_threshold,
        ))
    return sorted(results, key=lambda h: (not h.confident, -h.ddG))
