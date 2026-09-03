"""Tests for tools._a3m — canonical A3M parsing and paired-A3M construction."""

from __future__ import annotations

import pytest

from tools._a3m import build_paired_a3m, parse_a3m


def _write_a3m(path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n")


class TestParseA3M:
    def test_query_seq_and_rows(self, tmp_path):
        p = tmp_path / "target.a3m"
        _write_a3m(p, [
            ">query",
            "ACDEFGHIKL",
            ">UniRef100_hit1",
            "ACDE--HIKL",
            ">UniRef100_hit2",
            "-CDEFGH-KL",
        ])
        query_seq, rows = parse_a3m(p)
        assert query_seq == "ACDEFGHIKL"
        assert rows == [
            (">UniRef100_hit1", "ACDE--HIKL"),
            (">UniRef100_hit2", "-CDEFGH-KL"),
        ]

    def test_multiline_sequence_rows_are_joined(self, tmp_path):
        p = tmp_path / "wrapped.a3m"
        _write_a3m(p, [
            ">query",
            "ACDE",
            "FGHIKL",
            ">hit1",
            "ACDEFG",
            "HIKL",
        ])
        query_seq, rows = parse_a3m(p)
        assert query_seq == "ACDEFGHIKL"
        assert rows == [(">hit1", "ACDEFGHIKL")]

    def test_chain_break_hash_lines_are_skipped(self, tmp_path):
        p = tmp_path / "with_hash.a3m"
        _write_a3m(p, [
            ">query",
            "ACDE",
            "#",
            ">hit1",
            "ACDE",
        ])
        query_seq, rows = parse_a3m(p)
        assert query_seq == "ACDE"
        assert rows == [(">hit1", "ACDE")]

    def test_no_leading_header_raises(self, tmp_path):
        p = tmp_path / "bad.a3m"
        _write_a3m(p, ["ACDEFGHIKL"])
        with pytest.raises(ValueError, match="no leading header"):
            parse_a3m(p)

    def test_empty_file_raises(self, tmp_path):
        p = tmp_path / "empty.a3m"
        p.write_text("")
        with pytest.raises(ValueError):
            parse_a3m(p)


class TestBuildPairedA3M:
    def _target_a3m(self, tmp_path):
        p = tmp_path / "target.a3m"
        _write_a3m(p, [
            ">query",
            "ACDEFGH",
            ">UniRef100_hit1",
            "ACDE-GH",
        ])
        return p

    def test_writes_expected_structure(self, tmp_path):
        target_a3m = self._target_a3m(tmp_path)
        out_dir = tmp_path / "out"
        out_path = build_paired_a3m("MKT", "pep1", target_a3m, out_dir)

        assert out_path.exists()
        lines = out_path.read_text().splitlines()

        assert lines[0] == "#7,3\t1,1"
        assert lines[1] == ">target_pep1"
        assert lines[2] == "ACDEFGHMKT"
        assert lines[3] == ">UniRef100_hit1"
        assert lines[4] == "ACDE-GH"
        assert lines[5] == ">pep1"
        assert lines[6] == "-------MKT"

    def test_n_target_copies_encoded_in_metadata(self, tmp_path):
        target_a3m = self._target_a3m(tmp_path)
        out_path = build_paired_a3m(
            "MKT", "pep1", target_a3m, tmp_path / "out", n_target_copies=2
        )
        first_line = out_path.read_text().splitlines()[0]
        assert first_line == "#7,3\t2,1"

    def test_custom_complex_name(self, tmp_path):
        target_a3m = self._target_a3m(tmp_path)
        out_path = build_paired_a3m(
            "MKT", "pep1", target_a3m, tmp_path / "out", complex_name="custom_complex"
        )
        assert out_path.name == "custom_complex.a3m"
        assert out_path.read_text().splitlines()[1] == ">custom_complex"

    def test_preserves_all_msa_rows(self, tmp_path):
        p = tmp_path / "target.a3m"
        _write_a3m(p, [
            ">query", "AC",
            ">hit1", "AC",
            ">hit2", "A-",
            ">hit3", "-C",
        ])
        out_path = build_paired_a3m("KT", "pep1", p, tmp_path / "out")
        text = out_path.read_text()
        assert text.count(">hit") == 3
