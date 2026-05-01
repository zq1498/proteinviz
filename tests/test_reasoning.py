"""Tests for preflight.reasoning helpers."""

from __future__ import annotations

import pytest

from preflight.reasoning import (
    FeasibilityReport,
    Roadmap,
    TaskSpec,
    _reload_registry,
    build_roadmap,
    estimate_runtime,
    suggest_analyses,
    tools_for_subtask,
    validate_inputs,
)


@pytest.fixture(autouse=True)
def _fresh_registry():
    _reload_registry()
    yield
    _reload_registry()


def _empty_feasibility() -> FeasibilityReport:
    return FeasibilityReport(estimated_runtime_s=0.0, gpu_required=True)


# ---------------------------------------------------------------------------
# estimate_runtime
# ---------------------------------------------------------------------------

def test_estimate_runtime_documented_constants():
    # Locks the placeholder in: 1 pair * (60 + 0.5*100) = 110s
    assert estimate_runtime(1, 1, 100) == pytest.approx(110.0)


def test_estimate_runtime_scales_with_pairs():
    one = estimate_runtime(1, 1, 50)
    many = estimate_runtime(4, 20, 50)
    assert many == pytest.approx(80 * one)


def test_estimate_runtime_treats_zero_peptides_as_one_pass():
    # max(1, n_peptides or 1) — monomer-only spec should still give a finite estimate
    assert estimate_runtime(2, 0, 100) > 0


# ---------------------------------------------------------------------------
# validate_inputs
# ---------------------------------------------------------------------------

def test_validate_inputs_short_peptide():
    spec = TaskSpec(targets=["M" * 100], peptides=["MKT"])
    warnings = validate_inputs(spec)
    assert any("peptide[0]" in w and "too short" in w for w in warnings)


def test_validate_inputs_long_peptide():
    spec = TaskSpec(targets=["M" * 100], peptides=["G" * 50])
    warnings = validate_inputs(spec)
    assert any("peptide[0]" in w and "actifpTM" in w for w in warnings)


def test_validate_inputs_oom_target():
    spec = TaskSpec(targets=["M" * 3000], peptides=[])
    warnings = validate_inputs(spec)
    assert any("target[0]" in w and "OOM" in w for w in warnings)


def test_validate_inputs_disulfide_hint():
    spec = TaskSpec(targets=["MKT"], peptides=["ACGCT"])
    warnings = validate_inputs(spec)
    assert any("cysteine pair" in w for w in warnings)


def test_validate_inputs_clean_spec_returns_empty():
    spec = TaskSpec(
        targets=["MKTAYIAKQRQISFVKSHFSRQLEERLGLIEVQGGV"],
        peptides=["MKTAYIAKQRQ"],
    )
    assert validate_inputs(spec) == []


# ---------------------------------------------------------------------------
# Capability lookup
# ---------------------------------------------------------------------------

def test_tools_for_subtask_walks_children():
    """subtask_disorder is a parent; with children it should find more tools."""
    flat = tools_for_subtask("subtask_disorder", include_children=False)
    walked = tools_for_subtask("subtask_disorder", include_children=True)
    assert len(walked) > len(flat), (
        "include_children=True should find tools annotated under disorder_core / "
        "disorder_binding / etc., not just subtask_disorder itself"
    )


def test_tools_for_subtask_unknown_returns_empty():
    assert tools_for_subtask("subtask_does_not_exist") == []


# ---------------------------------------------------------------------------
# suggest_analyses
# ---------------------------------------------------------------------------

def test_suggest_analyses_flags_multiple_targets():
    spec = TaskSpec(targets=["M" * 100, "K" * 100], peptides=["MKTAY"])
    suggestions = suggest_analyses(spec)
    assert any("isoform" in s.lower() or "discrimination" in s.lower() for s in suggestions)


def test_suggest_analyses_flags_long_target_for_disorder():
    spec = TaskSpec(targets=["M" * 250], peptides=["MKTAY"])
    suggestions = suggest_analyses(spec)
    assert any("disorder" in s.lower() for s in suggestions)


def test_suggest_analyses_flags_cysteine_peptides():
    spec = TaskSpec(targets=["M" * 100], peptides=["MKCAY"])
    suggestions = suggest_analyses(spec)
    assert any("cysteine" in s.lower() or "reduced" in s.lower() for s in suggestions)


# ---------------------------------------------------------------------------
# build_roadmap
# ---------------------------------------------------------------------------

def test_build_roadmap_peptide_binding_flow():
    spec = TaskSpec(
        targets=["MKTAYIAKQRQISFVKSHFSRQLEERLGLIEVQGGV"],
        peptides=["MKTAY", "GGGGS"],
    )
    rm = build_roadmap(spec, _empty_feasibility())
    assert isinstance(rm, Roadmap)
    # Predicts 1*2 = 2 pairs
    assert any("2" in s and "complex" in s.lower() for s in rm.steps)
    assert any("Render markdown report" in s for s in rm.steps)
    assert rm.steps[-1] == "Render markdown report with flagged observations"


def test_build_roadmap_monomer_flow_routes_to_folding():
    spec = TaskSpec(targets=["MKTAYIAKQRQ"], peptides=[])
    rm = build_roadmap(spec, _empty_feasibility())
    # Monomer branch: should mention folding tool (tool_colabfold or similar) and pTM
    assert any("monomer" in s.lower() for s in rm.steps)
    assert any("pTM" in s for s in rm.steps)


def test_build_roadmap_long_target_adds_disorder_decision_point():
    spec = TaskSpec(targets=["M" * 250], peptides=["MKTAY"])
    rm = build_roadmap(spec, _empty_feasibility())
    assert any("disorder" in dp.lower() for dp in rm.decision_points)


def test_build_roadmap_jobs_list_is_currently_unpopulated():
    """Pin the current contract: build_roadmap does NOT yet emit Job objects.

    If/when this changes, update ARCHITECTURE.md and this test together.
    """
    spec = TaskSpec(targets=["MKT"], peptides=["MKTAY"])
    rm = build_roadmap(spec, _empty_feasibility())
    assert rm.jobs == []
