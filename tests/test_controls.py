"""Tests for tools.controls — scrambled control peptide generation."""

from __future__ import annotations

from tools.controls import (
    is_control_id,
    make_scrambled_controls,
    parent_id_of,
)


def _composition(seq: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for aa in seq:
        counts[aa] = counts.get(aa, 0) + 1
    return counts


class TestMakeScrambledControls:
    def test_returns_correct_count(self):
        peptides = [("p1", "ACDEF"), ("p2", "GHIKL")]
        controls = make_scrambled_controls(peptides, n_per_peptide=2)
        assert len(controls) == 4

    def test_id_pattern(self):
        controls = make_scrambled_controls([("pep_01", "ACDEF")], n_per_peptide=3)
        ids = [pid for pid, _ in controls]
        assert ids == ["pep_01__scram0", "pep_01__scram1", "pep_01__scram2"]

    def test_composition_preserved(self):
        peptides = [("p1", "ACDEFGHIKLMNPQRST"), ("p2", "PPPPRRKKK")]
        for n in (1, 3):
            controls = make_scrambled_controls(peptides, n_per_peptide=n)
            for ctrl_id, ctrl_seq in controls:
                parent_id = parent_id_of(ctrl_id)
                orig_seq = next(s for oid, s in peptides if oid == parent_id)
                assert _composition(ctrl_seq) == _composition(orig_seq), (
                    f"{ctrl_id}: composition mismatch — orig {_composition(orig_seq)} != "
                    f"ctrl {_composition(ctrl_seq)}"
                )

    def test_length_preserved(self):
        peptides = [("p1", "MKTAYIAKQ"), ("p2", "AA")]
        controls = make_scrambled_controls(peptides)
        for ctrl_id, ctrl_seq in controls:
            parent_id = parent_id_of(ctrl_id)
            orig_seq = next(s for oid, s in peptides if oid == parent_id)
            assert len(ctrl_seq) == len(orig_seq)

    def test_determinism(self):
        peptides = [("p1", "ACDEFGHIKLM")]
        c1 = make_scrambled_controls(peptides, seed=42)
        c2 = make_scrambled_controls(peptides, seed=42)
        assert c1 == c2

    def test_different_seeds_differ(self):
        peptides = [("p1", "ACDEFGHIKLMNPQRST")]
        c0 = make_scrambled_controls(peptides, seed=0)
        c1 = make_scrambled_controls(peptides, seed=1)
        # With a long enough sequence, two seeds almost certainly differ
        assert c0[0][1] != c1[0][1]

    def test_preserve_termini(self):
        seq = "MKTAYIAKQRST"
        controls = make_scrambled_controls(
            [("p1", seq)], n_per_peptide=10, preserve_termini=True
        )
        for _, ctrl_seq in controls:
            assert ctrl_seq[0] == seq[0], "first residue changed"
            assert ctrl_seq[-1] == seq[-1], "last residue changed"

    def test_preserve_termini_false_allows_permutation(self):
        # With preserve_termini=False, first/last residues can move.
        # This test just verifies the flag doesn't crash and output is valid.
        seq = "MKTAYIAKQRST"
        controls = make_scrambled_controls([("p1", seq)], n_per_peptide=5)
        for _, ctrl_seq in controls:
            assert _composition(ctrl_seq) == _composition(seq)

    def test_single_residue_sequence(self):
        controls = make_scrambled_controls([("p1", "A")])
        assert controls == [("p1__scram0", "A")]

    def test_two_residue_sequence_preserve_termini(self):
        # preserve_termini on a 2-residue seq: nothing to shuffle, returns original
        controls = make_scrambled_controls(
            [("p1", "AC")], n_per_peptide=5, preserve_termini=True
        )
        for _, seq in controls:
            assert sorted(seq) == sorted("AC")

    def test_empty_peptide_list(self):
        assert make_scrambled_controls([]) == []


class TestIsControlId:
    def test_positive(self):
        assert is_control_id("pep_01__scram0")
        assert is_control_id("p__scram99")
        assert is_control_id("some__complex__id__scram0")

    def test_negative(self):
        assert not is_control_id("pep_01")
        assert not is_control_id("scram0")
        assert not is_control_id("pep_01__scram")       # no digit
        assert not is_control_id("pep_01__scramX")      # non-digit suffix
        assert not is_control_id("__scram0")            # empty parent


class TestTestPeptideOf:
    def test_returns_parent(self):
        assert parent_id_of("pep_01__scram0") == "pep_01"
        assert parent_id_of("my__peptide__scram3") == "my__peptide"

    def test_returns_none_for_non_control(self):
        assert parent_id_of("pep_01") is None
        assert parent_id_of("__scram0") is None
        assert parent_id_of("") is None
