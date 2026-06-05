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


def _binding_tier(score: float) -> tuple[str, str]:
    """Composite-score → (confidence label, caveat) for complex/binding predictions."""
    if score > 0.70:
        return ("strong binding candidate", "Proceed with experimental validation.")
    if score > 0.50:
        return (
            "moderate binding candidate",
            "Interpret with caution; additional metrics or experimental follow-up recommended.",
        )
    return (
        "weak or likely non-binder",
        "Low confidence across all ranking metrics; treat predictions skeptically.",
    )


def _fold_tier(ptm: float) -> tuple[str, str]:
    """pTM → (confidence label, caveat) for monomer fold predictions."""
    if ptm > 0.80:
        return ("high-confidence fold", "Structure is likely reliable for downstream analysis.")
    if ptm > 0.50:
        return (
            "moderate-confidence fold",
            "Core structure is probably correct; flexible or disordered regions may be unreliable.",
        )
    return (
        "low-confidence fold",
        "Significant disorder or structural uncertainty; interpret with care.",
    )


def _actifptm_warning(m: ExtractedMetrics) -> str | None:
    """Reliability warning when actifpTM is reported for a 3+ chain complex, else None.

    actifpTM aggregates across all inter-chain interfaces, so in a homodimer + peptide
    setup the well-structured dimer interface inflates it regardless of peptide binding.
    It is excluded from the composite score for such complexes (see extract.composite_score).
    """
    if len(m.per_chain_plddt) < 3 or m.actifptm is None:
        return None
    chain_str = _format_chain_plddt(m.per_chain_plddt)
    min_chain = min(m.per_chain_plddt, key=lambda ch: m.per_chain_plddt[ch])
    return (
        f"**actifpTM unreliable in {len(m.per_chain_plddt)}-chain complex.** "
        f"actifpTM ({m.actifptm:.3f}) aggregates across all inter-chain interfaces. "
        f"In a homodimer + peptide setup the well-structured dimer interface dominates, "
        f"inflating this metric regardless of peptide binding quality. "
        f"actifpTM is excluded from the composite score for this complex. "
        f"Per-chain pLDDT: {chain_str} — chain {min_chain} "
        f"({m.per_chain_plddt[min_chain]:.1f}) is the low-confidence chain. "
        f"Rely on ipTM and peptide-chain pLDDT as primary signals."
    )


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
        confidence, caveat = _binding_tier(score)
    else:
        confidence, caveat = _fold_tier(best.ranking_score or 0.0)

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
        warning = _actifptm_warning(m)
        if warning is not None:
            auto_flags.append(warning)
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


# ---------------------------------------------------------------------------
# Aggregated panel report — one target, many ranked peptides (one row per complex)
# ---------------------------------------------------------------------------

@dataclass
class PanelEntry:
    """One target-peptide complex within a panel: its best (rank-1) model + analysis."""
    peptide_id: str
    metrics: ExtractedMetrics                      # best model for this complex
    structure: StructureFeatures | None = None     # from analyze_structure()
    sequence: SequenceFeatures | None = None       # from analyze_sequence()
    label: str | None = None                       # human label (e.g. peptide sequence)
    job_dir: Path | None = None                    # raw output location


@dataclass
class PanelReportInputs:
    """Inputs for an aggregated panel report ranking all peptides against one target."""
    target_id: str
    task_description: str
    complexes: list[PanelEntry] = field(default_factory=list)
    methods: dict[str, str] = field(default_factory=dict)
    flags: list[str] = field(default_factory=list)
    follow_up: list[str] = field(default_factory=list)
    output_paths: dict[str, Path] = field(default_factory=dict)
    weights: dict[str, float] | None = None


def _peptide_chain_plddt(m: ExtractedMetrics) -> str:
    """Best-effort peptide-chain pLDDT: ColabFold assigns the peptide after the
    target chains, so the last chain id is the peptide. '—' if unknown."""
    if not m.per_chain_plddt:
        return "—"
    last_chain = sorted(m.per_chain_plddt)[-1]
    return f"{last_chain}:{m.per_chain_plddt[last_chain]:.1f}"


def _panel_executive_summary(
    ranked: list[PanelEntry], target_id: str, weights: dict[str, float] | None
) -> str:
    if not ranked:
        return f"No complexes were predicted for {target_id}."
    kw: dict = {"weights": weights} if weights else {}
    scores = [composite_score(e.metrics, **kw) for e in ranked]
    top, top_score = ranked[0], scores[0]
    tier, caveat = _binding_tier(top_score)
    strong = sum(1 for s in scores if s > 0.70)
    moderate = sum(1 for s in scores if 0.50 < s <= 0.70)
    return (
        f"Screened {len(ranked)} peptide(s) against **{target_id}**. Top candidate "
        f"**{top.label or top.peptide_id}** is a {tier} (composite {top_score:.3f}). "
        f"{strong} strong / {moderate} moderate candidate(s); composite scores span "
        f"{min(scores):.3f}–{max(scores):.3f}. {caveat}"
    )


def render_panel_report(inp: PanelReportInputs) -> str:
    """Render an aggregated panel report: one row per complex, ranked by composite score.

    Matches the CLAUDE.md reporting spec ('ranked binder suggestions, one row per
    complex'). Use this for binding panels; use render_report for single complexes
    and monomers.
    """
    kw: dict = {"weights": inp.weights} if inp.weights else {}
    used_weights = inp.weights or DEFAULT_SCORE_WEIGHTS
    ranked = sorted(inp.complexes, key=lambda e: composite_score(e.metrics, **kw), reverse=True)

    lines: list[str] = [
        f"# Panel Report: {inp.target_id}",
        f"_Generated {_now_iso()} · task: {inp.task_description}_",
        "",
        "## 1. Executive Summary",
        "",
        _panel_executive_summary(ranked, inp.target_id, inp.weights),
        f"_Score weights: primary={used_weights['primary']}, "
        f"mean_plddt={used_weights['mean_plddt']}, "
        f"interface_plddt={used_weights['interface_plddt']}_",
        "",
        "## 2. Methods",
        "",
    ]
    if inp.methods:
        for k, v in inp.methods.items():
            lines.append(f"- **{k}:** {v}")
    else:
        lines.append("_No methods recorded._")
    lines.append("")

    lines += [
        "## 3. Ranked Binders",
        "",
        "| Rank | Peptide | Composite | ipTM | actifpTM | Mean pLDDT | Peptide-chain pLDDT | BSA (Å²) | Interface res |",
        "|------|---------|-----------|------|----------|------------|---------------------|----------|---------------|",
    ]
    for i, e in enumerate(ranked, 1):
        m = e.metrics
        cs = composite_score(m, **kw)
        iptm = f"{m.iptm:.3f}" if m.iptm is not None else "—"
        actifptm = f"{m.actifptm:.3f}" if m.actifptm is not None else "—"
        bsa = (
            f"{e.structure.buried_surface_area:.0f}"
            if e.structure and e.structure.buried_surface_area is not None
            else "—"
        )
        n_iface = str(len(e.structure.interface_residues)) if e.structure else "—"
        lines.append(
            f"| {i} | {e.label or e.peptide_id} | {cs:.3f} | {iptm} | {actifptm} |"
            f" {m.mean_plddt:.1f} | {_peptide_chain_plddt(m)} | {bsa} | {n_iface} |"
        )
    lines.append("")

    lines += ["## 4. Per-Complex Notes", ""]
    notes_written = False
    for e in ranked:
        bits: list[str] = []
        if e.structure and e.structure.disulfide_pairs:
            pairs = ", ".join(f"{c1}{r1}–{c2}{r2}" for c1, r1, c2, r2 in e.structure.disulfide_pairs)
            bits.append(f"disulfides {pairs}")
        if e.sequence and e.sequence.flags:
            bits.append("; ".join(e.sequence.flags))
        if bits:
            lines.append(f"- **{e.label or e.peptide_id}:** {' · '.join(bits)}")
            notes_written = True
    if not notes_written:
        lines.append("_No per-complex structural flags._")
    lines.append("")

    auto_flags = list(inp.flags)
    for e in ranked:
        warning = _actifptm_warning(e.metrics)
        if warning is not None:
            auto_flags.append(warning + " This applies to every 3+ chain complex in this panel.")
            break
    lines += ["## 5. Flagged Observations", ""]
    lines += [f"- {f}" for f in auto_flags] if auto_flags else ["_No flags raised._"]
    lines.append("")

    lines += ["## 6. Suggested Follow-Up", ""]
    lines += [f"- {item}" for item in inp.follow_up] if inp.follow_up else ["_None recorded._"]
    lines.append("")

    lines += ["## 7. Raw Output Locations", ""]
    paths = dict(inp.output_paths)
    for e in ranked:
        if e.job_dir is not None:
            paths[e.label or e.peptide_id] = e.job_dir
    if paths:
        for label, path in paths.items():
            lines.append(f"- **{label}:** `{path}`")
    else:
        lines.append("_No output paths recorded._")
    lines.append("")

    return "\n".join(lines)


def write_panel_report(inp: PanelReportInputs, out_path: Path) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_panel_report(inp))
    return out_path


def _format_chain_plddt(per_chain: dict[str, float]) -> str:
    if not per_chain:
        return "—"
    return ", ".join(f"{ch}:{v:.1f}" for ch, v in sorted(per_chain.items()))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
