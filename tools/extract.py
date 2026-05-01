"""Parse prediction outputs into structured metrics.

Reads ColabFold / ESMFold output directories and returns a uniform
ExtractedMetrics record regardless of backend.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class ExtractedMetrics:
    model_id: str
    backend: str
    pdb_path: Path
    mean_plddt: float
    min_plddt: float
    max_plddt: float
    per_residue_plddt: list[float] = field(default_factory=list)
    pae: list[list[float]] | None = None
    iptm: float | None = None
    actifptm: float | None = None
    ranking_score: float | None = None


def extract_colabfold(out_dir: Path) -> list[ExtractedMetrics]:
    """Parse a ColabFold output directory. One ExtractedMetrics per ranked model.

    Expects files matching:
        *_scores_rank_NNN_*.json  — per-residue pLDDT, PAE matrix, ptm
        *_unrelaxed_rank_NNN_*.pdb
    """
    results = []
    score_files = sorted(out_dir.glob("*_scores_rank_*_*.json"))
    if not score_files:
        raise FileNotFoundError(f"No ColabFold score JSON files found in {out_dir}")

    for score_path in score_files:
        # Derive rank and model id from filename
        # e.g. smoke_scores_rank_001_alphafold2_ptm_model_3_seed_000.json
        stem = score_path.stem  # strip .json
        rank_idx = stem.find("_rank_")
        model_id = stem[rank_idx + 1:] if rank_idx >= 0 else stem

        # Find matching PDB (unrelaxed preferred; relaxed accepted)
        pdb_glob = stem.replace("_scores_", "_unrelaxed_") + ".pdb"
        pdb_candidates = list(out_dir.glob(pdb_glob))
        if not pdb_candidates:
            pdb_candidates = list(out_dir.glob(stem.replace("_scores_", "_relaxed_") + ".pdb"))
        pdb_path = pdb_candidates[0] if pdb_candidates else out_dir / (stem + ".pdb")

        data = json.loads(score_path.read_text())
        plddt: list[float] = data.get("plddt", [])
        pae: list[list[float]] | None = data.get("pae")

        results.append(ExtractedMetrics(
            model_id=model_id,
            backend="colabfold",
            pdb_path=pdb_path,
            mean_plddt=sum(plddt) / len(plddt) if plddt else 0.0,
            min_plddt=min(plddt) if plddt else 0.0,
            max_plddt=max(plddt) if plddt else 0.0,
            per_residue_plddt=plddt,
            pae=pae,
            iptm=data.get("iptm"),
            actifptm=data.get("actifptm"),
            ranking_score=data.get("ptm"),
        ))

    return results


def extract_esmfold(out_dir: Path) -> list[ExtractedMetrics]:
    """Parse ESMFold API output (a single PDB with per-residue pLDDT in B-factor)."""
    raise NotImplementedError


def extract(out_dir: Path, backend: str) -> list[ExtractedMetrics]:
    if backend == "colabfold":
        return extract_colabfold(out_dir)
    if backend == "esmfold_api":
        return extract_esmfold(out_dir)
    raise ValueError(f"unknown backend: {backend}")


DEFAULT_SCORE_WEIGHTS: dict[str, float] = {
    "primary": 0.5,
    "mean_plddt": 0.3,
    "interface_plddt": 0.2,
}


def composite_score(
    m: ExtractedMetrics,
    interface_plddt: float | None = None,
    weights: dict[str, float] = DEFAULT_SCORE_WEIGHTS,
) -> float:
    """Composite ranking score for binder triage.

    Placeholder weighting; tune on calibration set before trusting rankings.
        score = weights["primary"] * actifpTM (or ipTM)
              + weights["mean_plddt"] * (mean_plddt / 100)
              + weights["interface_plddt"] * (interface_plddt / 100) if provided
    """
    w = weights
    primary = m.actifptm if m.actifptm is not None else (m.iptm or 0.0)
    score = w["primary"] * primary + w["mean_plddt"] * (m.mean_plddt / 100.0)
    if interface_plddt is not None:
        score += w["interface_plddt"] * (interface_plddt / 100.0)
    return score


def to_json(metrics: list[ExtractedMetrics], path: Path) -> None:
    payload = [
        {k: (str(v) if isinstance(v, Path) else v) for k, v in m.__dict__.items()}
        for m in metrics
    ]
    path.write_text(json.dumps(payload, indent=2))
