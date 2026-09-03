"""Contact fingerprinting and epitope clustering for binding panel results.

Given a collection of StructureFeatures (one per peptide–target complex), computes
binary contact vectors over target residue positions and clusters peptides by Jaccard
distance to identify distinct epitope families.

Pass 1: contact-only fingerprints (no pLDDT gating). A future pass will add
pLDDT-threshold filtering once real-run data confirms the clustering signal.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from tools.analyze import StructureFeatures


@dataclass
class ContactFingerprint:
    peptide_id: str
    target_chains: tuple[str, ...]
    residue_ids: list[int]    # 1-indexed target residue numbers in contact
    vector: np.ndarray        # bool, shape (universe_size,)


def make_fingerprint(
    peptide_id: str,
    structure: StructureFeatures,
    target_chains: tuple[str, ...] = ("A",),
    universe_size: int = 0,
) -> ContactFingerprint | None:
    """Build a contact fingerprint from interface_residues.

    Returns None if no interface residues on target chains are present.
    universe_size: if 0, uses max observed residue number as the vector length.
    All fingerprints in a panel must share the same universe_size for valid
    Jaccard comparisons — build_panel_report_inputs establishes this with a
    pre-pass before calling make_fingerprint per entry.
    """
    if not structure.interface_residues:
        return None
    target_contacts = [
        r[1] for r in structure.interface_residues if r[0] in target_chains
    ]
    if not target_contacts:
        return None
    res_ids = sorted(set(target_contacts))
    actual_size = universe_size if universe_size > 0 else max(res_ids)
    return ContactFingerprint(
        peptide_id=peptide_id,
        target_chains=target_chains,
        residue_ids=res_ids,
        vector=_to_vector(res_ids, actual_size),
    )


def _to_vector(res_ids: list[int], universe_size: int) -> np.ndarray:
    v = np.zeros(universe_size, dtype=bool)
    for i in res_ids:
        idx = i - 1  # 1-indexed → 0-indexed
        if 0 <= idx < universe_size:
            v[idx] = True
    return v


def jaccard_matrix(fps: list[ContactFingerprint]) -> np.ndarray:
    """Pairwise Jaccard similarity matrix (n × n, values in [0, 1])."""
    n = len(fps)
    mat = np.zeros((n, n))
    for i in range(n):
        for j in range(i, n):
            a, b = fps[i].vector, fps[j].vector
            intersection = float(np.sum(a & b))
            union = float(np.sum(a | b))
            sim = intersection / union if union > 0.0 else 0.0
            mat[i, j] = mat[j, i] = sim
    return mat


def cluster_fingerprints(
    fps: list[ContactFingerprint],
    threshold: float = 0.3,
) -> dict[str, str]:
    """Single-linkage hierarchical clustering on Jaccard distance.

    Returns mapping of peptide_id → cluster label (e.g. "Epitope-1").
    threshold: Jaccard *distance* cutoff; 0 = identical contacts, 1 = no overlap.
    Lower threshold → tighter clusters.
    """
    if not fps:
        return {}
    if len(fps) == 1:
        return {fps[0].peptide_id: "Epitope-1"}

    from scipy.cluster.hierarchy import fcluster, linkage

    sim = jaccard_matrix(fps)
    dist = 1.0 - sim
    n = len(fps)
    condensed = dist[np.triu_indices(n, k=1)]
    Z = linkage(condensed, method="single")
    labels = fcluster(Z, t=threshold, criterion="distance")
    return {fp.peptide_id: f"Epitope-{int(lbl)}" for fp, lbl in zip(fps, labels)}
