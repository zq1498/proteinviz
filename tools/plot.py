"""Visualization of fold metrics: pLDDT profile, PAE heatmap, model comparison bar chart."""

from __future__ import annotations

from pathlib import Path

import matplotlib
matplotlib.use("Agg")  # headless — no display required
import matplotlib.pyplot as plt
import matplotlib.colors as mcolors
import numpy as np

from tools.extract import ExtractedMetrics, extract_colabfold


# AlphaFold confidence colour tiers (matches RCSB / UniProt colouring)
_PLDDT_TIERS = [
    (90, 100, "#0053D6", "Very high (>90)"),
    (70,  90, "#65CBF3", "Confident (70-90)"),
    (50,  70, "#FFDB13", "Low (50-70)"),
    ( 0,  50, "#FF7D45", "Very low (<50)"),
]


def plot_plddt_profile(metrics: ExtractedMetrics, out_path: Path) -> Path:
    """Per-residue pLDDT coloured by confidence tier, saved as PNG."""
    plddt = np.array(metrics.per_residue_plddt)
    residues = np.arange(1, len(plddt) + 1)

    fig, ax = plt.subplots(figsize=(12, 3.5))
    ax.plot(residues, plddt, color="#888", linewidth=0.6, zorder=1)

    for lo, hi, color, _ in _PLDDT_TIERS:
        mask = (plddt >= lo) & (plddt < hi)
        ax.fill_between(residues, plddt, where=mask, color=color, alpha=0.6, zorder=2)

    # Tier legend
    handles = [
        plt.Rectangle((0, 0), 1, 1, color=c, alpha=0.6, label=lbl)
        for _, _, c, lbl in _PLDDT_TIERS
    ]
    ax.legend(handles=handles, loc="lower right", fontsize=8, framealpha=0.8)

    ax.axhline(90, color="#0053D6", linewidth=0.5, linestyle="--", alpha=0.4)
    ax.axhline(70, color="#65CBF3", linewidth=0.5, linestyle="--", alpha=0.4)
    ax.axhline(50, color="#FFDB13", linewidth=0.5, linestyle="--", alpha=0.4)

    ax.set_xlim(1, len(plddt))
    ax.set_ylim(0, 100)
    ax.set_xlabel("Residue")
    ax.set_ylabel("pLDDT")
    ax.set_title(f"Per-residue pLDDT — {metrics.model_id}\nmean={metrics.mean_plddt:.1f}")

    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return out_path


def plot_pae_heatmap(metrics: ExtractedMetrics, out_path: Path) -> Path:
    """PAE matrix heatmap. Low PAE = high confidence in relative position."""
    if metrics.pae is None:
        raise ValueError(f"No PAE data in {metrics.model_id}")

    pae = np.array(metrics.pae)
    n = pae.shape[0]

    fig, ax = plt.subplots(figsize=(6, 5))
    im = ax.imshow(pae, cmap="Greens_r", vmin=0, vmax=30, origin="upper", aspect="auto")
    plt.colorbar(im, ax=ax, label="PAE (Angstrom)")

    ax.set_xlabel("Scored residue")
    ax.set_ylabel("Aligned residue")
    ax.set_title(f"PAE — {metrics.model_id}")

    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return out_path


def plot_model_comparison(all_metrics: list[ExtractedMetrics], out_path: Path,
                           tmscore_vs_crystal: float | None = None) -> Path:
    """Bar chart comparing pLDDT and pTM across all ranked models."""
    labels = [f"rank_{i+1}" for i in range(len(all_metrics))]
    plddt_vals = [m.mean_plddt for m in all_metrics]
    ptm_vals   = [m.ranking_score or 0.0 for m in all_metrics]

    x = np.arange(len(labels))
    width = 0.35

    fig, ax1 = plt.subplots(figsize=(9, 4))
    ax2 = ax1.twinx()

    bars1 = ax1.bar(x - width/2, plddt_vals, width, color="#65CBF3", label="mean pLDDT")
    bars2 = ax2.bar(x + width/2, ptm_vals,   width, color="#0053D6", alpha=0.7, label="pTM")

    ax1.set_ylim(0, 100)
    ax2.set_ylim(0, 1)
    ax1.set_ylabel("mean pLDDT", color="#65CBF3")
    ax2.set_ylabel("pTM", color="#0053D6")
    ax1.set_xticks(x)
    ax1.set_xticklabels(labels, rotation=20, ha="right", fontsize=8)
    ax1.set_title("Model comparison — all 5 ranked predictions")

    lines = [bars1, bars2]
    leg_labels = ["mean pLDDT", "pTM"]
    if tmscore_vs_crystal is not None:
        ax2.axhline(tmscore_vs_crystal, color="red", linestyle="--", linewidth=1.2,
                    label=f"TM-score vs crystal ({tmscore_vs_crystal:.3f})")
        leg_labels.append(f"TM vs crystal ({tmscore_vs_crystal:.3f})")
        lines.append(ax2.axhline(tmscore_vs_crystal, color="red", linestyle="--", linewidth=1.2))

    fig.legend(leg_labels, loc="upper right", bbox_to_anchor=(0.98, 0.92), fontsize=8)
    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=150)
    plt.close(fig)
    return out_path


def generate_all(job_dir: Path, out_dir: Path,
                 tmscore_vs_crystal: float | None = None) -> list[Path]:
    """Generate pLDDT profile, PAE heatmap, and model comparison for a job directory."""
    all_metrics = extract_colabfold(job_dir)
    best = all_metrics[0]
    saved = []

    saved.append(plot_plddt_profile(best, out_dir / "plddt_profile.png"))
    saved.append(plot_pae_heatmap(best, out_dir / "pae_heatmap.png"))
    saved.append(plot_model_comparison(all_metrics, out_dir / "model_comparison.png",
                                        tmscore_vs_crystal=tmscore_vs_crystal))
    return saved


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--job-dir", type=Path, required=True)
    p.add_argument("--out-dir", type=Path, required=True)
    p.add_argument("--tmscore", type=float, default=None,
                   help="TM-score vs crystal to overlay on model comparison chart")
    args = p.parse_args()

    paths = generate_all(args.job_dir, args.out_dir, tmscore_vs_crystal=args.tmscore)
    for p in paths:
        print(p)
