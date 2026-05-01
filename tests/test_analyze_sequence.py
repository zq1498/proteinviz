"""Tests for tools.analyze.analyze_sequence.

Pure function — easy to unit test. Each test pins one heuristic.
The 'clean sequence has no flags' test is the most important one:
it guards against false-positive flag explosions.
"""

from __future__ import annotations

from tools.analyze import analyze_sequence


def test_short_peptide_flagged():
    feats = analyze_sequence("MKT")
    assert feats.length == 3
    assert any("length<5" in f for f in feats.flags)


def test_disulfide_pair_flagged_with_positions():
    # Cys at positions 1 and 3 in 0-indexed → Δ=2, well within 8
    feats = analyze_sequence("ACGCT")
    assert feats.cysteine_positions == [1, 3]
    assert any("cysteine pair" in f and "1-3" in f for f in feats.flags)


def test_distant_cysteines_not_flagged_as_disulfide():
    # Cys at 0 and 15 — Δ=15, should NOT raise the disulfide flag
    seq = "C" + "A" * 14 + "C" + "A" * 5
    feats = analyze_sequence(seq)
    assert feats.cysteine_positions == [0, 15]
    assert not any("cysteine pair" in f for f in feats.flags)


def test_charged_window_flagged_as_idr():
    feats = analyze_sequence("DEKRDEKRDE")
    assert any("charged window" in f for f in feats.flags)


def test_hydrophobic_window_flagged():
    # 10 residues, all hydrophobic
    feats = analyze_sequence("AAAAAFFFFF")
    assert any("hydrophobic window" in f for f in feats.flags)


def test_proline_rich_flagged():
    # >25% P in some 10-residue window
    feats = analyze_sequence("APAPAPAPAPGGGG")
    assert any("proline-rich" in f or "polyproline" in f for f in feats.flags)


def test_polyproline_run_flagged_when_no_window_match():
    # PPP run but only one window crosses it; ensure at least one flag fires
    feats = analyze_sequence("MKTAYIAKQRQISFVKSHFSPPPRYE")
    assert any("proline-rich" in f or "polyproline" in f for f in feats.flags)


def test_long_peptide_flagged():
    # >40 residues triggers ipTM denominator inflation flag
    feats = analyze_sequence("M" + "G" * 50)
    assert any("length>40" in f for f in feats.flags)


def test_clean_sequence_has_no_flags():
    """Guards against false-positive flag explosions on a normal globular peptide.

    If this test goes red after a heuristic edit, you've broadened the rule
    too far.
    """
    # A typical globular sequence: mixed composition, no Cys pairs, no IDR motifs
    feats = analyze_sequence("MKTAYIAKQRQISFVKSHFSRQLEERLGLIEVQGGV")
    assert feats.length == 36
    assert feats.cysteine_positions == []
    # length < 40, no Cys, no charged/hydrophobic/proline windows
    assert feats.flags == [], f"unexpected flags on clean sequence: {feats.flags}"


def test_basic_physchem_properties_populated():
    feats = analyze_sequence("MKTAYIAKQRQISFVKSHFSRQLEERLGLIEVQGGV")
    assert feats.molecular_weight > 0
    assert 0 < feats.pI < 14
    assert 0.0 <= feats.aromatic_fraction <= 1.0


def test_sequence_uppercased_and_stripped():
    feats = analyze_sequence("  mkt  ")
    assert feats.sequence == "MKT"
