"""Tests for the pure-Python pieces of tools.predict.

We do NOT exercise run_colabfold / run_colabfold_msa_only here — those
shell out to WSL and belong in an integration suite. Only to_wsl_path
and write_fasta are unit-testable in isolation.

`to_wsl_path` is Windows-only: it calls Path.resolve() which on Linux
yields a PosixPath with no drive attribute, and the function's whole
purpose (Windows -> WSL) is meaningless off Windows. Those tests skip
on non-Windows interpreters so the suite stays green when run inside
WSL or on a CI Linux runner.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from tools.predict import to_wsl_path, write_fasta


windows_only = pytest.mark.skipif(
    sys.platform != "win32",
    reason="to_wsl_path is a Windows-only utility (requires WindowsPath)",
)


@windows_only
def test_to_wsl_path_translates_c_drive():
    result = to_wsl_path(Path("C:/Users/Zach/foo"))
    assert result == "/mnt/c/Users/Zach/foo", f"got {result!r}"


@windows_only
def test_to_wsl_path_lowercases_drive_letter():
    # Path on Windows preserves drive case in .drive ('C:'); to_wsl_path must lowercase it.
    result = to_wsl_path(Path("C:/some/path"))
    assert result.startswith("/mnt/c/"), f"got {result!r}"


@windows_only
def test_to_wsl_path_uses_forward_slashes():
    result = to_wsl_path(Path("C:/a/b/c"))
    assert "\\" not in result, f"WSL path must not contain backslashes: {result!r}"


def test_write_fasta_creates_file_with_correct_format(tmp_path: Path):
    out = tmp_path / "x.fasta"
    write_fasta("MKTAY", "myseq", out)
    text = out.read_text()
    assert text == ">myseq\nMKTAY\n"


def test_write_fasta_creates_parent_dirs(tmp_path: Path):
    out = tmp_path / "deep" / "nested" / "x.fasta"
    write_fasta("MKT", "n", out)
    assert out.exists()
