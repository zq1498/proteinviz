"""Tests for tools.fingerprint — contact fingerprinting and epitope clustering."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from tools.analyze import StructureFeatures
from tools.fingerprint import (
    ContactFingerprint,
    _to_vector,
    cluster_fingerprints,
    jaccard_matrix,
    make_fingerprint,
)


def _structure(interface_residues: list[tuple[str, int]]) -> StructureFeatures:
    return StructureFeatures(
        pdb_path=Path("/tmp/fake.pdb"),
        sasa_total=1000.0,
        interface_residues=interface_residues,
    )


# ---------------------------------------------------------------------------
# _to_vector
# ---------------------------------------------------------------------------

def test_to_vector_sets_correct_bits():
    v = _to_vector([1, 3, 5], universe_size=6)
    assert v.dtype == bool
    assert list(v) == [True, False, True, False, True, False]


def test_to_vector_clips_out_of_range():
    # res_id 7 exceeds universe_size=5; should be silently ignored
    v = _to_vector([1, 7], universe_size=5)
    assert v[0] is np.bool_(True)
    assert v.sum() == 1


def test_to_vector_empty_res_ids():
    v = _to_vector([], universe_size=4)
    assert not v.any()


# ---------------------------------------------------------------------------
# make_fingerprint
# ---------------------------------------------------------------------------

def test_make_fingerprint_basic():
    s = _structure([("A", 10), ("A", 20), ("B", 5)])
    fp = make_fingerprint("pep1", s)
    assert fp is not None
    assert fp.peptide_id == "pep1"
    assert fp.residue_ids == [10, 20]
    assert fp.vector[9] and fp.vector[19]   # 1-indexed → 0-indexed


def test_make_fingerprint_returns_none_when_no_interface_residues():
    s = _structure([])
    assert make_fingerprint("pep1", s) is None


def test_make_fingerprint_returns_none_when_no_target_chain_contacts():
    # Only chain B contacts; default target_chains=("A",) → nothing
    s = _structure([("B", 5), ("B", 10)])
    assert make_fingerprint("pep1", s) is None


def test_make_fingerprint_respects_universe_size():
    s = _structure([("A", 3)])
    fp = make_fingerprint("pep1", s, universe_size=10)
    assert len(fp.vector) == 10
    assert fp.vector[2]   # residue 3 → index 2


def test_make_fingerprint_deduplicates_residues():
    s = _structure([("A", 5), ("A", 5), ("A", 5)])
    fp = make_fingerprint("pep1", s)
    assert fp.residue_ids == [5]
    assert fp.vector.sum() == 1


# ---------------------------------------------------------------------------
# jaccard_matrix
# ---------------------------------------------------------------------------

def _fp(pid: str, res_ids: list[int], size: int) -> ContactFingerprint:
    return ContactFingerprint(
        peptide_id=pid,
        target_chains=("A",),
        residue_ids=res_ids,
        vector=_to_vector(res_ids, size),
    )


def test_jaccard_matrix_identical_fingerprints():
    fps = [_fp("a", [1, 2, 3], 5), _fp("b", [1, 2, 3], 5)]
    mat = jaccard_matrix(fps)
    assert mat[0, 0] == pytest.approx(1.0)
    assert mat[0, 1] == pytest.approx(1.0)


def test_jaccard_matrix_disjoint_fingerprints():
    fps = [_fp("a", [1, 2], 5), _fp("b", [4, 5], 5)]
    mat = jaccard_matrix(fps)
    assert mat[0, 1] == pytest.approx(0.0)


def test_jaccard_matrix_partial_overlap():
    # |intersection| = 1 ({3}), |union| = 3 ({1,2,3,4} minus redundant = {1,2,3,4} → 4)
    # Actually: a={1,2,3}, b={3,4} → intersection={3}, union={1,2,3,4} → 1/4 = 0.25
    fps = [_fp("a", [1, 2, 3], 5), _fp("b", [3, 4], 5)]
    mat = jaccard_matrix(fps)
    assert mat[0, 1] == pytest.approx(0.25)


def test_jaccard_matrix_is_symmetric():
    fps = [_fp("a", [1, 2], 5), _fp("b", [2, 3], 5), _fp("c", [4, 5], 5)]
    mat = jaccard_matrix(fps)
    assert np.allclose(mat, mat.T)


# ---------------------------------------------------------------------------
# cluster_fingerprints
# ---------------------------------------------------------------------------

def test_cluster_fingerprints_empty():
    assert cluster_fingerprints([]) == {}


def test_cluster_fingerprints_single():
    result = cluster_fingerprints([_fp("solo", [1, 2], 5)])
    assert result == {"solo": "Epitope-1"}


def test_cluster_fingerprints_two_identical_go_same_cluster():
    fps = [_fp("a", [1, 2, 3], 5), _fp("b", [1, 2, 3], 5)]
    result = cluster_fingerprints(fps, threshold=0.3)
    assert result["a"] == result["b"]


def test_cluster_fingerprints_two_disjoint_go_different_clusters():
    fps = [_fp("a", [1, 2], 5), _fp("b", [4, 5], 5)]
    result = cluster_fingerprints(fps, threshold=0.3)
    assert result["a"] != result["b"]


def test_cluster_fingerprints_labels_are_strings():
    fps = [_fp("a", [1], 3), _fp("b", [2], 3), _fp("c", [3], 3)]
    result = cluster_fingerprints(fps)
    for v in result.values():
        assert isinstance(v, str)
        assert v.startswith("Epitope-")
