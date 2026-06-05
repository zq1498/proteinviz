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
    resolve_wrapper,
    suggest_analyses,
    tools_for_subtask,
    validate_inputs,
)


@pytest.fixture(autouse=True)
def _fresh_registry():
    _reload_registry()
    resolve_wrapper.cache_clear()
    yield
    _reload_registry()
    resolve_wrapper.cache_clear()


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


def test_build_roadmap_emits_one_job_per_pair():
    """build_roadmap populates rm.jobs with one Job per (target, peptide) pair."""
    spec = TaskSpec(targets=["MKT"], peptides=["MKTAY"])
    rm = build_roadmap(spec, _empty_feasibility())
    assert len(rm.jobs) == 1
    job = rm.jobs[0]
    assert job.metadata["kind"] == "binding"
    assert "data_aa_sequence" in job.inputs


# ---------------------------------------------------------------------------
# Job plan structure — tool_ids, kwargs, input_bindings
# ---------------------------------------------------------------------------

def test_binding_branch_plan_has_two_steps():
    spec = TaskSpec(targets=["MKTAY"], peptides=["ACDEF"])
    rm = build_roadmap(spec, _empty_feasibility())
    assert len(rm.jobs[0].plan) == 2


def test_binding_branch_plan_tool_ids():
    spec = TaskSpec(targets=["MKTAY"], peptides=["ACDEF"])
    rm = build_roadmap(spec, _empty_feasibility())
    tool_ids = [s.tool_id for s in rm.jobs[0].plan]
    assert tool_ids == ["tool_colabfold", "tool_extract_colabfold"]


def test_binding_branch_calc_extra_ptm_true():
    spec = TaskSpec(targets=["MKTAY"], peptides=["ACDEF"])
    rm = build_roadmap(spec, _empty_feasibility())
    step0 = rm.jobs[0].plan[0]
    assert step0.kwargs.get("calc_extra_ptm") is True


def test_monomer_branch_calc_extra_ptm_false():
    spec = TaskSpec(targets=["MKTAY"])
    rm = build_roadmap(spec, _empty_feasibility())
    step0 = rm.jobs[0].plan[0]
    assert step0.kwargs.get("calc_extra_ptm") is False


def test_binding_branch_prediction_step_input_bindings():
    spec = TaskSpec(targets=["MKTAY"], peptides=["ACDEF"])
    rm = build_roadmap(spec, _empty_feasibility())
    bindings = rm.jobs[0].plan[0].input_bindings
    assert bindings["fasta"] == "data_fasta_file"
    assert bindings["out_dir"] == "_job_dir"


def test_extract_step_binds_out_dir_to_job_dir():
    spec = TaskSpec(targets=["MKTAY"], peptides=["ACDEF"])
    rm = build_roadmap(spec, _empty_feasibility())
    bindings = rm.jobs[0].plan[1].input_bindings
    assert bindings["out_dir"] == "_job_dir"


# ---------------------------------------------------------------------------
# Sequence packing — how targets and peptides are stored in job.inputs
# ---------------------------------------------------------------------------

def test_binding_branch_packs_sequences_as_list():
    target, peptide = "MKTAY", "ACDEF"
    spec = TaskSpec(targets=[target], peptides=[peptide])
    rm = build_roadmap(spec, _empty_feasibility())
    seq = rm.jobs[0].inputs["data_aa_sequence"]
    assert seq == [target, peptide]


def test_monomer_branch_stores_sequence_as_string():
    target = "MKTAY"
    spec = TaskSpec(targets=[target])
    rm = build_roadmap(spec, _empty_feasibility())
    seq = rm.jobs[0].inputs["data_aa_sequence"]
    assert seq == target


# ---------------------------------------------------------------------------
# Job count and metadata across multi-target / multi-peptide specs
# ---------------------------------------------------------------------------

def test_binding_branch_n_targets_x_m_peptides():
    spec = TaskSpec(targets=["AAA", "BBB", "CCC"], peptides=["PPP", "QQQ"])
    rm = build_roadmap(spec, _empty_feasibility())
    assert len(rm.jobs) == 6


def test_binding_branch_job_metadata_labels():
    spec = TaskSpec(targets=["MKTAY"], peptides=["ACDEF", "GHILM"])
    rm = build_roadmap(spec, _empty_feasibility())
    assert rm.jobs[0].metadata == {"target_id": "target_0", "peptide_id": "peptide_0", "kind": "binding"}
    assert rm.jobs[1].metadata == {"target_id": "target_0", "peptide_id": "peptide_1", "kind": "binding"}


def test_monomer_branch_one_job_per_target():
    spec = TaskSpec(targets=["AAA", "BBB"])
    rm = build_roadmap(spec, _empty_feasibility())
    assert len(rm.jobs) == 2
    assert all(j.metadata["kind"] == "monomer" for j in rm.jobs)
    assert rm.jobs[0].metadata["target_id"] == "target_0"
    assert rm.jobs[1].metadata["target_id"] == "target_1"


# ---------------------------------------------------------------------------
# Wrapper resolution — tool_id → Python callable
# ---------------------------------------------------------------------------

def test_resolve_wrapper_colabfold():
    from tools.predict import predict_structure
    assert resolve_wrapper("tool_colabfold") is predict_structure


def test_resolve_wrapper_extract_colabfold():
    from tools.extract import extract_colabfold
    assert resolve_wrapper("tool_extract_colabfold") is extract_colabfold


def test_resolve_wrapper_unknown_tool_raises_key_error():
    with pytest.raises(KeyError):
        resolve_wrapper("tool_does_not_exist")
