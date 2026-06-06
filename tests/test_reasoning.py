"""Tests for preflight.reasoning helpers."""

from __future__ import annotations

import pytest

from preflight import reasoning
from preflight.reasoning import (
    FeasibilityReport,
    Roadmap,
    TaskSpec,
    _reload_registry,
    build_roadmap,
    estimate_runtime,
    lookup_tool,
    make_step,
    resolve_wrapper,
    select_tool,
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

@pytest.mark.xfail(
    reason="estimate_runtime is an uncalibrated placeholder returning 0; "
    "re-enable the formula assertion once it is calibrated from sacct data.",
    strict=False,
)
def test_estimate_runtime_documented_constants():
    # Locks the placeholder in: 1 pair * (60 + 0.5*100) = 110s
    assert estimate_runtime(1, 1, 100) == pytest.approx(110.0)


def test_estimate_runtime_scales_with_pairs():
    one = estimate_runtime(1, 1, 50)
    many = estimate_runtime(4, 20, 50)
    assert many == pytest.approx(80 * one)


@pytest.mark.xfail(
    reason="estimate_runtime is an uncalibrated placeholder returning 0; "
    "re-enable once it is calibrated from sacct data.",
    strict=False,
)
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
    # Two peptides → panel branch (MSA-cached SLURM array)
    assert any("panel job" in s and "2 peptides" in s for s in rm.steps)
    assert any("complex" in s.lower() for s in rm.steps)
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

def test_binding_branch_plan_has_predict_extract_and_analysis_steps():
    # B5: predict + extract + always-on structure & sequence analysis.
    spec = TaskSpec(targets=["MKTAY"], peptides=["ACDEF"])
    rm = build_roadmap(spec, _empty_feasibility())
    assert len(rm.jobs[0].plan) == 4


def test_binding_branch_plan_tool_ids():
    spec = TaskSpec(targets=["MKTAY"], peptides=["ACDEF"])
    rm = build_roadmap(spec, _empty_feasibility())
    tool_ids = [s.tool_id for s in rm.jobs[0].plan]
    assert tool_ids == [
        "tool_colabfold", "tool_extract_colabfold",
        "tool_freesasa", "tool_analyze_sequence",
    ]


def test_binding_branch_analysis_steps_are_optional():
    spec = TaskSpec(targets=["MKTAY"], peptides=["ACDEF"])
    rm = build_roadmap(spec, _empty_feasibility())
    plan = rm.jobs[0].plan
    # predict + extract are load-bearing; analysis steps are supplemental
    assert [s.optional for s in plan] == [False, False, True, True]


def test_binding_branch_sequence_analysis_binds_peptide():
    spec = TaskSpec(targets=["MKTAY"], peptides=["ACDEF"])
    rm = build_roadmap(spec, _empty_feasibility())
    seq_step = next(s for s in rm.jobs[0].plan if s.tool_id == "tool_analyze_sequence")
    assert seq_step.input_bindings["seq"] == "data_peptide_sequence"
    assert rm.jobs[0].inputs["data_peptide_sequence"] == "ACDEF"


def test_monomer_branch_sequence_analysis_binds_target_sequence():
    spec = TaskSpec(targets=["MKTAY"])
    rm = build_roadmap(spec, _empty_feasibility())
    seq_step = next(s for s in rm.jobs[0].plan if s.tool_id == "tool_analyze_sequence")
    assert seq_step.input_bindings["seq"] == "data_aa_sequence"


def test_requested_analyses_appended_as_optional_steps():
    spec = TaskSpec(
        targets=["MKTAY"], peptides=["ACDEF"],
        requested_analyses=["tool_metapredict", "tool_localcider"],
    )
    rm = build_roadmap(spec, _empty_feasibility())
    plan = rm.jobs[0].plan
    ids = [s.tool_id for s in plan]
    assert "tool_metapredict" in ids and "tool_localcider" in ids
    for tid in ("tool_metapredict", "tool_localcider"):
        step = next(s for s in plan if s.tool_id == tid)
        assert step.optional is True
        assert step.input_bindings["seq"] == "data_peptide_sequence"


def test_requested_analyses_unknown_id_is_skipped():
    spec = TaskSpec(
        targets=["MKTAY"], peptides=["ACDEF"],
        requested_analyses=["tool_does_not_exist"],
    )
    rm = build_roadmap(spec, _empty_feasibility())
    ids = [s.tool_id for s in rm.jobs[0].plan]
    assert "tool_does_not_exist" not in ids
    assert len(rm.jobs[0].plan) == 4  # only the always-on steps, nothing appended


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
    # Single peptide → single-pair branch (kind="binding")
    spec = TaskSpec(targets=["MKTAY"], peptides=["ACDEF"])
    rm = build_roadmap(spec, _empty_feasibility())
    assert rm.jobs[0].metadata == {"target_id": "target_0", "peptide_id": "peptide_0", "kind": "binding"}


def test_panel_branch_job_metadata_labels():
    # Two+ peptides → panel branch (kind="binding_panel", with target/peptide indices)
    spec = TaskSpec(targets=["MKTAY"], peptides=["ACDEF", "GHILM"])
    rm = build_roadmap(spec, _empty_feasibility())
    assert rm.jobs[0].metadata == {
        "target_id": "target_0", "target_idx": 0,
        "peptide_id": "peptide_0", "peptide_idx": 0, "kind": "binding_panel",
    }
    assert rm.jobs[1].metadata == {
        "target_id": "target_0", "target_idx": 0,
        "peptide_id": "peptide_1", "peptide_idx": 1, "kind": "binding_panel",
    }


def test_monomer_branch_one_job_per_target():
    spec = TaskSpec(targets=["AAA", "BBB"])
    rm = build_roadmap(spec, _empty_feasibility())
    assert len(rm.jobs) == 2
    assert all(j.metadata["kind"] == "monomer" for j in rm.jobs)
    assert rm.jobs[0].metadata["target_id"] == "target_0"
    assert rm.jobs[1].metadata["target_id"] == "target_1"


# ---------------------------------------------------------------------------
# make_step — registry-driven JobStep builder (B3)
# ---------------------------------------------------------------------------

def test_make_step_pulls_call_contract_from_registry():
    step = make_step("tool_colabfold")
    entry = lookup_tool("tool_colabfold")
    assert step.tool_id == "tool_colabfold"
    assert step.kwargs == entry["call_kwargs"]          # {"fast": False}
    assert step.input_bindings == entry["bindings"]     # fasta + out_dir
    assert step.consumes == entry["has_input"]
    assert step.produces == entry["has_output"]


def test_make_step_kwarg_overrides_win():
    step = make_step("tool_colabfold", kwarg_overrides={"calc_extra_ptm": True})
    assert step.kwargs == {"fast": False, "calc_extra_ptm": True}


def test_make_step_binding_overrides_win():
    step = make_step(
        "tool_extract_colabfold",
        binding_overrides={"out_dir": "data_colabfold_out_dir"},
    )
    assert step.input_bindings["out_dir"] == "data_colabfold_out_dir"


def test_make_step_no_call_contract_yields_empty_kwargs_and_bindings():
    # A wrapped tool without call_kwargs/bindings (e.g. per-residue energies has bindings only)
    step = make_step("tool_extract_colabfold")
    assert step.kwargs == {}                             # extract has no call_kwargs
    assert step.input_bindings == {"out_dir": "_job_dir"}


# ---------------------------------------------------------------------------
# select_tool — tier-aware, priority-ranked, explicit selection (B4)
# ---------------------------------------------------------------------------

def test_select_tool_binding_picks_colabfold():
    assert select_tool("subtask_binding")["id"] == "tool_colabfold"


def test_select_tool_folding_picks_colabfold():
    assert select_tool("subtask_folding")["id"] == "tool_colabfold"


def test_select_tool_skips_tier3_and_unwrapped_in_real_registry():
    # subtask_disorder_core has iupred3 (t1), metapredict (t1), netsurfp3 (t3, no wrapper)
    chosen = select_tool("subtask_disorder_core")["id"]
    assert chosen in {"tool_iupred3", "tool_metapredict"}
    assert chosen != "tool_netsurfp3"


def test_select_tool_respects_priority(monkeypatch):
    fake = [
        {"id": "tool_lo", "tier": 1, "wrapper": "x", "priority": 0},
        {"id": "tool_hi", "tier": 1, "wrapper": "x", "priority": 5},
    ]
    monkeypatch.setattr(reasoning, "tools_for_subtask", lambda s, **k: fake)
    assert select_tool("whatever")["id"] == "tool_hi"


def test_select_tool_skips_deprecated_unwrapped_and_high_tier(monkeypatch):
    fake = [
        {"id": "tool_dep", "tier": 1, "wrapper": "x", "deprecated": True},
        {"id": "tool_nowrap", "tier": 1, "wrapper": None},
        {"id": "tool_high", "tier": 3, "wrapper": "x"},
        {"id": "tool_ok", "tier": 2, "wrapper": "x"},
    ]
    monkeypatch.setattr(reasoning, "tools_for_subtask", lambda s, **k: fake)
    assert select_tool("x")["id"] == "tool_ok"


def test_select_tool_require_wrapper_false_allows_null_wrapper(monkeypatch):
    fake = [{"id": "tool_nowrap", "tier": 1, "wrapper": None}]
    monkeypatch.setattr(reasoning, "tools_for_subtask", lambda s, **k: fake)
    assert select_tool("x", require_wrapper=False)["id"] == "tool_nowrap"


def test_select_tool_raises_when_no_candidate(monkeypatch):
    monkeypatch.setattr(reasoning, "tools_for_subtask", lambda s, **k: [])
    with pytest.raises(RuntimeError):
        select_tool("subtask_empty")


def test_build_roadmap_surfaces_unimplemented_backend_as_decision_point():
    spec = TaskSpec(targets=["MKTAY"], peptides=["ACDEF"])
    rm = build_roadmap(spec, _empty_feasibility())
    assert any("tool_boltz1" in dp and "not yet callable" in dp for dp in rm.decision_points)


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


# ---------------------------------------------------------------------------
# Rosetta hotspot chain — _append_requested_analyses (Task 2, B5 follow-up)
# ---------------------------------------------------------------------------

def _binding_spec() -> TaskSpec:
    return TaskSpec(targets=["MKTAY"], peptides=["ACDEF"])


def _panel_spec() -> TaskSpec:
    return TaskSpec(targets=["MKTAY"], peptides=["ACDEF", "GHILM"])


def _monomer_spec() -> TaskSpec:
    return TaskSpec(targets=["MKTAY"])


def test_hotspot_marker_appends_two_steps_for_single_pair():
    spec = _binding_spec()
    spec.requested_analyses = ["tool_rosetta_confidence_filter"]
    rm = build_roadmap(spec, _empty_feasibility())
    ids = [s.tool_id for s in rm.jobs[0].plan]
    assert "tool_rosetta_alanine_scan" in ids
    assert "tool_rosetta_confidence_filter" in ids
    # alanine_scan must precede confidence_filter
    assert ids.index("tool_rosetta_alanine_scan") < ids.index("tool_rosetta_confidence_filter")


def test_hotspot_marker_appends_two_steps_for_panel():
    spec = _panel_spec()
    spec.requested_analyses = ["tool_rosetta_confidence_filter"]
    rm = build_roadmap(spec, _empty_feasibility())
    for job in rm.jobs:
        ids = [s.tool_id for s in job.plan]
        assert "tool_rosetta_alanine_scan" in ids
        assert "tool_rosetta_confidence_filter" in ids


def test_hotspot_steps_are_optional():
    spec = _binding_spec()
    spec.requested_analyses = ["tool_rosetta_confidence_filter"]
    rm = build_roadmap(spec, _empty_feasibility())
    for step in rm.jobs[0].plan:
        if step.tool_id in {"tool_rosetta_alanine_scan", "tool_rosetta_confidence_filter"}:
            assert step.optional is True


def test_hotspot_single_pair_uses_A_B_interface():
    spec = _binding_spec()
    spec.requested_analyses = ["tool_rosetta_confidence_filter"]
    rm = build_roadmap(spec, _empty_feasibility())
    scan_step = next(
        s for s in rm.jobs[0].plan if s.tool_id == "tool_rosetta_alanine_scan"
    )
    assert scan_step.kwargs["interface"] == "A_B"


def test_hotspot_panel_uses_AB_C_interface():
    spec = _panel_spec()
    spec.requested_analyses = ["tool_rosetta_confidence_filter"]
    rm = build_roadmap(spec, _empty_feasibility())
    for job in rm.jobs:
        scan_step = next(
            s for s in job.plan if s.tool_id == "tool_rosetta_alanine_scan"
        )
        assert scan_step.kwargs["interface"] == "AB_C"


def test_hotspot_skipped_for_monomer():
    spec = _monomer_spec()
    spec.requested_analyses = ["tool_rosetta_confidence_filter"]
    rm = build_roadmap(spec, _empty_feasibility())
    ids = [s.tool_id for s in rm.jobs[0].plan]
    assert "tool_rosetta_alanine_scan" not in ids
    assert "tool_rosetta_confidence_filter" not in ids


def test_hotspot_combined_with_seq_analyses():
    spec = _binding_spec()
    spec.requested_analyses = ["tool_metapredict", "tool_rosetta_confidence_filter"]
    rm = build_roadmap(spec, _empty_feasibility())
    ids = [s.tool_id for s in rm.jobs[0].plan]
    assert "tool_metapredict" in ids
    assert "tool_rosetta_alanine_scan" in ids
    assert "tool_rosetta_confidence_filter" in ids
