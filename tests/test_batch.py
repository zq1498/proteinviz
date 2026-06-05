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

def test_ensure_fasta_monomer_creates_file(tmp_path: Path):
    inputs = {"data_aa_sequence": "MKTAY"}
    _ensure_fasta(inputs, tmp_path)
    assert (tmp_path / "input.fasta").exists()


def test_ensure_fasta_monomer_correct_format(tmp_path: Path):
    inputs = {"data_aa_sequence": "MKTAY"}
    _ensure_fasta(inputs, tmp_path)
    content = (tmp_path / "input.fasta").read_text()
    assert content == ">query\nMKTAY\n"


def test_ensure_fasta_monomer_sets_fasta_key(tmp_path: Path):
    inputs = {"data_aa_sequence": "MKTAY"}
    result = _ensure_fasta(inputs, tmp_path)
    assert result["data_fasta_file"] == tmp_path / "input.fasta"


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
