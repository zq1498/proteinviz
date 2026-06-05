"""Sequence- and structure-level analyses.

Sequence features use Biopython where possible. Structure features (SASA,
interface residues, disulfide bonds) require a PDB and are populated after
prediction.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from Bio.Align import substitution_matrices
from Bio.SeqUtils.ProtParam import ProteinAnalysis

# Residue sets used by window-based flagging rules (heuristics.md § Sequence-level flags)
_CHARGED = set("DEKR")
_HYDROPHOBIC = set("ACFILMVWY")
_PROLINE_RICH_THRESHOLD = 0.25  # fraction of prolines that qualifies a window as PPII-forming

# Kyte-Doolittle scale used for GRAVY and local hydrophobic patch detection
_KD_SCALE = {
    "I": 4.5, "V": 4.2, "L": 3.8, "F": 2.8, "C": 2.5,
    "M": 1.9, "A": 1.8, "G": -0.4, "T": -0.7, "S": -0.8,
    "W": -0.9, "Y": -1.3, "P": -1.6, "H": -3.2, "E": -3.5,
    "Q": -3.5, "D": -3.5, "N": -3.5, "K": -3.9, "R": -4.5,
}

# Henderson-Hasselbalch pKa values used by _net_charge
_PKA_TABLE = {
    "D": (3.9, -1), "E": (4.1, -1),
    "H": (6.0, +1), "C": (8.3, -1),
    "Y": (10.1, -1), "K": (10.5, +1), "R": (12.5, +1),
}
_NTERM_PKA = 8.0
_CTERM_PKA = 3.1

# BLOSUM62 loaded once; used for mean diagonal self-score
_BLOSUM62 = substitution_matrices.load("BLOSUM62")

# Motifs associated with synthesis or in-solution liability
_LIABILITY_MOTIFS = ("NG", "DP", "NP", "PP", "CP")

# Boman (2003) free-energy-of-transfer scale (kcal/mol).
# Mean over a peptide gives the Boman index; > 1.0 suggests protein-binding potential.
_BOMAN_SCALE = {
    "A": 2.1, "C": -1.0, "D": 3.0, "E": 3.0, "F": -3.7,
    "G": 5.9, "H": -3.0, "I": -2.1, "K": 5.7, "L": -1.9,
    "M": -1.0, "N": 7.0, "P": 0.0,  "Q": 6.0, "R": -0.8,
    "S": 4.1,  "T": 4.1,  "V": -1.0, "W": -1.3, "Y": 0.7,
}

# Eisenberg (1984) consensus normalized hydrophobicity scale; used for hydrophobic moment.
_EISENBERG_SCALE = {
    "A": 0.25,  "R": -1.76, "N": -0.64, "D": -0.72, "C": 0.04,
    "Q": -0.69, "E": -0.62, "G": 0.16,  "H": -0.40, "I": 0.73,
    "L": 0.53,  "K": -1.10, "M": 0.26,  "F": 0.61,  "P": -0.07,
    "S": -0.26, "T": -0.18, "W": 0.37,  "Y": 0.02,  "V": 0.54,
}


# ── Helper functions ──────────────────────────────────────────────────────────

def _net_charge(seq: str, pH: float = 7.4) -> float:
    charge = 1.0 / (1.0 + 10 ** (pH - _NTERM_PKA))   # N-terminus
    charge -= 1.0 / (1.0 + 10 ** (_CTERM_PKA - pH))   # C-terminus
    for aa in seq:
        if aa in _PKA_TABLE:
            pka, sign = _PKA_TABLE[aa]
            if sign == +1:
                charge += 1.0 / (1.0 + 10 ** (pH - pka))
            else:
                charge -= 1.0 / (1.0 + 10 ** (pka - pH))
    return charge


def _sequence_entropy(seq: str) -> float:
    """Shannon entropy of amino acid composition (bits). Max ≈ 4.32 for 20 AAs."""
    counts = Counter(seq)
    n = len(seq)
    if n == 0:
        return 0.0
    return -sum((c / n) * math.log2(c / n) for c in counts.values())


def _low_complexity_fraction(seq: str, window: int = 10, entropy_cutoff: float = 1.5) -> float:
    """Fraction of residues that fall within at least one low-entropy window."""
    n = len(seq)
    if n < window:
        return 1.0 if _sequence_entropy(seq) < entropy_cutoff else 0.0
    lc_mask = [False] * n
    for start in range(n - window + 1):
        if _sequence_entropy(seq[start : start + window]) < entropy_cutoff:
            for i in range(start, start + window):
                lc_mask[i] = True
    return sum(lc_mask) / n


def _max_window_hydrophobicity(seq: str, window: int = 5) -> float:
    """Maximum mean Kyte-Doolittle score over any sliding window of `window` residues."""
    vals = [_KD_SCALE.get(aa, 0.0) for aa in seq]
    if len(vals) < window:
        return sum(vals) / len(vals) if vals else 0.0
    return max(sum(vals[i : i + window]) / window for i in range(len(vals) - window + 1))


def _longest_homopolymer_run(seq: str) -> int:
    """Length of the longest run of identical consecutive residues."""
    if not seq:
        return 0
    max_run = current_run = 1
    for i in range(1, len(seq)):
        if seq[i] == seq[i - 1]:
            current_run += 1
            max_run = max(max_run, current_run)
        else:
            current_run = 1
    return max_run


def _blosum_mean_self_score(seq: str) -> float:
    """Mean BLOSUM62 diagonal score. High = conserved residue types (e.g. Cys=9, Trp=11)."""
    scores = []
    for aa in seq:
        try:
            scores.append(_BLOSUM62[aa, aa])
        except (KeyError, IndexError):
            pass
    return sum(scores) / len(scores) if scores else 0.0


def _boman_index(seq: str) -> float:
    """Mean Boman free-energy-of-transfer (kcal/mol). > 1.0 = protein-binding potential."""
    vals = [_BOMAN_SCALE.get(aa, 0.0) for aa in seq]
    return sum(vals) / len(vals) if vals else 0.0


def _max_hydrophobic_moment(seq: str, window: int = 18, angle_deg: float = 100.0) -> float:
    """Maximum Eisenberg hydrophobic moment over sliding windows (α-helix δ=100°).

    μH = sqrt[(ΣH·sin(i·δ))² + (ΣH·cos(i·δ))²] / window_size
    Computed over all windows of `window` residues; returns the maximum.
    High μH (> ~0.4) indicates an amphiphilic helix.
    """
    vals = [_EISENBERG_SCALE.get(aa, 0.0) for aa in seq]
    n = len(vals)
    if n == 0:
        return 0.0
    w = min(window, n)
    delta = math.radians(angle_deg)
    max_mu = 0.0
    for start in range(n - w + 1):
        chunk = vals[start : start + w]
        sin_sum = sum(h * math.sin(i * delta) for i, h in enumerate(chunk))
        cos_sum = sum(h * math.cos(i * delta) for i, h in enumerate(chunk))
        mu = math.sqrt(sin_sum ** 2 + cos_sum ** 2) / w
        if mu > max_mu:
            max_mu = mu
    return max_mu


# ── Dataclasses ───────────────────────────────────────────────────────────────

@dataclass
class SequenceFeatures:
    sequence: str
    length: int
    molecular_weight: float
    pI: float
    gravy: float                        # mean Kyte-Doolittle hydrophobicity (GRAVY)
    instability_index: float            # Biopython heuristic; >40 suggests in-vitro instability
    net_charge: float                   # Henderson-Hasselbalch estimate at pH 7.4
    charge_asymmetry: float             # |pos_frac - neg_frac|, pos = K/R/H, neg = D/E
    aromatic_fraction: float            # fraction of F/W/Y residues
    max_window_hydrophobicity: float    # max mean KD score over any 5-residue window
    sequence_entropy: float             # Shannon entropy of AA composition (bits; max ≈ 4.32)
    low_complexity_fraction: float      # fraction of residues in entropy < 1.5 windows
    longest_homopolymer_run: int        # longest run of identical consecutive residues
    contains_liability_motif: bool      # True if any of NG, DP, NP, PP, CP present
    camsollike_score: float             # solubility heuristic: gravy - 0.5*charge_asym + 2*aromatic
    blosum_mean_self_score: float       # mean BLOSUM62 diagonal score
    boman_index: float                  # mean Boman ΔGsolv (kcal/mol); > 1.0 = protein-binding potential
    aliphatic_index: float             # 100*(X_A + 2.9*X_V + 3.9*(X_I+X_L)); thermal stability proxy
    hydrophobic_moment: float          # max Eisenberg μH over 18-res windows (α-helix δ=100°); amphiphilicity
    cysteine_positions: list[int] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)


@dataclass
class StructureFeatures:
    pdb_path: Path
    sasa_total: float
    sasa_per_chain: dict[str, float] = field(default_factory=dict)
    interface_residues: list[tuple[str, int]] = field(default_factory=list)
    buried_surface_area: float | None = None
    disulfide_pairs: list[tuple[str, int, str, int]] = field(default_factory=list)


def analyze_sequence(seq: str) -> SequenceFeatures:
    """Compute sequence properties and flag notable patterns.

    Flags applied (see preflight/heuristics.md):
    - length < 5, < 20, > 40, > 2000
    - cysteine pairs within 8 residues
    - charged windows (>50% DEKR in 10 residues)
    - hydrophobic windows (>70% ACFILMVWY in 10 residues)
    - proline-rich / PPII stretches
    - extreme net charge (|charge| > 6 at pH 7.4)
    - low-complexity sequence (Shannon entropy < 2.2 bits; only for n >= 10)
    - high low-complexity fraction (> 50% residues in low-entropy windows)
    - homopolymer runs of 3+
    - synthesis/stability liability motifs (NG, DP, NP, PP, CP)

    Note: instability_index is computed and stored but not flagged — it commonly
    exceeds 40 for globular proteins and is better used as a filter threshold
    in peptide_filters.apply_filters than as a pre-flight warning.
    """
    seq = seq.upper().strip()
    pa = ProteinAnalysis(seq)

    flags: list[str] = []
    n = len(seq)

    # --- Length flags ---
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
            break

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
    gravy = pa.gravy()
    instability = pa.instability_index()
    aa_counts = pa.count_amino_acids()
    aromatic_fraction = sum(aa_counts.get(aa, 0) for aa in "FWY") / n

    # --- Additional scalar metrics ---
    nc = _net_charge(seq)
    pos_charged = sum(1 for aa in seq if aa in "KRH")
    neg_charged = sum(1 for aa in seq if aa in "DE")
    charge_asym = abs(pos_charged / n - neg_charged / n)
    max_win_h = _max_window_hydrophobicity(seq)
    entropy = _sequence_entropy(seq)
    lc_frac = _low_complexity_fraction(seq)
    homopolymer = _longest_homopolymer_run(seq)
    has_liability = any(m in seq for m in _LIABILITY_MOTIFS)
    camsollike = gravy - 0.5 * charge_asym + 2.0 * aromatic_fraction
    blosum_self = _blosum_mean_self_score(seq)
    boman_idx = _boman_index(seq)
    xa = aa_counts.get("A", 0) / n
    xv = aa_counts.get("V", 0) / n
    xi = aa_counts.get("I", 0) / n
    xl = aa_counts.get("L", 0) / n
    aliphatic_idx = 100.0 * (xa + 2.9 * xv + 3.9 * (xi + xl))
    hydrophobic_m = _max_hydrophobic_moment(seq)

    # --- Flags for additional metrics ---
    if abs(nc) > 6:
        flags.append(
            f"net charge {nc:+.1f} at pH 7.4: extreme charge may drive non-specific"
            " electrostatic binding"
        )
    # Entropy flag only meaningful once sequence is long enough to allow diversity
    if n >= 10 and entropy < 2.2:
        flags.append(
            f"low-complexity sequence (Shannon entropy {entropy:.2f} bits < 2.2 threshold):"
            " dominated by few residue types — AF2 confidence may be unreliable"
        )
    if lc_frac > 0.5:
        flags.append(
            f"low-complexity fraction {lc_frac:.2f}: >50% of residues in low-entropy"
            " windows — possible compositional artifact from design tool"
        )
    if homopolymer >= 5:
        flags.append(
            f"homopolymer run of {homopolymer}: unusual in designed peptides,"
            " possible design artifact"
        )
    if has_liability:
        found = [m for m in _LIABILITY_MOTIFS if m in seq]
        flags.append(
            f"liability motifs {found}: synthesis or in-solution stability risk"
            " (NG=deamidation, DP=Asp-Pro cleavage, NP=slow folding, PP/CP=proline)"
        )
    if instability > 40 and gravy > 1.0:
        flags.append(
            f"instability index {instability:.1f} > 40 with GRAVY {gravy:.2f} > 1.0:"
            " hydrophobic + unstable combination — aggregation risk"
        )

    return SequenceFeatures(
        sequence=seq,
        length=n,
        molecular_weight=round(mw, 2),
        pI=round(pI, 2),
        gravy=round(gravy, 4),
        instability_index=round(instability, 2),
        net_charge=round(nc, 2),
        charge_asymmetry=round(charge_asym, 4),
        aromatic_fraction=round(aromatic_fraction, 4),
        max_window_hydrophobicity=round(max_win_h, 4),
        sequence_entropy=round(entropy, 4),
        low_complexity_fraction=round(lc_frac, 4),
        longest_homopolymer_run=homopolymer,
        contains_liability_motif=has_liability,
        camsollike_score=round(camsollike, 4),
        blosum_mean_self_score=round(blosum_self, 4),
        boman_index=round(boman_idx, 4),
        aliphatic_index=round(aliphatic_idx, 2),
        hydrophobic_moment=round(hydrophobic_m, 4),
        cysteine_positions=cys_positions,
        flags=flags,
    )


# ── Batch screening ───────────────────────────────────────────────────────────

DEFAULT_SCREEN_THRESHOLDS: dict[str, tuple] = {
    # field_name: (threshold, direction)
    # "max"    — fail if value > threshold
    # "min"    — fail if value < threshold
    # "absmax" — fail if abs(value) > threshold (used for signed net_charge)
    "sequence_entropy":          (2.2,  "min"),
    "net_charge":                (8.0,  "absmax"),
    "longest_homopolymer_run":   (2,    "max"),
    "charge_asymmetry":          (0.5,  "max"),
    "max_window_hydrophobicity": (2.5,  "max"),
    "aromatic_fraction":         (0.35, "max"),
    "low_complexity_fraction":   (0.5,  "max"),
    "contains_liability_motif":  (0,    "max"),  # bool: True → 1 > 0 → fail
}


def screen_sequences(
    sequences: list[str],
    thresholds: dict | None = None,
    output_dir: str | Path | None = None,
) -> tuple[list[SequenceFeatures], list]:
    """Run analyze_sequence on a library and produce diagnostic visualizations.

    Each sequence is analyzed, then tested against DEFAULT_SCREEN_THRESHOLDS
    (or a caller-supplied override). A sequence passes if it meets every
    threshold.

    Parameters
    ----------
    sequences : list[str]
        Peptide sequences in single-letter AA code.
    thresholds : dict, optional
        Override DEFAULT_SCREEN_THRESHOLDS. Keys must be SequenceFeatures
        field names; values are (threshold, direction) tuples where direction
        is "max", "min", or "absmax".
    output_dir : str or Path, optional
        If given, saves three figures: screen_summary.png,
        metric_distributions.png, charge_vs_hydrophobicity.png.

    Returns
    -------
    passing : list[SequenceFeatures]
        Features for sequences passing all thresholds, in input order.
    figures : list[matplotlib.figure.Figure]
        [0] Per-threshold pass/fail bar chart
        [1] 4×2 metric distribution grid (passing vs failing)
        [2] |Net charge| vs max-window hydrophobicity scatter
    """
    import numpy as np
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    if thresholds is None:
        thresholds = DEFAULT_SCREEN_THRESHOLDS

    all_feats = [analyze_sequence(seq) for seq in sequences]
    n_total = len(all_feats)

    def _passes(feats: SequenceFeatures, field: str, cutoff, direction: str) -> bool:
        val = getattr(feats, field)
        if direction == "max":    return float(val) <= cutoff
        if direction == "min":    return float(val) >= cutoff
        if direction == "absmax": return abs(float(val)) <= cutoff
        return True

    threshold_pass: dict[str, list[bool]] = {
        field: [_passes(f, field, cutoff, direction) for f in all_feats]
        for field, (cutoff, direction) in thresholds.items()
    }
    pass_all = [
        all(threshold_pass[field][i] for field in thresholds)
        for i in range(n_total)
    ]
    passing = [f for f, p in zip(all_feats, pass_all) if p]
    n_pass = sum(pass_all)

    C_PASS = "#4caf50"
    C_FAIL = "#ef5350"

    def _vals(field: str) -> list[float]:
        return [
            abs(float(getattr(f, field))) if field == "net_charge"
            else float(getattr(f, field))
            for f in all_feats
        ]

    def _split(vals: list[float]) -> tuple[list[float], list[float]]:
        return (
            [v for v, p in zip(vals, pass_all) if p],
            [v for v, p in zip(vals, pass_all) if not p],
        )

    # ── Figure 1: Per-threshold summary ───────────────────────────────────────
    fail_counts  = {field: sum(1 for p in passes if not p)
                    for field, passes in threshold_pass.items()}
    sorted_items = sorted(fail_counts.items(), key=lambda x: x[1], reverse=True)
    bar_labels   = [f for f, _ in sorted_items]
    bar_fails    = [v for _, v in sorted_items]
    bar_passes   = [n_total - v for v in bar_fails]

    fig1, ax1 = plt.subplots(figsize=(8, max(4, len(bar_labels) * 0.45 + 1.5)))
    y = list(range(len(bar_labels)))
    ax1.barh(y, bar_passes, color=C_PASS, alpha=0.85, label="Pass")
    ax1.barh(y, bar_fails, left=bar_passes, color=C_FAIL, alpha=0.85, label="Fail")
    ax1.set_yticks(y)
    ax1.set_yticklabels(bar_labels, fontsize=9)
    ax1.set_xlabel("Sequences", fontsize=10)
    ax1.set_title(
        f"Per-threshold results  —  {n_pass}/{n_total} pass all "
        f"({100 * n_pass / n_total:.1f}%)",
        fontsize=11, fontweight="bold", pad=10,
    )
    ax1.set_xlim(0, n_total * 1.12)
    ax1.legend(loc="lower right", fontsize=9)
    for i, (p, f) in enumerate(zip(bar_passes, bar_fails)):
        if f > 0:
            ax1.text(p + f + n_total * 0.01, i, str(f),
                     va="center", fontsize=8, color="#c62828")
    ax1.spines[["top", "right"]].set_visible(False)
    fig1.tight_layout()

    # ── Figure 2: Metric distributions ────────────────────────────────────────
    DIST_METRICS = [
        ("sequence_entropy",          "Sequence entropy (bits)"),
        ("net_charge",                "|Net charge| pH 7.4"),
        ("charge_asymmetry",          "Charge asymmetry"),
        ("gravy",                     "GRAVY (mean KD)"),
        ("max_window_hydrophobicity", "Max window hydrophobicity"),
        ("aromatic_fraction",         "Aromatic fraction"),
        ("blosum_mean_self_score",    "BLOSUM62 self-score"),
        ("instability_index",         "Instability index"),
    ]
    fig2, axes = plt.subplots(2, 4, figsize=(14, 7))
    axes = axes.flatten()

    for idx, (field, label) in enumerate(DIST_METRICS):
        ax = axes[idx]
        all_v = _vals(field)
        v_pass, v_fail = _split(all_v)
        lo, hi = min(all_v), max(all_v)
        if lo >= hi:
            lo, hi = lo - 0.5, hi + 0.5
        bins = np.linspace(lo, hi, 22)

        if v_fail:
            ax.hist(v_fail, bins=bins, color=C_FAIL, alpha=0.60, label="Fail")
        if v_pass:
            ax.hist(v_pass, bins=bins, color=C_PASS, alpha=0.60, label="Pass")

        if field in thresholds:
            cutoff, _ = thresholds[field]
            ax.axvline(cutoff, color="navy", linestyle="--", linewidth=1.2,
                       label=f"cutoff = {cutoff}")

        ax.set_xlabel(label, fontsize=8)
        ax.set_ylabel("Count", fontsize=8)
        ax.tick_params(labelsize=7)
        ax.spines[["top", "right"]].set_visible(False)
        if idx == 0:
            ax.legend(fontsize=7, loc="upper right")

    fig2.suptitle(
        "Metric distributions  ·  green = pass all, red = fail ≥ 1  ·  dashed = threshold",
        fontsize=10, fontweight="bold",
    )
    fig2.tight_layout()

    # ── Figure 3: |Charge| × max-window hydrophobicity scatter ────────────────
    nc_vals  = _vals("net_charge")
    mwh_vals = _vals("max_window_hydrophobicity")
    nc_pass,  nc_fail  = _split(nc_vals)
    mwh_pass, mwh_fail = _split(mwh_vals)

    fig3, ax3 = plt.subplots(figsize=(6, 5))
    if nc_fail:
        ax3.scatter(nc_fail, mwh_fail, c=C_FAIL, alpha=0.65, s=22,
                    label=f"Fail  (n={len(nc_fail)})", edgecolors="none")
    if nc_pass:
        ax3.scatter(nc_pass, mwh_pass, c=C_PASS, alpha=0.65, s=22,
                    label=f"Pass  (n={len(nc_pass)})", edgecolors="none")
    if "net_charge" in thresholds:
        ax3.axvline(thresholds["net_charge"][0], color="navy",
                    linestyle="--", linewidth=1.0, alpha=0.7)
    if "max_window_hydrophobicity" in thresholds:
        ax3.axhline(thresholds["max_window_hydrophobicity"][0], color="navy",
                    linestyle="--", linewidth=1.0, alpha=0.7)
    ax3.set_xlabel("|Net charge| at pH 7.4", fontsize=10)
    ax3.set_ylabel("Max window hydrophobicity (KD)", fontsize=10)
    ax3.set_title("Charge vs max-window hydrophobicity", fontsize=11, fontweight="bold")
    ax3.legend(fontsize=9)
    ax3.spines[["top", "right"]].set_visible(False)
    fig3.tight_layout()

    figures = [fig1, fig2, fig3]

    if output_dir is not None:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)
        fig1.savefig(out / "screen_summary.png",           dpi=150, bbox_inches="tight")
        fig2.savefig(out / "metric_distributions.png",     dpi=150, bbox_inches="tight")
        fig3.savefig(out / "charge_vs_hydrophobicity.png", dpi=150, bbox_inches="tight")

    return passing, figures


def analyze_structure(pdb_path: Path, interface_cutoff_a: float = 5.0) -> StructureFeatures:
    """Compute SASA, interface residues, buried surface area, and disulfide pairs from a PDB.

    Uses FreeSASA for solvent-accessible surface area and Biopython for interface
    residue detection (all-atom, configurable cutoff). BSA is computed for two-chain
    complexes only: BSA = SASA(chain_A_isolated) + SASA(chain_B_isolated) - SASA(complex).
    """
    import freesasa
    import numpy as np
    from Bio.PDB import PDBParser

    pdb_path = Path(pdb_path)

    # SASA of the full complex
    fs_structure = freesasa.Structure(str(pdb_path))
    result = freesasa.calc(fs_structure)
    total_sasa = result.totalArea()
    residue_areas = result.residueAreas()
    sasa_per_chain = {
        chain_id: round(sum(r.total for r in residues.values()), 2)
        for chain_id, residues in residue_areas.items()
    }

    # Interface residues: any residue with an atom within cutoff of any atom on another chain
    parser = PDBParser(QUIET=True)
    bp_structure = parser.get_structure("complex", str(pdb_path))
    model = next(bp_structure.get_models())
    chains = list(model.get_chains())
    chain_ids = [c.id for c in chains]

    chain_atoms: dict[str, np.ndarray] = {}
    for chain in chains:
        coords = [
            atom.get_vector().get_array()
            for res in chain.get_residues()
            if res.id[0] == " "
            for atom in res.get_atoms()
        ]
        chain_atoms[chain.id] = (
            np.array(coords, dtype=np.float32) if coords else np.empty((0, 3), dtype=np.float32)
        )

    interface_residues: list[tuple[str, int]] = []
    for chain in chains:
        other_ids = [cid for cid in chain_ids if cid != chain.id]
        if not other_ids:
            continue
        other_atoms = np.concatenate([chain_atoms[cid] for cid in other_ids], axis=0)
        if len(other_atoms) == 0:
            continue
        for res in chain.get_residues():
            if res.id[0] != " ":
                continue
            res_coords = np.array(
                [a.get_vector().get_array() for a in res.get_atoms()], dtype=np.float32
            )
            if len(res_coords) == 0:
                continue
            diffs = res_coords[:, None, :] - other_atoms[None, :, :]  # (M, N, 3)
            if np.sqrt((diffs**2).sum(axis=-1)).min() <= interface_cutoff_a:
                interface_residues.append((chain.id, res.id[1]))

    # BSA: only defined for two-chain complexes
    buried_surface_area: float | None = None
    if len(chain_ids) == 2:
        isolated = freesasa.structureArray(str(pdb_path), {"separate-chains": True})
        isolated_sum = sum(freesasa.calc(s).totalArea() for s in isolated)
        buried_surface_area = round(isolated_sum - total_sasa, 2)

    return StructureFeatures(
        pdb_path=pdb_path,
        sasa_total=round(total_sasa, 2),
        sasa_per_chain=sasa_per_chain,
        interface_residues=interface_residues,
        buried_surface_area=buried_surface_area,
        disulfide_pairs=detect_disulfides(pdb_path),
    )


def detect_disulfides(
    pdb_path: Path, distance_cutoff_a: float = 3.0
) -> list[tuple[str, int, str, int]]:
    """Return (chain_i, res_i, chain_j, res_j) for cysteine SG atoms within distance_cutoff_a.

    Canonical SG-SG bond length is ~2.05 Å. The default 3.0 Å cutoff is permissive
    to catch near-contacts in unrelaxed ColabFold structures that haven't been
    energy-minimized. Chain IDs are included so intra- and inter-chain pairs are
    unambiguous in multi-chain models.
    """
    from Bio.PDB import PDBParser

    parser = PDBParser(QUIET=True)
    structure = parser.get_structure("pdb", str(pdb_path))
    model = next(structure.get_models())

    cys_sg = [
        (chain.id, res.id[1], res["SG"])
        for chain in model.get_chains()
        for res in chain.get_residues()
        if res.get_resname() == "CYS" and "SG" in res
    ]

    pairs: list[tuple[str, int, str, int]] = []
    for i in range(len(cys_sg)):
        for j in range(i + 1, len(cys_sg)):
            chain_i, res_i, sg_i = cys_sg[i]
            chain_j, res_j, sg_j = cys_sg[j]
            if sg_i - sg_j <= distance_cutoff_a:
                pairs.append((chain_i, res_i, chain_j, res_j))
    return pairs


@dataclass
class CIDERFeatures:
    """Sequence-level IDR biophysics from localCIDER (Pappu lab).

    All global descriptors are computed via localcider.sequenceParameters.SequenceParameters.
    Reference: Holehouse et al., Biophys. J. 2017 (localCIDER).

    kappa: charge patterning parameter [0, 1]. 0 = charges perfectly interspersed;
        1 = charges fully segregated (poly-acid block + poly-base block). Higher kappa
        correlates with more compact disordered ensembles.
    omega: charge + aromatic patterning (Pappu/Brangwynne SLiME analysis).
    scd: sequence charge decoration (Sawle-Ghosh). Negative = acidic-dominant patterning.
    delta / delta_max: fractional delta = delta / delta_max; same as kappa for charge blocks.
    mean_hydropathy: Uversky normalized hydropathy [0, 1] (not Kyte-Doolittle).
        < ~0.4 is strongly associated with disorder; Uversky boundary = 0.413.
    ppii_propensity: PPII helix propensity (values > 0.5 suggest extended PPII tendencies).
    phase_region: integer 1–5 corresponding to the Uversky phase diagram quadrant.
        Region 1 = strongly disordered (low |NCPR|, low hydropathy).
    """
    sequence: str
    fcr: float            # fraction of charged residues (DE + KR + H / total)
    ncpr: float           # net charge per residue (pos − neg; sign-bearing)
    kappa: float          # charge patterning [0=mixed, 1=segregated]
    omega: float          # charge-aromatic patterning
    scd: float            # sequence charge decoration (Sawle-Ghosh)
    delta: float          # charge asymmetry parameter
    mean_hydropathy: float  # Uversky normalized hydropathy [0, 1]
    ppii_propensity: float  # PPII helix propensity
    isoelectric_point: float
    phase_region: int     # Uversky phase diagram region (1–5)


def analyze_cider(seq: str) -> CIDERFeatures:
    """Compute IDR biophysical descriptors via localCIDER.

    Returns global scalar descriptors only (no linear profiles).
    Available in the proteinviz conda env — no subprocess bridge needed.
    """
    from localcider.sequenceParameters import SequenceParameters

    seq = seq.upper().strip()
    sp = SequenceParameters(seq)
    return CIDERFeatures(
        sequence=seq,
        fcr=round(float(sp.get_FCR()), 4),
        ncpr=round(float(sp.get_NCPR()), 4),
        kappa=round(float(sp.get_kappa()), 4),
        omega=round(float(sp.get_Omega()), 4),
        scd=round(float(sp.get_SCD()), 4),
        delta=round(float(sp.get_delta()), 6),
        mean_hydropathy=round(float(sp.get_mean_hydropathy()), 4),
        ppii_propensity=round(float(sp.get_PPII_propensity()), 4),
        isoelectric_point=round(float(sp.get_isoelectric_point()), 2),
        phase_region=int(sp.get_phasePlotRegion()),
    )


def query_aiupred(seq: str) -> list[float]:
    """Per-residue disorder propensity [0, 1] via the local AIUPred package.

    Uses AIUPred's single disorder model (no long/short distinction as in IUPred3).
    First call is slow due to PyTorch model loading (~5-10 s); subsequent calls
    within the same process reuse the loaded model if you pass the predictor object.
    Returns a list of floats aligned to the input sequence positions.
    """
    from aiupred import AIUPred

    seq = seq.upper().strip()
    predictor = AIUPred()
    scores = predictor.predict_disorder(seq)
    return [float(s) for s in scores]


# Legacy name kept so the tools.yaml registry entry still resolves.
query_iupred3 = query_aiupred
