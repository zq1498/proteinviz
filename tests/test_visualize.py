"""Tests for tools.visualize.

Unit tests use a minimal synthetic PDB string so they don't require real output
files. Integration tests (marked with the real_pdb mark) exercise the full
render path against actual ColabFold PDB outputs and are skipped if those
files aren't present.
"""

from __future__ import annotations

import pytest
from pathlib import Path

from tools.visualize import (
    _PEPTIDE_COLOR,
    _PY3DMOL_CDN,
    _PLDDT_COLORS,
    _escape_pdb,
    render_py3dmol,
    render_complex_py3dmol,
    write_html,
    write_complex_html,
    write_html_from_metrics,
)


# ---------------------------------------------------------------------------
# Minimal PDB fixture (two residues, two chains — enough to parse)
# ---------------------------------------------------------------------------

_MINI_PDB = """\
ATOM      1  CA  ALA A   1       1.000   2.000   3.000  1.00 82.50           C
ATOM      2  CA  GLY B   1       4.000   5.000   6.000  1.00 45.00           C
ATOM      3  CA  PRO C   1       7.000   8.000   9.000  1.00 25.00           C
END
"""

_MONOMER_PDB = """\
ATOM      1  CA  ALA A   1       1.000   2.000   3.000  1.00 82.50           C
ATOM      2  CA  GLY A   2       4.000   5.000   6.000  1.00 78.30           C
END
"""

# Real complex PDB (ColabFold multimer output for the VEGF selectivity screen).
# Tests that use this are skipped if the file doesn't exist.
_REAL_COMPLEX_PDB = Path(
    "jobs/vegf_selectivity_screen/vegf165/rank262/output/"
    "vegf165_homodimer_rank262_unrelaxed_rank_001_alphafold2_multimer_v3_model_1_seed_000.pdb"
)
_REAL_MONOMER_PDB = Path(
    "jobs/vegf_monomer_out/"
    "vegf_monomer_unrelaxed_rank_001_alphafold2_ptm_model_5_seed_000.pdb"
)


# ---------------------------------------------------------------------------
# _escape_pdb
# ---------------------------------------------------------------------------

def test_escape_pdb_backtick():
    assert _escape_pdb("foo`bar") == "foo\\`bar"


def test_escape_pdb_dollar():
    assert _escape_pdb("foo$bar") == "foo\\$bar"


def test_escape_pdb_backslash():
    assert _escape_pdb("foo\\bar") == "foo\\\\bar"


def test_escape_pdb_clean_string_unchanged():
    pdb = "ATOM      1  CA  ALA A   1       1.000   2.000   3.000\n"
    assert _escape_pdb(pdb) == pdb


# ---------------------------------------------------------------------------
# render_py3dmol — HTML structure
# ---------------------------------------------------------------------------

def test_render_py3dmol_contains_cdn(tmp_path):
    p = tmp_path / "mini.pdb"
    p.write_text(_MINI_PDB)
    html = render_py3dmol(p)
    assert _PY3DMOL_CDN in html


def test_render_py3dmol_plddt_embeds_gradient_colors(tmp_path):
    p = tmp_path / "mini.pdb"
    p.write_text(_MINI_PDB)
    html = render_py3dmol(p, color_by="plddt")
    for color in _PLDDT_COLORS:
        assert color in html


def test_render_py3dmol_plddt_includes_legend(tmp_path):
    p = tmp_path / "mini.pdb"
    p.write_text(_MINI_PDB)
    html = render_py3dmol(p, color_by="plddt")
    assert "pLDDT confidence" in html


def test_render_py3dmol_chain_uses_chainhetatm(tmp_path):
    p = tmp_path / "mini.pdb"
    p.write_text(_MINI_PDB)
    html = render_py3dmol(p, color_by="chain")
    assert "chainHetatm" in html
    assert "pLDDT confidence" not in html


def test_render_py3dmol_spectrum_uses_spectrum_keyword(tmp_path):
    p = tmp_path / "mini.pdb"
    p.write_text(_MINI_PDB)
    html = render_py3dmol(p, color_by="spectrum")
    assert '"spectrum"' in html


def test_render_py3dmol_pdb_data_embedded(tmp_path):
    p = tmp_path / "mini.pdb"
    p.write_text(_MINI_PDB)
    html = render_py3dmol(p)
    # A distinctive atom coordinate should be embedded in the JS template literal
    assert "82.50" in html


def test_render_py3dmol_title_is_stem(tmp_path):
    p = tmp_path / "mypdb.pdb"
    p.write_text(_MINI_PDB)
    html = render_py3dmol(p)
    assert "<title>mypdb</title>" in html


# ---------------------------------------------------------------------------
# render_complex_py3dmol
# ---------------------------------------------------------------------------

def test_render_complex_embeds_pdb_data(tmp_path):
    p = tmp_path / "complex.pdb"
    p.write_text(_MINI_PDB)
    html = render_complex_py3dmol(p)
    assert "82.50" in html


def test_render_complex_has_plddt_gradient_for_target(tmp_path):
    p = tmp_path / "complex.pdb"
    p.write_text(_MINI_PDB)
    html = render_complex_py3dmol(p)
    for color in _PLDDT_COLORS:
        assert color in html


def test_render_complex_has_peptide_color(tmp_path):
    p = tmp_path / "complex.pdb"
    p.write_text(_MINI_PDB)
    html = render_complex_py3dmol(p)
    assert _PEPTIDE_COLOR in html


def test_render_complex_selects_peptide_chain(tmp_path):
    p = tmp_path / "complex.pdb"
    p.write_text(_MINI_PDB)
    html = render_complex_py3dmol(p, peptide_chain="C")
    assert 'chain: "C"' in html


def test_render_complex_custom_peptide_chain(tmp_path):
    p = tmp_path / "complex.pdb"
    p.write_text(_MINI_PDB)
    html = render_complex_py3dmol(p, peptide_chain="B")
    assert 'chain: "B"' in html


def test_render_complex_legend_names_chain(tmp_path):
    p = tmp_path / "complex.pdb"
    p.write_text(_MINI_PDB)
    html = render_complex_py3dmol(p, peptide_chain="C")
    assert "chain C" in html


def test_render_complex_includes_stick_style_for_peptide(tmp_path):
    p = tmp_path / "complex.pdb"
    p.write_text(_MINI_PDB)
    html = render_complex_py3dmol(p)
    assert "stick" in html


# ---------------------------------------------------------------------------
# write_html / write_complex_html
# ---------------------------------------------------------------------------

def test_write_html_creates_file(tmp_path):
    pdb = tmp_path / "mono.pdb"
    out = tmp_path / "mono.html"
    pdb.write_text(_MONOMER_PDB)
    result = write_html(pdb, out)
    assert result == out
    assert out.exists()
    assert out.stat().st_size > 0


def test_write_html_creates_parent_dirs(tmp_path):
    pdb = tmp_path / "mono.pdb"
    out = tmp_path / "nested" / "deep" / "mono.html"
    pdb.write_text(_MONOMER_PDB)
    write_html(pdb, out)
    assert out.exists()


def test_write_complex_html_creates_file(tmp_path):
    pdb = tmp_path / "complex.pdb"
    out = tmp_path / "complex.html"
    pdb.write_text(_MINI_PDB)
    result = write_complex_html(pdb, out)
    assert result == out
    assert out.exists()


def test_write_complex_html_content_has_peptide_chain(tmp_path):
    pdb = tmp_path / "complex.pdb"
    out = tmp_path / "complex.html"
    pdb.write_text(_MINI_PDB)
    write_complex_html(pdb, out, peptide_chain="C")
    assert 'chain: "C"' in out.read_text()


# ---------------------------------------------------------------------------
# write_html_from_metrics
# ---------------------------------------------------------------------------

def _fake_metrics(model_id: str, pdb_path: Path):
    from tools.extract import ExtractedMetrics
    return ExtractedMetrics(
        model_id=model_id,
        backend="colabfold",
        pdb_path=pdb_path,
        mean_plddt=75.0,
        min_plddt=20.0,
        max_plddt=95.0,
    )


def test_write_html_from_metrics_filename_is_model_id(tmp_path):
    pdb = tmp_path / "rank_001.pdb"
    pdb.write_text(_MONOMER_PDB)
    m = _fake_metrics("rank_001_alphafold2_ptm", pdb)
    out = write_html_from_metrics(m, tmp_path)
    assert out.name == "rank_001_alphafold2_ptm.html"
    assert out.exists()


def test_write_html_from_metrics_no_peptide_chain_uses_standard_viewer(tmp_path):
    pdb = tmp_path / "rank_001.pdb"
    pdb.write_text(_MONOMER_PDB)
    m = _fake_metrics("rank_001", pdb)
    out = write_html_from_metrics(m, tmp_path)
    content = out.read_text()
    # Standard viewer: no chain-specific selector, pLDDT gradient present
    assert 'chain: "C"' not in content
    assert "gradient" in content


def test_write_html_from_metrics_with_peptide_chain_uses_complex_viewer(tmp_path):
    pdb = tmp_path / "rank_001.pdb"
    pdb.write_text(_MINI_PDB)
    m = _fake_metrics("rank_001", pdb)
    out = write_html_from_metrics(m, tmp_path, peptide_chain="C")
    content = out.read_text()
    assert 'chain: "C"' in content
    assert _PEPTIDE_COLOR in content


# ---------------------------------------------------------------------------
# Integration tests against real ColabFold outputs (skipped if absent)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _REAL_MONOMER_PDB.exists(), reason="real monomer PDB not present")
def test_render_py3dmol_real_monomer():
    html = render_py3dmol(_REAL_MONOMER_PDB)
    assert "<!DOCTYPE html>" in html
    assert _PY3DMOL_CDN in html
    # pLDDT B-factors from a real ColabFold output are non-trivial floats
    assert "ATOM" in html


@pytest.mark.skipif(not _REAL_COMPLEX_PDB.exists(), reason="real complex PDB not present")
def test_render_complex_py3dmol_real_complex():
    html = render_complex_py3dmol(_REAL_COMPLEX_PDB, peptide_chain="C")
    assert "<!DOCTYPE html>" in html
    assert _PEPTIDE_COLOR in html
    assert 'chain: "C"' in html


@pytest.mark.skipif(not _REAL_COMPLEX_PDB.exists(), reason="real complex PDB not present")
def test_write_complex_html_real_complex(tmp_path):
    out = tmp_path / "vegf165_rank262.html"
    write_complex_html(_REAL_COMPLEX_PDB, out, peptide_chain="C")
    assert out.exists()
    assert out.stat().st_size > 100_000  # complex PDB embeds many ATOM records
