"""Markdown report generation.

Consumes ExtractedMetrics + SequenceFeatures + StructureFeatures and emits
the report format specified in CLAUDE.md:
  1. Executive summary
  2. Methods
  3. Per-complex results table
  4. Flagged observations
  5. Suggested follow-up
  6. Raw output locations
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from tools.extract import DEFAULT_SCORE_WEIGHTS, ExtractedMetrics, composite_score


@dataclass
class ReportInputs:
    job_id: str
    task_description: str
    metrics: list[ExtractedMetrics] = field(default_factory=list)
    methods: dict[str, str] = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)
    follow_up: list[str] = field(default_factory=list)
    output_paths: dict[str, Path] = field(default_factory=dict)
    weights: dict[str, float] | None = None


def executive_summary(
    ranked: list[ExtractedMetrics],
    flags: list[str],
    weights: dict[str, float] | None = None,
) -> str:
    """One-paragraph interpretation of the top-ranked complex."""
    if not ranked:
        return "No models were provided for analysis."

    kw = {"weights": weights} if weights else {}
    best = ranked[0]
    score = composite_score(best, **kw)

    if score > 0.70:
        confidence = "strong binding candidate"
        caveat = "Proceed with experimental validation."
    elif score > 0.50:
        confidence = "moderate binding candidate"
        caveat = "Interpret with caution; additional metrics or experimental follow-up recommended."
    else:
        confidence = "weak or likely non-binder"
        caveat = "Low confidence across all ranking metrics; treat predictions skeptically."

    ptm_str = f"pTM {best.ranking_score:.3f}" if best.ranking_score is not None else "pTM N/A"
    iptm_str = f", ipTM {best.iptm:.3f}" if best.iptm is not None else ""
    actifptm_str = f", actifpTM {best.actifptm:.3f}" if best.actifptm is not None else ""
    flag_note = (
        f" {len(flags)} sequence flag(s) warrant review (see § Flagged Observations)."
        if flags else ""
    )

    return (
        f"Top-ranked model **{best.model_id}** is a {confidence} with composite score "
        f"{score:.3f} (mean pLDDT {best.mean_plddt:.1f}, {ptm_str}{iptm_str}{actifptm_str}). "
        f"{len(ranked)} model(s) evaluated.{flag_note} {caveat}"
    )


def render_report(inputs: ReportInputs) -> str:
    """Render a markdown report as a single string."""
    kw: dict = {"weights": inputs.weights} if inputs.weights else {}
    ranked = sorted(inputs.metrics, key=lambda m: composite_score(m, **kw), reverse=True)
    used_weights = inputs.weights or DEFAULT_SCORE_WEIGHTS

    lines: list[str] = [
        f"# Prediction Report: {inputs.job_id}",
        f"_Generated {_now_iso()} · task: {inputs.task_description}_",
        "",
        "## 1. Executive Summary",
        "",
        executive_summary(ranked, inputs.flags, inputs.weights),
        f"_Score weights: primary={used_weights['primary']}, "
        f"mean_plddt={used_weights['mean_plddt']}, "
        f"interface_plddt={used_weights['interface_plddt']}_",
        "",
        "## 2. Methods",
        "",
    ]

    if inputs.methods:
        for k, v in inputs.methods.items():
            lines.append(f"- **{k}:** {v}")
    else:
        lines.append("_No methods recorded._")
    lines.append("")

    lines += [
        "## 3. Per-Complex Results",
        "",
        "| Rank | Model | Composite | Mean pLDDT | Min pLDDT | pTM | ipTM | actifpTM |",
        "|------|-------|-----------|------------|-----------|-----|------|----------|",
    ]
    for i, m in enumerate(ranked, 1):
        cs = composite_score(m, **kw)
        ptm = f"{m.ranking_score:.3f}" if m.ranking_score is not None else "—"
        iptm = f"{m.iptm:.3f}" if m.iptm is not None else "—"
        actifptm = f"{m.actifptm:.3f}" if m.actifptm is not None else "—"
        lines.append(
            f"| {i} | {m.model_id} | {cs:.3f} | {m.mean_plddt:.1f} |"
            f" {m.min_plddt:.1f} | {ptm} | {iptm} | {actifptm} |"
        )
    lines.append("")

    lines += ["## 4. Flagged Observations", ""]
    if inputs.flags:
        lines += [f"- {f}" for f in inputs.flags]
    else:
        lines.append("_No flags raised._")
    lines.append("")

    lines += ["## 5. Suggested Follow-Up", ""]
    if inputs.follow_up:
        lines += [f"- {item}" for item in inputs.follow_up]
    else:
        lines.append("_None recorded._")
    lines.append("")

    lines += ["## 6. Raw Output Locations", ""]
    if inputs.output_paths:
        for label, path in inputs.output_paths.items():
            lines.append(f"- **{label}:** `{path}`")
    else:
        lines.append("_No output paths recorded._")
    lines.append("")

    return "\n".join(lines)


def write_report(inputs: ReportInputs, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_report(inputs))
    return out_path


def _now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"
