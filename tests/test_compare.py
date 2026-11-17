"""Tests for tools.compare — TM-score / Ca RMSD structural comparison.

Unit tests use minimal synthetic PDB strings so they don't require real
prediction/reference structure files.
"""

from __future__ import annotations

import json

import pytest

from tools.compare import ComparisonResult, compare, parse_ca_coords

_TWO_RES_TWO_CHAIN = """\
ATOM      1  CA  ALA A   1       1.000   2.000   3.000  1.00 82.50           C
ATOM      2  CA  GLY A   2       4.000   5.000   6.000  1.00 78.30           C
ATOM      3  CA  PRO B   1       7.000   8.000   9.000  1.00 25.00           C
END
"""

_IDENTICAL_TRIMER = """\
ATOM      1  CA  ALA A   1       0.000   0.000   0.000  1.00 90.00           C
ATOM      2  CA  GLY A   2       3.800   0.000   0.000  1.00 90.00           C
ATOM      3  CA  PRO A   3       7.600   0.000   0.000  1.00 90.00           C
END
"""


class TestParseCaCoords:
    def test_extracts_ca_only(self, tmp_path):
        p = tmp_path / "x.pdb"
        p.write_text(_TWO_RES_TWO_CHAIN)
        coords, seq = parse_ca_coords(p)
        # Defaults to the first chain encountered (A) — 2 residues, not 3.
        assert coords.shape == (2, 3)
        assert seq == "AG"

    def test_explicit_chain_selection(self, tmp_path):
        p = tmp_path / "x.pdb"
        p.write_text(_TWO_RES_TWO_CHAIN)
        coords, seq = parse_ca_coords(p, chain="B")
        assert coords.shape == (1, 3)
        assert seq == "P"

    def test_coords_match_atom_columns(self, tmp_path):
        p = tmp_path / "x.pdb"
        p.write_text(_TWO_RES_TWO_CHAIN)
        coords, _ = parse_ca_coords(p, chain="A")
        assert coords[0].tolist() == pytest.approx([1.0, 2.0, 3.0])
        assert coords[1].tolist() == pytest.approx([4.0, 5.0, 6.0])

    def test_duplicate_residue_number_not_double_counted(self, tmp_path):
        # Two ATOM lines sharing a residue number (e.g. altloc) should count once.
        p = tmp_path / "x.pdb"
        p.write_text(
            "ATOM      1  CA  ALA A   1       1.000   2.000   3.000  1.00 82.50           C\n"
            "ATOM      2  CA  ALA A   1       1.100   2.100   3.100  1.00 82.50           C\n"
            "END\n"
        )
        coords, seq = parse_ca_coords(p, chain="A")
        assert coords.shape == (1, 3)
        assert seq == "A"


class TestCompare:
    def test_identical_structures_near_perfect_tmscore(self, tmp_path):
        pred = tmp_path / "pred.pdb"
        ref = tmp_path / "ref.pdb"
        pred.write_text(_IDENTICAL_TRIMER)
        ref.write_text(_IDENTICAL_TRIMER)

        result = compare(pred, ref)

        assert isinstance(result, ComparisonResult)
        assert result.n_pred == result.n_ref == 3
        assert result.tmscore_ref == pytest.approx(1.0, abs=1e-3)
        assert result.rmsd_ca == pytest.approx(0.0, abs=1e-3)

    def test_no_score_json_leaves_plddt_fields_none(self, tmp_path):
        pred = tmp_path / "pred.pdb"
        ref = tmp_path / "ref.pdb"
        pred.write_text(_IDENTICAL_TRIMER)
        ref.write_text(_IDENTICAL_TRIMER)

        result = compare(pred, ref)

        assert result.mean_plddt is None
        assert result.per_residue_plddt is None
        assert result.ptm is None

    def test_score_json_populates_plddt_and_ptm(self, tmp_path):
        pred = tmp_path / "pred.pdb"
        ref = tmp_path / "ref.pdb"
        pred.write_text(_IDENTICAL_TRIMER)
        ref.write_text(_IDENTICAL_TRIMER)
        scores = tmp_path / "scores.json"
        scores.write_text(json.dumps({"plddt": [80.0, 90.0, 100.0], "ptm": 0.77}))

        result = compare(pred, ref, score_json=scores)

        assert result.mean_plddt == pytest.approx(90.0)
        assert result.per_residue_plddt == [80.0, 90.0, 100.0]
        assert result.ptm == pytest.approx(0.77)

    def test_missing_score_json_path_is_ignored(self, tmp_path):
        pred = tmp_path / "pred.pdb"
        ref = tmp_path / "ref.pdb"
        pred.write_text(_IDENTICAL_TRIMER)
        ref.write_text(_IDENTICAL_TRIMER)

        result = compare(pred, ref, score_json=tmp_path / "does_not_exist.json")

        assert result.mean_plddt is None
