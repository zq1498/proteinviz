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
from datetime import datetime, timezone
from pathlib import Path

from tools.analyze import SequenceFeatures, StructureFeatures
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
    structure: StructureFeatures | None = None   # from analyze_structure()
    sequence: SequenceFeatures | None = None     # from analyze_sequence()
    disorder: list[float] | None = None          # from query_aiupred()


def executive_summary(
    ranked: list[ExtractedMetrics],
    flags: list[str],
    weights: dict[str, float] | None = None,
) -> str:
    """One-paragraph interpretation of the top-ranked model.

    Language adapts to context: monomer predictions get fold-quality tiers
    (pTM-based); complex predictions get binding-candidate tiers (composite-based).
    """
    if not ranked:
        return "No models were provided for analysis."

    kw: dict = {"weights": weights} if weights else {}
    best = ranked[0]
    score = composite_score(best, **kw)
    is_complex = best.iptm is not None or best.actifptm is not None

    if is_complex:
        if score > 0.70:
            confidence = "strong binding candidate"
            caveat = "Proceed with experimental validation."
        elif score > 0.50:
            confidence = "moderate binding candidate"
            caveat = "Interpret with caution; additional metrics or experimental follow-up recommended."
        else:
            confidence = "weak or likely non-binder"
            caveat = "Low confidence across all ranking metrics; treat predictions skeptically."
    else:
        ptm = best.ranking_score or 0.0
        if ptm > 0.80:
            confidence = "high-confidence fold"
            caveat = "Structure is likely reliable for downstream analysis."
        elif ptm > 0.50:
            confidence = "moderate-confidence fold"
            caveat = "Core structure is probably correct; flexible or disordered regions may be unreliable."
        else:
            confidence = "low-confidence fold"
            caveat = "Significant disorder or structural uncertainty; interpret with care."

    ptm_str = f"pTM {best.ranking_score:.3f}" if best.ranking_score is not None else "pTM N/A"
    iptm_str = f", ipTM {best.iptm:.3f}" if best.iptm is not None else ""

    n_chains = len(best.per_chain_plddt)
    actifptm_demoted = best.actifptm is not None and n_chains >= 3
    if actifptm_demoted:
        actifptm_str = (
            f", actifpTM {best.actifptm:.3f} "
            f"(excluded from composite — {n_chains}-chain complex, see § Flagged Observations)"
        )
    else:
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


def _render_structural_section(inp: ReportInputs) -> list[str]:
    lines: list[str] = ["## 4. Structural Analysis", ""]

    if inp.structure:
        s = inp.structure
        lines.append(f"- Complex SASA: {s.sasa_total:.1f} Å²")
        for chain, sasa in sorted(s.sasa_per_chain.items()):
            lines.append(f"  - Chain {chain}: {sasa:.1f} Å²")
        if s.buried_surface_area is not None:
            interp = "good interface" if s.buried_surface_area > 800 else "small interface"
            lines.append(
                f"- Buried surface area: {s.buried_surface_area:.1f} Å² ({interp})"
            )
        if s.disulfide_pairs:
            pairs = ", ".join(
                f"{c1}{r1}–{c2}{r2}" for c1, r1, c2, r2 in s.disulfide_pairs
            )
            lines.append(f"- Disulfide bonds detected: {pairs}")
        else:
            lines.append("- No disulfide bonds detected.")

    if inp.sequence and inp.sequence.flags:
        lines += ["", f"**Sequence flags:** {'; '.join(inp.sequence.flags)}"]

    if inp.disorder is not None:
        mean_d = sum(inp.disorder) / len(inp.disorder)
        high_frac = sum(1 for d in inp.disorder if d > 0.5) / len(inp.disorder)
        lines += [
            "",
            f"- Mean disorder score: {mean_d:.2f} "
            f"({high_frac * 100:.0f}% of residues above 0.5 threshold)",
        ]

    if not inp.structure and not inp.sequence and inp.disorder is None:
        lines.append("_No structural analysis data available._")

    lines.append("")
    return lines


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
        "| Rank | Model | Composite | Mean pLDDT | Min pLDDT | pTM | ipTM | actifpTM | Per-chain pLDDT |",
        "|------|-------|-----------|------------|-----------|-----|------|----------|-----------------|",
    ]
    for i, m in enumerate(ranked, 1):
        cs = composite_score(m, **kw)
        ptm = f"{m.ranking_score:.3f}" if m.ranking_score is not None else "—"
        iptm = f"{m.iptm:.3f}" if m.iptm is not None else "—"
        actifptm = f"{m.actifptm:.3f}" if m.actifptm is not None else "—"
        chain_plddt = _format_chain_plddt(m.per_chain_plddt)
        lines.append(
            f"| {i} | {m.model_id} | {cs:.3f} | {m.mean_plddt:.1f} |"
            f" {m.min_plddt:.1f} | {ptm} | {iptm} | {actifptm} | {chain_plddt} |"
        )
    lines.append("")

    lines += _render_structural_section(inputs)

    auto_flags = list(inputs.flags)
    for m in ranked:
        if len(m.per_chain_plddt) >= 3 and m.actifptm is not None:
            chain_str = _format_chain_plddt(m.per_chain_plddt)
            min_chain = min(m.per_chain_plddt, key=lambda ch: m.per_chain_plddt[ch])
            auto_flags.append(
                f"**actifpTM unreliable in {len(m.per_chain_plddt)}-chain complex.** "
                f"actifpTM ({m.actifptm:.3f}) aggregates across all inter-chain interfaces. "
                f"In a homodimer + peptide setup the well-structured dimer interface dominates, "
                f"inflating this metric regardless of peptide binding quality. "
                f"actifpTM is excluded from the composite score for this complex. "
                f"Per-chain pLDDT: {chain_str} — chain {min_chain} "
                f"({m.per_chain_plddt[min_chain]:.1f}) is the low-confidence chain. "
                f"Rely on ipTM and peptide-chain pLDDT as primary signals."
            )
            break

    lines += ["## 5. Flagged Observations", ""]
    if auto_flags:
        lines += [f"- {f}" for f in auto_flags]
    else:
        lines.append("_No flags raised._")
    lines.append("")

    lines += ["## 6. Suggested Follow-Up", ""]
    if inputs.follow_up:
        lines += [f"- {item}" for item in inputs.follow_up]
    else:
        lines.append("_None recorded._")
    lines.append("")

    lines += ["## 7. Raw Output Locations", ""]
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


def _format_chain_plddt(per_chain: dict[str, float]) -> str:
    if not per_chain:
        return "—"
    return ", ".join(f"{ch}:{v:.1f}" for ch, v in sorted(per_chain.items()))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
