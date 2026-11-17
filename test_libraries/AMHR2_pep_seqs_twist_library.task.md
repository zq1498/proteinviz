# Task Description: AMHR2

## Library file
`AMHR2_pep_seqs_twist_library.xlsx` — 1000 rows, single sheet.

Columns: index, `ID` (`AMHR2_PepMLM_rank1` … `rank1000`), `Positive PPI` (binary label, 1/0), `Best ipTM` (precomputed reference value, e.g. 0.91 for rank1), `Binder` (12-mer peptide sequence), `Pseudo Perplexity`, `DNA_seq`.

All 1000 peptides came out of a PepMLM design run, pre-ranked, with a prior ipTM already attached to each — this is the library Zach flagged for comparing our MSA-caching pipeline's output against a known reference (see [[project_msa_caching_validated]]).

## Target
- Protein: AMHR2 / Anti-Müllerian hormone receptor type 2 (UniProt [Q16671](https://www.uniprot.org/uniprotkb/Q16671), 573 aa)
- Construct boundary — **proposed from UniProt, not yet confirmed by Zach.** Pulled from an AI summary of the UniProt record rather than a hand-verified feature table, so spot-check before locking in the target FASTA:
  - Signal peptide 1–17; extracellular ligand-binding domain 18–149 (two disulfides, Cys55–79 and Cys92–109); transmembrane 150–170; cytoplasmic kinase domain 171–573.
  - Screen goal is protein-level (no sub-domain targeting), which for a type-II single-pass receptor means the extracellular domain (~18–149) is the only biologically relevant construct for a peptide-interface prediction — the intracellular kinase domain isn't presented to the peptide in vivo and isn't suitable for a monomer-complex prediction anyway. This matches what's already in [[project_new_target_screens]].

## Objective
Simple positive-interaction screen: no domain targeting within AMHR2, just identify sequences from this library that positively interact with the receptor's extracellular domain. All 1000 peptides already carry a precomputed `Best ipTM` and binary `Positive PPI` label from the original PepMLM design/ranking run, so this doubles as a validation task — do our pipeline's ipTM/actifpTM (with MSA caching, see [[project_msa_caching_validated]]) track those precomputed values closely enough to trust the pipeline on a target it hasn't run before?

Everything below (exact construct boundary, run scope, how the precomputed labels factor into evaluation) is **intentionally left unresolved here for the pre-flight pass to catch and ask about** — see Open questions.

## Comparison plan
Run the pipeline on the chosen subset, plot our ipTM/actifpTM against the library's `Best ipTM`, and flag systematic divergence per CLAUDE.md's ipTM-vs-actifpTM heuristics (large gaps mean flanking regions are degrading the raw score, actifpTM is likely more trustworthy). Since `Positive PPI` is a binary label, it's also worth checking whether our predicted-binder threshold agrees with it as a simple concordance check.