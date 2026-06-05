"""Tests for tools.predict utility functions."""

from __future__ import annotations

from pathlib import Path

from tools.predict import write_fasta


def test_write_fasta_creates_file_with_correct_format(tmp_path: Path):
    out = tmp_path / "x.fasta"
    write_fasta("MKTAY", "myseq", out)
    assert out.read_text() == ">myseq\nMKTAY\n"


def test_write_fasta_creates_parent_dirs(tmp_path: Path):
    out = tmp_path / "deep" / "nested" / "x.fasta"
    write_fasta("MKT", "n", out)
    assert out.exists()
