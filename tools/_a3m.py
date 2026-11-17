"""Canonical A3M format utilities — stdlib only.

Single source of truth for parse_a3m and build_paired_a3m. Both tools/msa.py
(local) and cluster/build_a3m.py (DCC node) import from here. predict_panel()
uploads this file alongside cluster/build_a3m.py so the cluster script can
resolve it as a sibling module.
"""
from __future__ import annotations

from pathlib import Path


def parse_a3m(path: "Path | str") -> tuple[str, list[tuple[str, str]]]:
    """Return (query_seq, [(header, seq), ...]) from a single-chain A3M file.

    Preserves per-row >header lines. ColabFold requires a header before every
    MSA row; discarding headers causes the entire file to be read as one giant
    concatenated sequence when the paired A3M is parsed.
    """
    lines = Path(path).read_text().splitlines()
    if not lines or not lines[0].startswith(">"):
        raise ValueError(f"Not a valid A3M file (no leading header): {path}")

    rows: list[tuple[str, str]] = []
    query_seq: str | None = None
    current_header: str = ""
    current: list[str] = []
    is_first = True
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(">"):
            if current:
                seq = "".join(current)
                if is_first:
                    query_seq = seq
                    is_first = False
                else:
                    rows.append((current_header, seq))
                current = []
            current_header = stripped
        elif stripped == "#":
            continue  # chain-break markers only appear in multimer A3Ms
        else:
            current.append(stripped)
    if current:
        seq = "".join(current)
        if is_first:
            query_seq = seq
        else:
            rows.append((current_header, seq))

    if query_seq is None:
        raise ValueError(f"No sequences found in {path}")
    return query_seq, rows


def build_paired_a3m(
    peptide_seq: str,
    peptide_id: str,
    target_a3m: "Path | str",
    out_dir: "Path | str",
    complex_name: "str | None" = None,
    n_target_copies: int = 1,
) -> Path:
    """Build a ColabFold multimer A3M for a target:peptide complex from a cached target MSA.

    Produces the ColabFold multimer A3M metadata format that unserialize_msa() in
    colabfold/batch.py expects. The _split_by_aln function splits each MSA row into
    per-chain segments by counting aligned (non-lowercase) positions against the chain
    lengths encoded in the metadata header:

        #<target_len>,<pep_len>\t1,1   <- metadata: chain lengths TAB cardinalities
        >complex_name                   <- query header
        TARGET_SEQ + PEPTIDE_SEQ        <- concatenated query (no ':' separator)
        >UniRef100_HOM1_header
        HOM1_SEQ                        <- target MSA rows; chain B segment empty -> unpaired A
        ...
        >peptide_id
        <target_len gaps>PEPTIDE_SEQ    <- chain A = gaps (no aa) -> unpaired B

    Args:
        peptide_seq:     Amino acid string for the peptide (chain B).
        peptide_id:      Short identifier used in the filename and header.
        target_a3m:      Pre-computed single-chain A3M for the target protein (chain A).
        out_dir:         Directory to write the paired A3M into.
        complex_name:    Header name for the combined complex. Defaults to
                         '{target_stem}_{peptide_id}'.
        n_target_copies: >1 for homo-oligomeric targets (e.g. VEGF homodimer -> 2).

    Returns:
        Path to the written paired A3M file.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    target_a3m = Path(target_a3m)
    name = complex_name or f"{target_a3m.stem}_{peptide_id}"
    out_path = out / f"{name}.a3m"

    target_seq, msa_rows = parse_a3m(target_a3m)
    target_gap = "-" * len(target_seq)

    with out_path.open("w") as f:
        f.write(f"#{len(target_seq)},{len(peptide_seq)}\t{n_target_copies},1\n")
        f.write(f">{name}\n")
        f.write(f"{target_seq}{peptide_seq}\n")
        for header, row in msa_rows:
            f.write(f"{header}\n")
            f.write(f"{row}\n")
        f.write(f">{peptide_id}\n")
        f.write(f"{target_gap}{peptide_seq}\n")

    print(
        f"[a3m] wrote {out_path} "
        f"(target {len(target_seq)} aa ×{n_target_copies}, "
        f"peptide {len(peptide_seq)} aa, {len(msa_rows)} MSA rows)",
        flush=True,
    )
    return out_path
