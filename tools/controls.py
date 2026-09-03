"""Composition-matched scrambled control peptide generation.

Scrambled controls share amino acid composition with their parent peptide but
have shuffled order — preserving length, charge, hydrophobicity, and disorder
propensity. Running them alongside test peptides in the same panel turns
composite_score from an absolute number into a per-panel z-score.

Control IDs follow the pattern ``{parent_id}__scram{k}`` (k = 0-indexed copy
number). The double-underscore prefix is a reserved delimiter; peptide IDs that
contain ``__scram`` without following this exact pattern will be treated as
controls and may produce incorrect z-scores. Don't use ``__scram`` in peptide
IDs unless you mean this.
"""

from __future__ import annotations

import random
import re

CONTROL_SUFFIX_RE = re.compile(r"^(.+)__scram(\d+)$")


def make_scrambled_controls(
    peptides: list[tuple[str, str]],
    n_per_peptide: int = 1,
    seed: int = 0,
    preserve_termini: bool = False,
) -> list[tuple[str, str]]:
    """Return composition-matched scrambled controls for each peptide.

    Args:
        peptides: List of (peptide_id, sequence) tuples — the test peptides.
        n_per_peptide: Number of independent scrambles per peptide.
        seed: RNG seed for reproducibility. Same seed + same inputs = same controls.
        preserve_termini: If True and len(seq) > 2, keep the first and last
            residue fixed and shuffle only the inner positions.

    Returns:
        List of (control_id, scrambled_seq) tuples where control_id is
        ``{peptide_id}__scram{k}`` for k in range(n_per_peptide).
    """
    rng = random.Random(seed)
    out: list[tuple[str, str]] = []
    for pid, seq in peptides:
        for k in range(n_per_peptide):
            out.append((f"{pid}__scram{k}", _shuffle(seq, rng, preserve_termini)))
    return out


def _shuffle(seq: str, rng: random.Random, preserve_termini: bool) -> str:
    chars = list(seq)
    if preserve_termini and len(chars) > 2:
        inner = chars[1:-1]
        rng.shuffle(inner)
        return chars[0] + "".join(inner) + chars[-1]
    rng.shuffle(chars)
    return "".join(chars)


def is_control_id(peptide_id: str) -> bool:
    """Return True if peptide_id matches the ``{parent}__scram{k}`` pattern."""
    return bool(CONTROL_SUFFIX_RE.match(peptide_id))


def parent_id_of(control_id: str) -> str | None:
    """Return the parent test-peptide ID for a control ID, or None if not a control."""
    m = CONTROL_SUFFIX_RE.match(control_id)
    return m.group(1) if m else None
