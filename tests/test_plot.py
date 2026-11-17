"""Tests for tools.plot — pLDDT profile, PAE heatmap, model comparison chart.

All plotting is headless (Agg backend); tests assert on emitted file existence
and basic sanity of inputs rather than pixel content.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tools.extract import ExtractedMetrics
from tools.plot import plot_model_comparison, plot_pae_heatmap, plot_plddt_profile


def _make_metric(**overrides) -> ExtractedMetrics:
    base = dict(
        model_id="rank_001_test",
        backend="colabfold",
        pdb_path=Path("/tmp/x.pdb"),
        mean_plddt=80.0,
        min_plddt=40.0,
        max_plddt=95.0,
        per_residue_plddt=[30.0, 55.0, 75.0, 92.0, 98.0],
        pae=None,
        iptm=0.6,
        actifptm=0.9,
        ranking_score=0.7,
    )
    base.update(overrides)
    return ExtractedMetrics(**base)


class TestPlotPlddtProfile:
    def test_writes_png(self, tmp_path):
        m = _make_metric()
        out = tmp_path / "plddt.png"
        result = plot_plddt_profile(m, out)
        assert result == out
        assert out.exists()
        assert out.stat().st_size > 0

    def test_creates_parent_dirs(self, tmp_path):
        m = _make_metric()
        out = tmp_path / "nested" / "dir" / "plddt.png"
        plot_plddt_profile(m, out)
        assert out.exists()


class TestPlotPaeHeatmap:
    def test_writes_png(self, tmp_path):
        m = _make_metric(pae=[[0.0, 5.0], [5.0, 0.0]])
        out = tmp_path / "pae.png"
        result = plot_pae_heatmap(m, out)
        assert result == out
        assert out.exists()

    def test_raises_without_pae(self, tmp_path):
        m = _make_metric(pae=None)
        with pytest.raises(ValueError, match="No PAE data"):
            plot_pae_heatmap(m, tmp_path / "pae.png")


class TestPlotModelComparison:
    def test_writes_png_without_tmscore(self, tmp_path):
        metrics = [_make_metric(model_id=f"rank_{i}") for i in range(3)]
        out = tmp_path / "comparison.png"
        result = plot_model_comparison(metrics, out)
        assert result == out
        assert out.exists()

    def test_writes_png_with_tmscore_overlay(self, tmp_path):
        metrics = [_make_metric(model_id=f"rank_{i}") for i in range(3)]
        out = tmp_path / "comparison.png"
        result = plot_model_comparison(metrics, out, tmscore_vs_crystal=0.82)
        assert result == out
        assert out.exists()

    def test_handles_missing_ranking_score(self, tmp_path):
        metrics = [_make_metric(ranking_score=None)]
        out = tmp_path / "comparison.png"
        plot_model_comparison(metrics, out)
        assert out.exists()
