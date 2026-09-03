#!/usr/bin/env python3
"""Build a ColabFold multimer A3M from a cached target MSA and a peptide sequence.

Runs on a DCC compute node without the Singularity container. Delegates to _a3m.py,
which is uploaded to the same remote directory as this file by predict_panel().

Usage:
    python3 build_a3m.py <target_a3m> <peptide_seq> <peptide_id> <out_dir> [<complex_name>] [<n_target_copies>]
"""
from __future__ import annotations

import sys
from pathlib import Path

# _a3m.py is uploaded alongside this file by predict_panel().
# Running `python3 /abs/path/build_a3m.py` adds the script directory to sys.path,
# so `import _a3m` resolves to the co-uploaded _a3m.py.
from _a3m import build_paired_a3m, parse_a3m  # noqa: F401 — parse_a3m re-exported for callers


def build(
    target_a3m: str,
    peptide_seq: str,
    peptide_id: str,
    out_dir: str,
    complex_name: "str | None" = None,
    n_target_copies: int = 1,
) -> Path:
    """CLI-order adapter: target_a3m first, matching array_predict.sh invocation."""
    return build_paired_a3m(peptide_seq, peptide_id, target_a3m, out_dir, complex_name, n_target_copies)


if __name__ == "__main__":
    if len(sys.argv) < 5:
        sys.exit(
            "usage: build_a3m.py <target_a3m> <peptide_seq> <peptide_id> <out_dir>"
            " [<complex_name>] [<n_target_copies>]"
        )
    build(
        sys.argv[1],
        sys.argv[2],
        sys.argv[3],
        sys.argv[4],
        sys.argv[5] if len(sys.argv) > 5 and sys.argv[5] else None,
        int(sys.argv[6]) if len(sys.argv) > 6 else 1,
    )
