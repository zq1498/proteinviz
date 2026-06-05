"""Tests for tools.extract.

Uses jobs/smoke_test as a real ColabFold output fixture (5 ranked models).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.extract import (
    DEFAULT_SCORE_WEIGHTS,
    ExtractedMetrics,
    _parse_pdb_chain_plddt,
    composite_score,
    extract_colabfold,
)


def test_default_weights_sum_to_one():
    total = sum(DEFAULT_SCORE_WEIGHTS.values())
    assert abs(total - 1.0) < 1e-9, f"DEFAULT_SCORE_WEIGHTS sum to {total}, expected 1.0"


def test_default_weight_keys_present():
    for k in ("primary", "mean_plddt", "interface_plddt"):
        assert k in DEFAULT_SCORE_WEIGHTS


def test_extract_colabfold_returns_five_models(smoke_test_dir: Path):
    metrics = extract_colabfold(smoke_test_dir)
    assert len(metrics) == 5, f"expected 5 ranked models, got {len(metrics)}"


def test_extract_colabfold_models_are_well_formed(smoke_test_dir: Path):
    metrics = extract_colabfold(smoke_test_dir)
    for m in metrics:
        assert m.backend == "colabfold"
        assert m.model_id.startswith("rank_"), f"unexpected model_id: {m.model_id}"
        assert m.pdb_path.exists(), f"PDB does not exist: {m.pdb_path}"
        assert m.per_residue_plddt, "per-residue pLDDT should not be empty"
        assert 0.0 <= m.min_plddt <= m.mean_plddt <= m.max_plddt <= 100.0
        # ColabFold ptm should be present in score JSON
        assert m.ranking_score is not None


def test_extract_colabfold_raises_on_empty_dir(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        extract_colabfold(tmp_path)


def _make_metric(**overrides) -> ExtractedMetrics:
    base = dict(
        model_id="rank_001_test",
        backend="colabfold",
        pdb_path=Path("/tmp/x.pdb"),
        mean_plddt=80.0,
        min_plddt=40.0,
        max_plddt=95.0,
        per_residue_plddt=[80.0],
        pae=None,
        iptm=0.6,
        actifptm=0.9,
        ranking_score=0.7,
    )
    base.update(overrides)
    return ExtractedMetrics(**base)


def test_composite_score_uses_actifptm_when_present():
    m = _make_metric(iptm=0.6, actifptm=0.9, mean_plddt=80.0)
    # 0.5*0.9 + 0.3*0.80 = 0.45 + 0.24 = 0.69
    score = composite_score(m)
    assert abs(score - 0.69) < 1e-9


def test_composite_score_falls_back_to_iptm():
    m = _make_metric(iptm=0.6, actifptm=None, mean_plddt=80.0)
    # 0.5*0.6 + 0.3*0.80 = 0.30 + 0.24 = 0.54
    score = composite_score(m)
    assert abs(score - 0.54) < 1e-9


def test_composite_score_handles_missing_iptm_and_actifptm():
    # Falls back to ranking_score (ptm=0.7): 0.5*0.7 + 0.3*0.80 = 0.59
    m = _make_metric(iptm=None, actifptm=None, mean_plddt=80.0)
    score = composite_score(m)
    assert abs(score - 0.59) < 1e-9


def test_composite_score_includes_interface_plddt_when_supplied():
    m = _make_metric(iptm=0.6, actifptm=0.9, mean_plddt=80.0)
    # base 0.69 + 0.2 * (70/100) = 0.69 + 0.14 = 0.83
    score = composite_score(m, interface_plddt=70.0)
    assert abs(score - 0.83) < 1e-9


def test_composite_score_respects_custom_weights():
    m = _make_metric(iptm=0.5, actifptm=None, mean_plddt=100.0)
    weights = {"primary": 1.0, "mean_plddt": 0.0, "interface_plddt": 0.0}
    score = composite_score(m, weights=weights)
    assert abs(score - 0.5) < 1e-9


def test_composite_score_uses_actifptm_when_two_chains():
    m = _make_metric(iptm=0.55, actifptm=0.97, mean_plddt=74.0,
                     per_chain_plddt={"A": 76.0, "C": 25.0})
    # n_chains=2 → actifpTM used: 0.5*0.97 + 0.3*0.74 = 0.485 + 0.222 = 0.707
    score = composite_score(m)
    assert abs(score - (0.5 * 0.97 + 0.3 * 0.74)) < 1e-9


def test_composite_score_demotes_actifptm_for_three_chain_complex():
    # Simulates homodimer + peptide: actifpTM inflated by dimer interface.
    # With n_chains=3, ipTM is used as primary instead.
    m = _make_metric(iptm=0.55, actifptm=0.97, mean_plddt=74.0,
                     per_chain_plddt={"A": 76.0, "B": 75.5, "C": 25.0})
    # n_chains=3 → ipTM used: 0.5*0.55 + 0.3*0.74 = 0.275 + 0.222 = 0.497
    score = composite_score(m)
    assert abs(score - (0.5 * 0.55 + 0.3 * 0.74)) < 1e-9


def test_composite_score_three_chain_is_lower_than_two_chain():
    # Same raw metrics; 3-chain demotion should yield a lower composite score.
    two_chain = _make_metric(iptm=0.55, actifptm=0.97, mean_plddt=74.0,
                             per_chain_plddt={"A": 76.0, "C": 25.0})
    three_chain = _make_metric(iptm=0.55, actifptm=0.97, mean_plddt=74.0,
                               per_chain_plddt={"A": 76.0, "B": 75.5, "C": 25.0})
    assert composite_score(three_chain) < composite_score(two_chain)


def test_parse_pdb_chain_plddt_reads_bfactor_by_chain(tmp_path):
    pdb = tmp_path / "test.pdb"
    pdb.write_text(
        "ATOM      1  CA  ALA A   1       1.000   1.000   1.000  1.00 80.00           C\n"
        "ATOM      2  CA  ALA A   2       2.000   2.000   2.000  1.00 90.00           C\n"
        "ATOM      3  CA  ALA B   1       3.000   3.000   3.000  1.00 30.00           C\n"
    )
    result = _parse_pdb_chain_plddt(pdb)
    assert result == {"A": 85.0, "B": 30.0}


def test_parse_pdb_chain_plddt_returns_empty_when_file_missing():
    result = _parse_pdb_chain_plddt(Path("/nonexistent/file.pdb"))
    assert result == {}
