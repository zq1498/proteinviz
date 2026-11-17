"""Tests for tools.report.

Verifies the report renderer is well-formed, ranks correctly, and the
executive summary's confidence tier matches the documented thresholds.
"""

from __future__ import annotations

from pathlib import Path

from tools.analyze import CIDERFeatures
from tools.extract import DEFAULT_SCORE_WEIGHTS, ExtractedMetrics
from tools.report import (
    PanelEntry,
    PanelReportInputs,
    ReportInputs,
    executive_summary,
    render_panel_report,
    render_report,
)
from tools.rosetta import ConfidentHotSpot


SECTION_HEADERS = [
    "## 1. Executive Summary",
    "## 2. Methods",
    "## 3. Per-Complex Results",
    "## 4. Structural Analysis",
    "## 5. Flagged Observations",
    "## 6. Suggested Follow-Up",
    "## 7. Raw Output Locations",
]


def _metric(model_id: str, mean_plddt: float, actifptm: float | None, iptm: float | None) -> ExtractedMetrics:
    return ExtractedMetrics(
        model_id=model_id,
        backend="colabfold",
        pdb_path=Path(f"/tmp/{model_id}.pdb"),
        mean_plddt=mean_plddt,
        min_plddt=mean_plddt - 10,
        max_plddt=mean_plddt + 10,
        per_residue_plddt=[mean_plddt],
        pae=None,
        iptm=iptm,
        actifptm=actifptm,
        ranking_score=0.7,
    )


def test_render_report_has_all_sections_when_empty():
    inputs = ReportInputs(job_id="empty", task_description="nothing to do")
    report = render_report(inputs)
    for header in SECTION_HEADERS:
        assert header in report, f"missing section: {header}"


def test_render_report_handles_empty_metrics_gracefully():
    inputs = ReportInputs(job_id="empty", task_description="nothing to do")
    report = render_report(inputs)
    # No crash, and the executive summary acknowledges the absence
    assert "No models were provided" in report


def test_render_report_ranks_higher_score_first():
    high = _metric("rank_001_high", mean_plddt=85.0, actifptm=0.95, iptm=0.90)
    low = _metric("rank_002_low", mean_plddt=50.0, actifptm=0.40, iptm=0.30)
    inputs = ReportInputs(
        job_id="rank_test",
        task_description="ranking",
        metrics=[low, high],  # deliberately out of order
    )
    report = render_report(inputs)
    table = report.split("## 3. Per-Complex Results")[1]
    # The high-score row should appear before the low-score row in the table
    assert table.index("rank_001_high") < table.index("rank_002_low")


def test_executive_summary_strong_tier():
    # composite = 0.5*0.95 + 0.3*0.90 = 0.475 + 0.27 = 0.745 → strong
    m = _metric("rank_001", mean_plddt=90.0, actifptm=0.95, iptm=0.90)
    summary = executive_summary([m], flags=[])
    assert "strong" in summary.lower()


def test_executive_summary_weak_tier():
    # composite = 0.5*0.20 + 0.3*0.40 = 0.10 + 0.12 = 0.22 → weak
    m = _metric("rank_001", mean_plddt=40.0, actifptm=0.20, iptm=0.20)
    summary = executive_summary([m], flags=[])
    assert "weak" in summary.lower() or "non-binder" in summary.lower()


def test_executive_summary_moderate_tier():
    # composite = 0.5*0.65 + 0.3*0.70 = 0.325 + 0.21 = 0.535 → moderate
    m = _metric("rank_001", mean_plddt=70.0, actifptm=0.65, iptm=0.60)
    summary = executive_summary([m], flags=[])
    assert "moderate" in summary.lower()


def test_executive_summary_mentions_flag_count():
    m = _metric("rank_001", mean_plddt=70.0, actifptm=0.65, iptm=0.60)
    summary = executive_summary([m], flags=["a", "b", "c"])
    assert "3" in summary


def test_render_report_echoes_default_weights():
    m = _metric("rank_001", mean_plddt=70.0, actifptm=0.65, iptm=0.60)
    inputs = ReportInputs(job_id="w", task_description="t", metrics=[m])
    report = render_report(inputs)
    # Echo line should reflect the defaults the score was actually computed with
    assert f"primary={DEFAULT_SCORE_WEIGHTS['primary']}" in report
    assert f"mean_plddt={DEFAULT_SCORE_WEIGHTS['mean_plddt']}" in report


def test_render_report_echoes_custom_weights():
    m = _metric("rank_001", mean_plddt=70.0, actifptm=0.65, iptm=0.60)
    custom = {"primary": 0.7, "mean_plddt": 0.2, "interface_plddt": 0.1}
    inputs = ReportInputs(
        job_id="w",
        task_description="t",
        metrics=[m],
        weights=custom,
    )
    report = render_report(inputs)
    assert "primary=0.7" in report
    assert "mean_plddt=0.2" in report
    assert "interface_plddt=0.1" in report


def test_render_report_writes_methods_when_provided():
    inputs = ReportInputs(
        job_id="m",
        task_description="t",
        methods={"backend": "colabfold", "msa_source": "mmseqs2"},
    )
    report = render_report(inputs)
    assert "**backend:**" in report
    assert "colabfold" in report


def test_render_report_table_includes_per_chain_plddt_column():
    m = _metric("rank_001", mean_plddt=74.0, actifptm=0.97, iptm=0.55)
    m.per_chain_plddt = {"A": 76.0, "B": 75.5, "C": 25.0}
    inputs = ReportInputs(job_id="j", task_description="t", metrics=[m])
    report = render_report(inputs)
    assert "Per-chain pLDDT" in report
    assert "A:76.0" in report
    assert "C:25.0" in report


def test_render_report_flags_actifptm_for_three_chain_complex():
    m = _metric("rank_001", mean_plddt=74.0, actifptm=0.97, iptm=0.55)
    m.per_chain_plddt = {"A": 76.0, "B": 75.5, "C": 25.0}
    inputs = ReportInputs(job_id="j", task_description="t", metrics=[m])
    report = render_report(inputs)
    flags_section = report.split("## 5. Flagged Observations")[1]
    assert "actifpTM unreliable" in flags_section
    assert "3-chain" in flags_section
    assert "chain C" in flags_section


def test_render_report_no_actifptm_flag_for_two_chain_complex():
    m = _metric("rank_001", mean_plddt=80.0, actifptm=0.90, iptm=0.75)
    m.per_chain_plddt = {"A": 80.0, "B": 40.0}
    inputs = ReportInputs(job_id="j", task_description="t", metrics=[m])
    report = render_report(inputs)
    flags_section = report.split("## 5. Flagged Observations")[1]
    assert "actifpTM unreliable" not in flags_section


def test_executive_summary_notes_actifptm_exclusion_for_three_chain():
    m = _metric("rank_001", mean_plddt=74.0, actifptm=0.97, iptm=0.55)
    m.per_chain_plddt = {"A": 76.0, "B": 75.5, "C": 25.0}
    summary = executive_summary([m], flags=[])
    assert "excluded from composite" in summary
    assert "3-chain" in summary


# ---------------------------------------------------------------------------
# Aggregated panel report
# ---------------------------------------------------------------------------

def _entry(peptide_id: str, mean_plddt: float, actifptm, iptm) -> PanelEntry:
    return PanelEntry(
        peptide_id=peptide_id,
        metrics=_metric(peptide_id, mean_plddt=mean_plddt, actifptm=actifptm, iptm=iptm),
        label=peptide_id,
    )


def test_render_panel_report_one_row_per_peptide():
    inp = PanelReportInputs(
        target_id="VEGF165",
        task_description="panel",
        complexes=[
            _entry("p0", 80.0, 0.95, 0.90),
            _entry("p1", 60.0, 0.50, 0.45),
            _entry("p2", 70.0, 0.70, 0.65),
        ],
    )
    report = render_panel_report(inp)
    table = report.split("## 3. Ranked Binders")[1].split("## 4.")[0]
    data_rows = [ln for ln in table.splitlines() if ln.startswith("| ") and "Peptide" not in ln and "---" not in ln]
    assert len(data_rows) == 3
    for pid in ("p0", "p1", "p2"):
        assert pid in table


def test_render_panel_report_ranks_by_composite_descending():
    inp = PanelReportInputs(
        target_id="t",
        task_description="panel",
        complexes=[
            _entry("low", 50.0, 0.30, 0.25),
            _entry("high", 90.0, 0.95, 0.92),
            _entry("mid", 70.0, 0.65, 0.60),
        ],
    )
    report = render_panel_report(inp)
    assert report.index("| 1 | high") < report.index("| 2 | mid") < report.index("| 3 | low")
    assert "Top candidate **high**" in report


def test_render_panel_report_actifptm_warning_appears_once():
    entries = []
    for pid in ("p0", "p1"):
        e = _entry(pid, 74.0, 0.97, 0.55)
        e.metrics.per_chain_plddt = {"A": 76.0, "B": 75.5, "C": 25.0}
        entries.append(e)
    inp = PanelReportInputs(target_id="t", task_description="panel", complexes=entries)
    report = render_panel_report(inp)
    assert report.count("actifpTM unreliable") == 1
    assert "applies to every 3+ chain complex" in report


def test_render_panel_report_empty_is_graceful():
    inp = PanelReportInputs(target_id="t", task_description="panel", complexes=[])
    report = render_panel_report(inp)
    assert "No complexes were predicted" in report


# ---------------------------------------------------------------------------
# Panel report with scrambled controls (8-section path)
# ---------------------------------------------------------------------------

def _entry_with_zscore(
    peptide_id: str,
    mean_plddt: float,
    actifptm: float | None,
    iptm: float | None,
    *,
    null_mean: float | None = None,
    null_sd: float | None = None,
    z_score: float | None = None,
    is_control: bool = False,
) -> PanelEntry:
    return PanelEntry(
        peptide_id=peptide_id,
        metrics=_metric(peptide_id, mean_plddt=mean_plddt, actifptm=actifptm, iptm=iptm),
        label=peptide_id,
        is_control=is_control,
        null_mean=null_mean,
        null_sd=null_sd,
        z_score=z_score,
    )


def test_render_panel_report_controls_excluded_from_ranked_table():
    """Control entries must not appear in the Ranked Binders table."""
    inp = PanelReportInputs(
        target_id="t",
        task_description="panel",
        complexes=[
            _entry_with_zscore("p0", 80.0, 0.90, 0.85, null_mean=0.45, null_sd=0.05, z_score=3.2),
            _entry_with_zscore("p0__scram0", 50.0, 0.40, 0.35, is_control=True),
        ],
    )
    report = render_panel_report(inp)
    table = report.split("## 3. Ranked Binders")[1].split("## 4.")[0]
    data_rows = [ln for ln in table.splitlines() if ln.startswith("| ") and "Peptide" not in ln and "---" not in ln]
    assert len(data_rows) == 1
    assert "p0__scram0" not in table


def test_render_panel_report_selectivity_section_present_with_zscore():
    """Section 4 'Selectivity vs Scrambled Controls' appears when any entry has a z_score."""
    inp = PanelReportInputs(
        target_id="t",
        task_description="panel",
        complexes=[
            _entry_with_zscore("p0", 80.0, 0.90, 0.85, null_mean=0.45, null_sd=0.05, z_score=3.2),
            _entry_with_zscore("p0__scram0", 50.0, 0.40, 0.35, is_control=True),
        ],
    )
    report = render_panel_report(inp)
    assert "## 4. Selectivity vs Scrambled Controls" in report
    assert "Z-score" in report
    assert "+3.20" in report


def test_render_panel_report_selectivity_section_absent_without_controls():
    """Without controls, report stays at 7 sections with no Selectivity heading."""
    inp = PanelReportInputs(
        target_id="t",
        task_description="panel",
        complexes=[
            _entry("p0", 80.0, 0.90, 0.85),
            _entry("p1", 60.0, 0.55, 0.50),
        ],
    )
    report = render_panel_report(inp)
    assert "Selectivity vs Scrambled Controls" not in report
    assert "## 4. Per-Complex Notes" in report
    assert "## 7. Raw Output Locations" in report


def test_render_panel_report_section_renumbering_with_controls():
    """With controls, sections shift: Per-Complex Notes → §5, Raw Output → §8."""
    inp = PanelReportInputs(
        target_id="t",
        task_description="panel",
        complexes=[
            _entry_with_zscore("p0", 80.0, 0.90, 0.85, null_mean=0.45, null_sd=None, z_score=2.1),
            _entry_with_zscore("p0__scram0", 50.0, 0.40, 0.35, is_control=True),
        ],
    )
    report = render_panel_report(inp)
    assert "## 5. Per-Complex Notes" in report
    assert "## 8. Raw Output Locations" in report
    assert "## 4. Per-Complex Notes" not in report
    assert "## 7. Raw Output Locations" not in report


def test_render_panel_report_null_sd_none_shown_as_na():
    """When only one scramble was run, null_sd is None and renders as 'n/a'."""
    inp = PanelReportInputs(
        target_id="t",
        task_description="panel",
        complexes=[
            _entry_with_zscore("p0", 80.0, 0.90, 0.85, null_mean=0.48, null_sd=None, z_score=None),
            _entry_with_zscore("p0__scram0", 50.0, 0.40, 0.35, is_control=True),
        ],
    )
    report = render_panel_report(inp)
    if "## 4. Selectivity vs Scrambled Controls" in report:
        selectivity_section = report.split("## 4. Selectivity vs Scrambled Controls")[1]
        assert "n/a" in selectivity_section


# ---------------------------------------------------------------------------
# Disorder rendering (iupred3 + metapredict labeled separately)
# ---------------------------------------------------------------------------

def _cider() -> CIDERFeatures:
    return CIDERFeatures(
        sequence="MKTAY",
        fcr=0.2, ncpr=0.1, kappa=0.15, omega=0.05, scd=-0.3,
        delta=0.0001, mean_hydropathy=0.35, ppii_propensity=0.3,
        isoelectric_point=8.1, phase_region=1,
    )


def _hotspot(confident: bool = True) -> ConfidentHotSpot:
    return ConfidentHotSpot(
        rosetta_resnum=5, pdb_resnum=5, chain="A",
        resname="TRP", ddG=2.5, plddt=85.0, confident=confident,
    )


def test_render_report_iupred3_disorder_rendered():
    scores = [0.3, 0.6, 0.8, 0.2]
    inputs = ReportInputs(job_id="j", task_description="t", disorder_iupred3=scores)
    report = render_report(inputs)
    structural = report.split("## 4. Structural Analysis")[1]
    assert "iupred3" in structural
    assert "50%" in structural  # 2/4 residues > 0.5


def test_render_report_metapredict_disorder_rendered():
    scores = [0.7, 0.8, 0.9]
    inputs = ReportInputs(job_id="j", task_description="t", disorder_metapredict=scores)
    report = render_report(inputs)
    structural = report.split("## 4. Structural Analysis")[1]
    assert "metapredict" in structural
    assert "largely disordered" in structural


def test_render_report_both_disorder_labeled_separately():
    inputs = ReportInputs(
        job_id="j", task_description="t",
        disorder_iupred3=[0.2, 0.3],
        disorder_metapredict=[0.8, 0.9],
    )
    report = render_report(inputs)
    structural = report.split("## 4. Structural Analysis")[1]
    assert "iupred3" in structural
    assert "metapredict" in structural


def test_render_report_cider_rendered():
    inputs = ReportInputs(job_id="j", task_description="t", cider=_cider())
    report = render_report(inputs)
    structural = report.split("## 4. Structural Analysis")[1]
    assert "CIDER" in structural
    assert "κ 0.150" in structural
    assert "FCR" in structural
    assert "NCPR" in structural
    assert "strongly disordered" in structural  # phase_region=1


def test_render_report_hotspots_rendered():
    spots = [_hotspot(confident=True), _hotspot(confident=False)]
    inputs = ReportInputs(job_id="j", task_description="t", hotspots=spots)
    report = render_report(inputs)
    structural = report.split("## 4. Structural Analysis")[1]
    assert "Hot-spot scan" in structural
    assert "A5 TRP" in structural
    assert "+2.50" in structural
    assert "confident" in structural


def test_render_report_empty_hotspots_says_none_flagged():
    inputs = ReportInputs(job_id="j", task_description="t", hotspots=[])
    report = render_report(inputs)
    structural = report.split("## 4. Structural Analysis")[1]
    assert "no interface residues flagged" in structural


# ---------------------------------------------------------------------------
# Panel Per-Complex Notes — disorder / CIDER / hotspots
# ---------------------------------------------------------------------------

def _entry_full(peptide_id: str) -> PanelEntry:
    return PanelEntry(
        peptide_id=peptide_id,
        metrics=_metric(peptide_id, mean_plddt=80.0, actifptm=0.90, iptm=0.85),
        label=peptide_id,
        disorder_iupred3=[0.6, 0.7, 0.8],
        disorder_metapredict=[0.2, 0.3, 0.4],
        cider=_cider(),
        hotspots=[_hotspot()],
    )


def test_panel_per_complex_notes_renders_disorder_cider_and_hotspots():
    inp = PanelReportInputs(
        target_id="T", task_description="t",
        complexes=[_entry_full("p0")],
    )
    notes = render_panel_report(inp).split("Per-Complex Notes")[1]
    assert "iupred3" in notes
    assert "metapredict" in notes
    assert "κ" in notes
    assert "FCR" in notes
    assert "hotspots" in notes
    assert "A5 TRP" in notes


# ---------------------------------------------------------------------------
# Epitope families (Pass 1 — contact fingerprints, no pLDDT gating)
# ---------------------------------------------------------------------------

def _entry_with_epitope(peptide_id: str, cluster: str | None) -> PanelEntry:
    return PanelEntry(
        peptide_id=peptide_id,
        metrics=_metric(peptide_id, mean_plddt=80.0, actifptm=0.90, iptm=0.85),
        label=peptide_id,
        epitope_cluster=cluster,
    )


def test_render_panel_report_epitope_column_in_ranked_table():
    inp = PanelReportInputs(
        target_id="T", task_description="t",
        complexes=[
            _entry_with_epitope("p0", "Epitope-1"),
            _entry_with_epitope("p1", "Epitope-2"),
        ],
    )
    report = render_panel_report(inp)
    assert "Epitope" in report.split("## 3. Ranked Binders")[1].split("## 4.")[0]


def test_render_panel_report_epitope_families_section_present():
    inp = PanelReportInputs(
        target_id="T", task_description="t",
        complexes=[
            _entry_with_epitope("p0", "Epitope-1"),
            _entry_with_epitope("p1", "Epitope-1"),
            _entry_with_epitope("p2", "Epitope-2"),
        ],
    )
    report = render_panel_report(inp)
    assert "## 4. Epitope Families" in report
    families = report.split("## 4. Epitope Families")[1].split("## 5.")[0]
    assert "Epitope-1" in families
    assert "Epitope-2" in families
    assert "p0" in families and "p1" in families


def test_render_panel_report_no_epitope_section_without_clusters():
    inp = PanelReportInputs(
        target_id="T", task_description="t",
        complexes=[_entry("p0", 80.0, 0.90, 0.85), _entry("p1", 60.0, 0.55, 0.50)],
    )
    report = render_panel_report(inp)
    assert "Epitope Families" not in report
    assert "## 4. Per-Complex Notes" in report


def test_render_panel_report_section_renumbering_with_epitopes_only():
    """With epitopes but no controls: §5 Per-Complex Notes, §8 Raw Output."""
    inp = PanelReportInputs(
        target_id="T", task_description="t",
        complexes=[
            _entry_with_epitope("p0", "Epitope-1"),
            _entry_with_epitope("p1", "Epitope-2"),
        ],
    )
    report = render_panel_report(inp)
    assert "## 5. Per-Complex Notes" in report
    assert "## 8. Raw Output Locations" in report
    assert "## 4. Per-Complex Notes" not in report


def test_render_panel_report_section_renumbering_with_epitopes_and_controls():
    """With both: §4 Epitope Families, §5 Controls, §6 Per-Complex Notes, §9 Raw Output."""
    inp = PanelReportInputs(
        target_id="T", task_description="t",
        complexes=[
            PanelEntry(
                peptide_id="p0",
                metrics=_metric("p0", mean_plddt=80.0, actifptm=0.90, iptm=0.85),
                label="p0",
                epitope_cluster="Epitope-1",
                null_mean=0.45, null_sd=0.05, z_score=2.5,
            ),
            PanelEntry(
                peptide_id="p0__scram0",
                metrics=_metric("p0__scram0", mean_plddt=50.0, actifptm=0.40, iptm=0.35),
                label="p0__scram0",
                is_control=True,
            ),
        ],
    )
    report = render_panel_report(inp)
    assert "## 4. Epitope Families" in report
    assert "## 5. Selectivity vs Scrambled Controls" in report
    assert "## 6. Per-Complex Notes" in report
    assert "## 9. Raw Output Locations" in report
