"""Tests for tools.report.

Verifies the report renderer is well-formed, ranks correctly, and the
executive summary's confidence tier matches the documented thresholds.
"""

from __future__ import annotations

from pathlib import Path

from tools.extract import DEFAULT_SCORE_WEIGHTS, ExtractedMetrics
from tools.report import ReportInputs, executive_summary, render_report


SECTION_HEADERS = [
    "## 1. Executive Summary",
    "## 2. Methods",
    "## 3. Per-Complex Results",
    "## 4. Flagged Observations",
    "## 5. Suggested Follow-Up",
    "## 6. Raw Output Locations",
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


def test_render_report_has_all_six_sections_when_empty():
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
    flags_section = report.split("## 4. Flagged Observations")[1]
    assert "actifpTM unreliable" in flags_section
    assert "3-chain" in flags_section
    assert "chain C" in flags_section


def test_render_report_no_actifptm_flag_for_two_chain_complex():
    m = _metric("rank_001", mean_plddt=80.0, actifptm=0.90, iptm=0.75)
    m.per_chain_plddt = {"A": 80.0, "B": 40.0}
    inputs = ReportInputs(job_id="j", task_description="t", metrics=[m])
    report = render_report(inputs)
    flags_section = report.split("## 4. Flagged Observations")[1]
    assert "actifpTM unreliable" not in flags_section


def test_executive_summary_notes_actifptm_exclusion_for_three_chain():
    m = _metric("rank_001", mean_plddt=74.0, actifptm=0.97, iptm=0.55)
    m.per_chain_plddt = {"A": 76.0, "B": 75.5, "C": 25.0}
    summary = executive_summary([m], flags=[])
    assert "excluded from composite" in summary
    assert "3-chain" in summary
