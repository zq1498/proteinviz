"""Tests for tools.batch internal helpers.

Focuses on _ensure_fasta, which is the translation layer between the
sequence representation stored in Job.inputs and the FASTA file that
gets uploaded to DCC. Getting this wrong means ColabFold receives the
wrong input — either a monomer instead of a complex or a malformed file.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.batch import _ensure_fasta


# ---------------------------------------------------------------------------
# _ensure_fasta — monomer path
# ---------------------------------------------------------------------------

def test_ensure_fasta_monomer(tmp_path: Path):
    inputs = {"data_aa_sequence": "MKTAY"}
    result = _ensure_fasta(inputs, tmp_path)
    fasta = tmp_path / "input.fasta"
    assert fasta.exists()
    assert fasta.read_text() == ">query\nMKTAY\n"
    assert result["data_fasta_file"] == fasta


# ---------------------------------------------------------------------------
# _ensure_fasta — multimer path (ColabFold ':' convention)
# ---------------------------------------------------------------------------

def test_ensure_fasta_multimer_joins_chains_with_colon(tmp_path: Path):
    inputs = {"data_aa_sequence": ["MKTAY", "ACDEF"]}
    _ensure_fasta(inputs, tmp_path)
    content = (tmp_path / "input.fasta").read_text()
    assert content == ">query\nMKTAY:ACDEF\n"


def test_ensure_fasta_multimer_three_chains(tmp_path: Path):
    inputs = {"data_aa_sequence": ["AAA", "BBB", "CCC"]}
    _ensure_fasta(inputs, tmp_path)
    content = (tmp_path / "input.fasta").read_text()
    assert content == ">query\nAAA:BBB:CCC\n"


def test_ensure_fasta_multimer_sets_fasta_key(tmp_path: Path):
    inputs = {"data_aa_sequence": ["MKTAY", "ACDEF"]}
    result = _ensure_fasta(inputs, tmp_path)
    assert result["data_fasta_file"] == tmp_path / "input.fasta"


# ---------------------------------------------------------------------------
# _ensure_fasta — skip if fasta already present
# ---------------------------------------------------------------------------

def test_ensure_fasta_skips_if_fasta_already_set(tmp_path: Path):
    existing = tmp_path / "existing.fasta"
    existing.write_text(">x\nMKT\n")
    inputs = {"data_fasta_file": existing, "data_aa_sequence": "MKTAY"}
    result = _ensure_fasta(inputs, tmp_path)
    assert result["data_fasta_file"] == existing
    assert (tmp_path / "input.fasta").exists() is False


def test_ensure_fasta_skips_if_fasta_is_none_only_when_sequence_also_missing(tmp_path: Path):
    # No sequence, no fasta — returns inputs unchanged rather than raising
    inputs: dict = {}
    result = _ensure_fasta(inputs, tmp_path)
    assert "data_fasta_file" not in result


# ---------------------------------------------------------------------------
# _ensure_fasta — bad input type
# ---------------------------------------------------------------------------

def test_ensure_fasta_rejects_invalid_sequence_type(tmp_path: Path):
    inputs = {"data_aa_sequence": 12345}
    with pytest.raises(TypeError, match="str or list"):
        _ensure_fasta(inputs, tmp_path)


# ---------------------------------------------------------------------------
# emit_reports — report emission grouped by target
# ---------------------------------------------------------------------------

from preflight.jobs import BatchResult, Job
from tools.batch import (
    _propagate_outputs,
    _resolve_output_expr,
    build_panel_report_inputs,
    emit_reports,
)
from tools.extract import ExtractedMetrics


# ---------------------------------------------------------------------------
# _resolve_output_expr — the three supported expression forms
# ---------------------------------------------------------------------------

class _FakeResult:
    def __init__(self, pdb_paths=None, a3m_paths=None):
        self.pdb_paths = pdb_paths if pdb_paths is not None else []
        self.a3m_paths = a3m_paths if a3m_paths is not None else []


def test_resolve_output_expr_whole_result():
    r = _FakeResult()
    assert _resolve_output_expr(r, ".") is r


def test_resolve_output_expr_plain_attr():
    r = _FakeResult(a3m_paths=[Path("/tmp/a.a3m")])
    assert _resolve_output_expr(r, "a3m_paths") == [Path("/tmp/a.a3m")]


def test_resolve_output_expr_index_first_element():
    r = _FakeResult(pdb_paths=[Path("/tmp/rank1.pdb"), Path("/tmp/rank2.pdb")])
    assert _resolve_output_expr(r, "pdb_paths[0]") == Path("/tmp/rank1.pdb")


def test_resolve_output_expr_index_empty_list_returns_none():
    assert _resolve_output_expr(_FakeResult(pdb_paths=[]), "pdb_paths[0]") is None


def test_resolve_output_expr_missing_attr_returns_none():
    assert _resolve_output_expr(_FakeResult(), "does_not_exist") is None


# ---------------------------------------------------------------------------
# _propagate_outputs — registry-driven (B2)
# ---------------------------------------------------------------------------

def test_propagate_outputs_colabfold_sets_pdb_and_a3m():
    job = Job(job_id="j", inputs={}, plan=[])
    result = _FakeResult(
        pdb_paths=[Path("/tmp/rank1.pdb"), Path("/tmp/rank2.pdb")],
        a3m_paths=[Path("/tmp/q.a3m")],
    )
    _propagate_outputs(job, "tool_colabfold", result)
    assert job.inputs["data_pdb_complex"] == Path("/tmp/rank1.pdb")
    assert job.inputs["data_pdb_file"] == Path("/tmp/rank1.pdb")
    assert job.inputs["data_a3m_msa"] == [Path("/tmp/q.a3m")]


def test_propagate_outputs_terminal_tool_sets_nothing():
    # tool_extract_colabfold has no output_map → job.inputs unchanged
    job = Job(job_id="j", inputs={}, plan=[])
    _propagate_outputs(job, "tool_extract_colabfold", [object()])
    assert job.inputs == {}


def test_propagate_outputs_skips_absent_outputs():
    # No pdb_paths/a3m_paths populated → nothing propagated, no crash on empty index
    job = Job(job_id="j", inputs={}, plan=[])
    _propagate_outputs(job, "tool_colabfold", _FakeResult())
    assert job.inputs == {}


# ---------------------------------------------------------------------------
# _run_one_job — optional (supplemental) step failure isolation (B5)
# ---------------------------------------------------------------------------

from preflight.jobs import JobStep
from tools.batch import _run_one_job


def test_optional_step_failure_does_not_fail_job(tmp_path, monkeypatch):
    import tools.batch as batch

    def fake_resolve(tool_id):
        if tool_id == "tool_extract_colabfold":
            return lambda **kw: ["ok"]
        if tool_id == "tool_freesasa":
            def boom(**kw):
                raise RuntimeError("freesasa exploded")
            return boom
        raise AssertionError(tool_id)

    monkeypatch.setattr(batch, "resolve_wrapper", fake_resolve)
    job = Job(
        job_id="j_opt",
        inputs={"data_aa_sequence": "MKTAY"},
        plan=[
            JobStep(tool_id="tool_extract_colabfold", input_bindings={"out_dir": "_job_dir"}),
            JobStep(tool_id="tool_freesasa", optional=True),
        ],
    )
    _run_one_job(job, tmp_path, abort_on_failure=False)
    assert job.status == "done"
    assert job.results["tool_extract_colabfold"] == ["ok"]
    assert "tool_freesasa" not in job.results  # failed optional step recorded nothing


def test_required_step_failure_fails_job(tmp_path, monkeypatch):
    import tools.batch as batch

    def fake_resolve(tool_id):
        def boom(**kw):
            raise RuntimeError("extract exploded")
        return boom

    monkeypatch.setattr(batch, "resolve_wrapper", fake_resolve)
    job = Job(
        job_id="j_req",
        inputs={"data_aa_sequence": "MKTAY"},
        plan=[JobStep(tool_id="tool_extract_colabfold", input_bindings={"out_dir": "_job_dir"})],
    )
    _run_one_job(job, tmp_path, abort_on_failure=False)
    assert job.status == "failed"
    assert "extract exploded" in job.error


def _metrics(model_id: str, mean_plddt: float, iptm: float, actifptm: float) -> list[ExtractedMetrics]:
    return [ExtractedMetrics(
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
        ranking_score=iptm,
    )]


def _done_job(job_id, target_id, kind, *, peptide_id=None, mean_plddt=80.0, iptm=0.9, actifptm=0.9, job_dir=None):
    meta = {"target_id": target_id, "kind": kind}
    if peptide_id is not None:
        meta["peptide_id"] = peptide_id
    j = Job(
        job_id=job_id,
        inputs={"data_aa_sequence": ["TARGET", "PEPTIDE"] if peptide_id else "TARGET"},
        plan=[],
        status="done",
        results={"tool_extract_colabfold": _metrics(job_id, mean_plddt, iptm, actifptm)},
        metadata=meta,
    )
    j.job_dir = job_dir
    return j


def _batch(jobs):
    return BatchResult(spec=None, roadmap=None, jobs=jobs)


def test_emit_reports_writes_one_panel_report_per_target(tmp_path: Path):
    jobs = [
        _done_job("t0__p0", "target_0", "binding_panel", peptide_id="peptide_0", iptm=0.9),
        _done_job("t0__p1", "target_0", "binding_panel", peptide_id="peptide_1", iptm=0.4),
    ]
    written = emit_reports(_batch(jobs), outputs_root=tmp_path, task_description="screen")
    panel = tmp_path / "target_0" / "panel_report.md"
    assert panel in written
    assert panel.exists()
    text = panel.read_text()
    assert "peptide_0" in text and "peptide_1" in text
    # higher-ipTM peptide ranks first
    assert text.index("| 1 | peptide_0") < text.index("| 2 | peptide_1")


def test_emit_reports_groups_targets_separately(tmp_path: Path):
    jobs = [
        _done_job("t0__p0", "target_0", "binding_panel", peptide_id="peptide_0"),
        _done_job("t1__p0", "target_1", "binding_panel", peptide_id="peptide_0"),
    ]
    written = emit_reports(_batch(jobs), outputs_root=tmp_path, task_description="screen")
    assert (tmp_path / "target_0" / "panel_report.md").exists()
    assert (tmp_path / "target_1" / "panel_report.md").exists()
    assert len(written) == 2


def test_emit_reports_monomer_writes_per_job_report(tmp_path: Path):
    jobs = [_done_job("mono_0", "target_0", "monomer")]
    written = emit_reports(_batch(jobs), outputs_root=tmp_path, task_description="fold")
    report = tmp_path / "mono_0" / "report.md"
    assert report in written
    assert report.exists()


def test_emit_reports_skips_failed_jobs(tmp_path: Path):
    j = _done_job("t0__p0", "target_0", "binding_panel", peptide_id="peptide_0")
    j.status = "failed"
    written = emit_reports(_batch([j]), outputs_root=tmp_path, task_description="screen")
    assert written == []


def test_build_panel_report_inputs_picks_best_model_by_composite():
    job = Job(
        job_id="j", inputs={}, plan=[], status="done",
        results={"tool_extract_colabfold": [
            ExtractedMetrics(model_id="rank2", backend="colabfold", pdb_path=Path("/tmp/a.pdb"),
                             mean_plddt=50.0, min_plddt=40, max_plddt=60, per_residue_plddt=[50],
                             pae=None, iptm=0.3, actifptm=0.3, ranking_score=0.3),
            ExtractedMetrics(model_id="rank1", backend="colabfold", pdb_path=Path("/tmp/b.pdb"),
                             mean_plddt=90.0, min_plddt=80, max_plddt=95, per_residue_plddt=[90],
                             pae=None, iptm=0.95, actifptm=0.95, ranking_score=0.95),
        ]},
        metadata={"target_id": "t", "peptide_id": "p0", "kind": "binding_panel"},
    )
    inp = build_panel_report_inputs([job], "t", "screen")
    assert len(inp.complexes) == 1
    assert inp.complexes[0].metrics.model_id == "rank1"


# ---------------------------------------------------------------------------
# build_report_inputs / build_panel_report_inputs — disorder/CIDER/hotspots
# ---------------------------------------------------------------------------

from tools.analyze import CIDERFeatures
from tools.batch import build_report_inputs
from tools.rosetta import ConfidentHotSpot


def _base_metrics() -> ExtractedMetrics:
    return ExtractedMetrics(
        model_id="r1", backend="colabfold", pdb_path=Path("/tmp/r1.pdb"),
        mean_plddt=80.0, min_plddt=70, max_plddt=90, per_residue_plddt=[80],
        pae=None, iptm=0.9, actifptm=0.9, ranking_score=0.9,
    )


def _cider_feat() -> CIDERFeatures:
    return CIDERFeatures(
        sequence="MKTAY", fcr=0.2, ncpr=0.1, kappa=0.15, omega=0.05,
        scd=-0.3, delta=0.0001, mean_hydropathy=0.35, ppii_propensity=0.3,
        isoelectric_point=8.1, phase_region=1,
    )


def _hotspot_feat() -> ConfidentHotSpot:
    return ConfidentHotSpot(
        rosetta_resnum=5, pdb_resnum=5, chain="A", resname="TRP",
        ddG=2.5, plddt=85.0, confident=True,
    )


class _FakeMetapredict:
    disorder_scores = [0.7, 0.8, 0.9]


def test_build_report_inputs_populates_iupred3():
    job = Job(
        job_id="j", inputs={}, plan=[], status="done",
        results={
            "tool_extract_colabfold": [_base_metrics()],
            "tool_iupred3": [0.3, 0.6, 0.8],
        },
        metadata={"target_id": "t", "kind": "binding"},
    )
    ri = build_report_inputs(job, "test")
    assert ri.disorder_iupred3 == [0.3, 0.6, 0.8]
    assert ri.disorder_metapredict is None


def test_build_report_inputs_populates_metapredict():
    job = Job(
        job_id="j", inputs={}, plan=[], status="done",
        results={
            "tool_extract_colabfold": [_base_metrics()],
            "tool_metapredict": _FakeMetapredict(),
        },
        metadata={"target_id": "t", "kind": "binding"},
    )
    ri = build_report_inputs(job, "test")
    assert ri.disorder_metapredict == [0.7, 0.8, 0.9]


def test_build_report_inputs_populates_cider():
    job = Job(
        job_id="j", inputs={}, plan=[], status="done",
        results={
            "tool_extract_colabfold": [_base_metrics()],
            "tool_localcider": _cider_feat(),
        },
        metadata={"target_id": "t", "kind": "binding"},
    )
    ri = build_report_inputs(job, "test")
    assert ri.cider is not None
    assert ri.cider.kappa == 0.15


def test_build_report_inputs_populates_hotspots():
    spots = [_hotspot_feat()]
    job = Job(
        job_id="j", inputs={}, plan=[], status="done",
        results={
            "tool_extract_colabfold": [_base_metrics()],
            "tool_rosetta_confidence_filter": spots,
        },
        metadata={"target_id": "t", "kind": "binding"},
    )
    ri = build_report_inputs(job, "test")
    assert ri.hotspots is spots


def test_build_panel_report_inputs_populates_disorder_and_cider():
    job = Job(
        job_id="j", inputs={}, plan=[], status="done",
        results={
            "tool_extract_colabfold": [_base_metrics()],
            "tool_iupred3": [0.4, 0.5],
            "tool_metapredict": _FakeMetapredict(),
            "tool_localcider": _cider_feat(),
            "tool_rosetta_confidence_filter": [_hotspot_feat()],
        },
        metadata={"target_id": "t", "peptide_id": "p0", "kind": "binding_panel"},
    )
    inp = build_panel_report_inputs([job], "t", "screen")
    e = inp.complexes[0]
    assert e.disorder_iupred3 == [0.4, 0.5]
    assert e.disorder_metapredict == [0.7, 0.8, 0.9]
    assert e.cider is not None and e.cider.phase_region == 1
    assert e.hotspots is not None and len(e.hotspots) == 1
