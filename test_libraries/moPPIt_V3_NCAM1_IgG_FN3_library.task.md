# Task Description: NCAM1

## Library file
`moPPIt_V3_NCAM1_IgG_FN3_library.csv` — 1008 rows, columns `Name, Sequence`.

Composition (by name prefix):
| Prefix | Count | Notes |
|---|---|---|
| `NCAM1_IgG` | 500 | IgG-domain-scaffold candidates |
| `NCAM_1_FN3` | 500 | FN3-domain-scaffold candidates |
| `top_IgG_scramble` | 4 | scrambled-sequence controls for the IgG set |
| `top_FN3_scramble` | 4 | scrambled-sequence controls for the FN3 set |

Sequences only — no precomputed ipTM here (unlike the AMHR2 library), so there's no external reference to validate against; this is a fresh discovery/ranking run.

## Target
- Protein: NCAM1 / Neural cell adhesion molecule 1 (UniProt [P13591](https://www.uniprot.org/uniprotkb/P13591), canonical isoform 858 aa)
- Construct boundaries — **proposed from UniProt, not yet confirmed by Zach.** Pulled from an AI summary of the UniProt record rather than a hand-verified feature table, so spot-check before locking in target FASTAs:
  - Signal peptide 1–19; mature ectodomain 20–718; transmembrane 719–739; cytoplasmic 740–858.
  - Five Ig-like C2-type domains (N-terminal half of ectodomain): Ig1 20–111, Ig2 116–205, Ig3 212–301, Ig4 308–413, Ig5 416–501.
  - Two FN3 domains (C-terminal half of ectodomain): FN3-1 509–608, FN3-2 611–706.
  - Given the library's naming (`NCAM1_IgG` vs. `NCAM_1_FN3`), the natural read is two separate target constructs: an Ig1–Ig5 construct (~20–501) for the IgG-scaffold sub-panel, and an FN3-1/FN3-2 construct (~509–706) for the FN3-scaffold sub-panel — rather than one full-ectodomain target for both. Confirm before building FASTAs; full ectodomain (~700 aa) would also be a much larger MSA/fold than either half.

## Objective
Identify domain-specific NCAM1 binders: the `NCAM1_IgG` sub-panel (500 candidates) targets the N-terminal Ig-like domains, the `NCAM_1_FN3` sub-panel (500 candidates) targets the more C-terminal FN3 domains — two separate epitope campaigns on the same protein, not one shared-epitope screen. "Limited accompanying experimental results" exist for this library (per Zach) but are not present as columns in this CSV (only `Name, Sequence`) — if there's a separate results file, it should be identified and linked here before the comparison/report step, so predicted hits can be checked against whatever partial ground truth exists.

Everything below (construct boundaries/split, locating the experimental results, run scope, scramble handling) is **intentionally left unresolved here for the pre-flight pass to catch and ask about** — see Open questions.

## Comparison plan
Rank each sub-panel's candidates against its own scramble baseline (per-scaffold, not pooled — the Ig and FN3 constructs are different folds so their ipTM distributions aren't directly comparable). Cross-reference against whatever limited experimental results exist once located. Composite-score weights are still uncalibrated (see [[project_composite_score]]), so treat any ranking as provisional pending ablation.


