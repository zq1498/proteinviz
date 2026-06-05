#!/usr/bin/env python3
"""Build a ColabFold multimer A3M from a cached target MSA and a peptide sequence.

Runs on a DCC compute node without the Singularity container — stdlib only.
Logic mirrors tools/msa.build_paired_a3m(); keep the two in sync if either changes.

Usage:
    python3 build_a3m.py <target_a3m> <peptide_seq> <peptide_id> <out_dir> [<complex_name>]

Writes <out_dir>/<complex_name>.a3m  (default name: {target_stem}_{peptide_id}.a3m)

Format written is the ColabFold multimer A3M metadata format (colabfold/batch.py
unserialize_msa). The first line encodes chain lengths and cardinalities; the
_split_by_aln function then splits each MSA row into per-chain segments by counting
aligned (non-lowercase) positions:

    #<target_len>,<pep_len>\t1,1   <- metadata: chain lengths TAB cardinalities
    >complex_name                   <- query header
    TARGET_SEQ + PEPTIDE_SEQ        <- concatenated query (no ':' separator)
    >UniRef100_HOM1_header
    HOM1_SEQ                        <- target MSA rows (chain B segment = empty → unpaired A)
    >UniRef100_HOM2_header
    HOM2_SEQ
    ...
    >peptide_id
    <target_len gaps>PEPTIDE_SEQ    <- chain A = gaps (no aa) → unpaired B
"""
from __future__ import annotations  # str | None syntax on Python < 3.10

import sys
from pathlib import Path


def parse_a3m(path: str) -> tuple[str, list[tuple[str, str]]]:
    """Return (query_seq, [(header, seq), ...]) preserving per-row headers.

    ColabFold requires a >header line before every MSA row; discarding them
    causes the entire file to be read as one giant concatenated sequence.
    """
    lines = Path(path).read_text().splitlines()
    if not lines or not lines[0].startswith(">"):
        raise ValueError(f"Not a valid A3M file (no leading header): {path}")
    rows: list[tuple[str, str]] = []
    query_seq: str | None = None
    current_header: str = ""
    current_seq: list[str] = []
    is_first = True
    for line in lines:
        s = line.strip()
        if not s:
            continue
        if s.startswith(">"):
            if current_seq:
                seq = "".join(current_seq)
                if is_first:
                    query_seq = seq
                    is_first = False
                else:
                    rows.append((current_header, seq))
                current_seq = []
            current_header = s
        elif s == "#":
            continue  # chain-break markers only appear in multimer A3Ms
        else:
            current_seq.append(s)
    if current_seq:
        seq = "".join(current_seq)
        if is_first:
            query_seq = seq
        else:
            rows.append((current_header, seq))
    if query_seq is None:
        raise ValueError(f"No sequences found in {path}")
    return query_seq, rows


def build(
    target_a3m: str,
    peptide_seq: str,
    peptide_id: str,
    out_dir: str,
    complex_name: str | None = None,
    n_target_copies: int = 1,
) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    name = complex_name or f"{Path(target_a3m).stem}_{peptide_id}"
    out_path = out / f"{name}.a3m"

    target_seq, msa_rows = parse_a3m(target_a3m)
    target_gap = "-" * len(target_seq)

    with out_path.open("w") as f:
        # Metadata: chain lengths TAB cardinalities.
        # n_target_copies > 1 for homo-oligomeric targets (e.g. VEGF homodimer → 2).
        # ColabFold replicates the target chain n_target_copies times using the same MSA.
        f.write(f"#{len(target_seq)},{len(peptide_seq)}\t{n_target_copies},1\n")
        f.write(f">{name}\n")
        # Unique chain sequences concatenated (no ':'); _split_by_aln slices by length.
        f.write(f"{target_seq}{peptide_seq}\n")
        for header, row in msa_rows:
            f.write(f"{header}\n")
            f.write(f"{row}\n")
        f.write(f">{peptide_id}\n")
        f.write(f"{target_gap}{peptide_seq}\n")

    print(
        f"[build_a3m] wrote {out_path} "
        f"({len(msa_rows)} target MSA rows, n_target_copies={n_target_copies})",
        flush=True,
    )
    return out_path


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
