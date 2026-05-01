"""Sequence- and structure-level analyses.

Sequence features use Biopython where possible. Structure features (SASA,
interface residues, disulfide bonds) require a PDB and are populated after
prediction.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from Bio.SeqUtils.ProtParam import ProteinAnalysis

# Residue sets used by window-based flagging rules (heuristics.md § Sequence-level flags)
_CHARGED = set("DEKR")
_HYDROPHOBIC = set("ACFILMVWY")
_PROLINE_RICH_THRESHOLD = 0.25  # fraction of prolines that qualifies a window as PPII-forming


@dataclass
class SequenceFeatures:
    sequence: str
    length: int
    molecular_weight: float
    pI: float
    aromatic_fraction: float
    cysteine_positions: list[int] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)


@dataclass
class StructureFeatures:
    pdb_path: Path
    sasa_total: float
    sasa_per_chain: dict[str, float] = field(default_factory=dict)
    interface_residues: list[tuple[str, int]] = field(default_factory=list)
    buried_surface_area: float | None = None
    disulfide_pairs: list[tuple[int, int]] = field(default_factory=list)


def analyze_sequence(seq: str) -> SequenceFeatures:
    """Compute basic sequence properties and flag notable patterns.

    Flags applied (see preflight/heuristics.md):
    - cysteine pairs within 8 residues
    - proline-rich / PPII stretches
    - highly charged windows (>50% charged in 10 residues)
    - hydrophobic stretches (>70% hydrophobic in 10 residues)
    - length < 5, < 20, > 40, > 2000
    """
    seq = seq.upper().strip()
    pa = ProteinAnalysis(seq)

    flags: list[str] = []

    # --- Length flags ---
    n = len(seq)
    if n < 5:
        flags.append("length<5: too short for interpretable interface — recommend exclusion")
    elif n < 20:
        flags.append("length<20: too short for informative monomer fold — confirm intent")
    if n > 40:
        flags.append("length>40: ipTM denominator inflation — actifpTM is essential")
    if n > 2000:
        flags.append("length>2000: ColabFold may OOM — consider domain-wise prediction")

    # --- Cysteine pairs within 8 residues ---
    cys_positions = [i for i, aa in enumerate(seq) if aa == "C"]
    for i in range(len(cys_positions) - 1):
        if cys_positions[i + 1] - cys_positions[i] <= 8:
            flags.append(
                f"cysteine pair at positions {cys_positions[i]}-{cys_positions[i+1]}"
                " (within 8 residues): potential intramolecular disulfide — consider"
                " reduced vs. oxidized predictions"
            )

    # --- Window-based flags (window=10) ---
    window = 10
    for start in range(n - window + 1):
        chunk = seq[start : start + window]

        charged_frac = sum(aa in _CHARGED for aa in chunk) / window
        if charged_frac > 0.5:
            flags.append(
                f"charged window [{start}-{start+window}]: {charged_frac:.0%} charged"
                " residues — likely IDR; run IUPred3"
            )
            break  # one flag per sequence is enough to trigger action

    for start in range(n - window + 1):
        chunk = seq[start : start + window]

        hydrophobic_frac = sum(aa in _HYDROPHOBIC for aa in chunk) / window
        if hydrophobic_frac > 0.7:
            flags.append(
                f"hydrophobic window [{start}-{start+window}]: {hydrophobic_frac:.0%}"
                " hydrophobic residues — possible TM segment or aggregation-prone"
            )
            break

    # Proline-rich: >25% prolines in any 10-residue window, or >3 consecutive prolines
    for start in range(n - window + 1):
        chunk = seq[start : start + window]
        if chunk.count("P") / window >= _PROLINE_RICH_THRESHOLD:
            flags.append(
                f"proline-rich window [{start}-{start+window}]: PPII-forming motif likely"
                " disordered even in complex — confidence interpretation may be unreliable"
            )
            break
    if "PPP" in seq and not any("proline-rich" in f for f in flags):
        flags.append(
            "polyproline stretch (PPP+): PPII-forming motif — confidence interpretation"
            " may be unreliable"
        )

    # --- Physicochemical properties via Biopython ---
    mw = pa.molecular_weight()
    pI = pa.isoelectric_point()
    aa_counts = pa.count_amino_acids()
    aromatic_fraction = sum(aa_counts.get(aa, 0) for aa in "FWY") / n

    return SequenceFeatures(
        sequence=seq,
        length=n,
        molecular_weight=round(mw, 2),
        pI=round(pI, 2),
        aromatic_fraction=round(aromatic_fraction, 4),
        cysteine_positions=cys_positions,
        flags=flags,
    )


def analyze_structure(pdb_path: Path, interface_cutoff_a: float = 5.0) -> StructureFeatures:
    """Compute SASA, interface residues, and disulfide pairs from a PDB."""
    raise NotImplementedError


def detect_disulfides(pdb_path: Path, distance_cutoff_a: float = 3.0) -> list[tuple[int, int]]:
    raise NotImplementedError


def query_iupred3(seq: str) -> list[float]:
    """Per-residue disorder propensity via IUPred3 web API."""
    raise NotImplementedError
