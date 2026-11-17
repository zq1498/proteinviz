# Task Description: β-catenin / γ-catenin

## Library file
`moPPIt_V3_beta_gamma_catenin_library.csv` — 2008 rows, columns `name, sequence`.

Composition (by name prefix):
| Prefix | Count | Notes |
|---|---|---|
| `beta_cat_N_term` | 500 | candidates against β-catenin N-terminal region |
| `beta_cat_internal` | 500 | candidates against an internal β-catenin site |
| `gamma_cat_N_term` | 500 | candidates against γ-catenin N-terminal region |
| `gamma_cat_internal` | 500 | candidates against an internal γ-catenin site |
| `beta_N_term_scramble` | 4 | scrambled controls for β-cat N-term set |
| `gamma_N_term_scramble` | 4 | scrambled controls for γ-cat N-term set |

Two targets (β-catenin, γ-catenin) × two binding sites each (N-term, internal) — effectively four sub-panels in one file. No precomputed ipTM; sequences only. Note there are scrambled controls for the N-term sets on both targets but **none listed for either `internal` set** — worth confirming that's intentional before running.

## Targets
- Proteins: β-catenin / CTNNB1 (UniProt [P35222](https://www.uniprot.org/uniprotkb/P35222)) and γ-catenin (plakoglobin) / JUP (UniProt [P14923](https://www.uniprot.org/uniprotkb/P14923))
- Construct boundaries — **proposed from UniProt, not yet confirmed by Zach.** These came back from an AI summary of the UniProt record, not a hand-verified feature table, so spot-check before locking in a target FASTA:
  - β-catenin (781 aa): N-terminal region ~1–136 (GSK3B phosphodegron, disordered), central armadillo-repeat domain ~138–686 (the folded core — cadherin/TCF-LEF/APC binding groove), C-terminal region ~671–781 (transactivation, disordered).
  - γ-catenin (745 aa): N-terminal region ~1–131 (disordered), armadillo-repeat domain ~132–661 (folded core, 12 ARM repeats), C-terminal tail beyond 661 (disordered, "unstructured").
  - Both have disordered N/C termini flanking a folded armadillo core — this should trip CLAUDE.md's IDR pre-flight suggestion (low monomer pLDDT in those flanks is expected, not a failure).

## Objective
Phage display selection: this library's sequences were panned for binding to β-catenin (positive selection) while being counter-selected against γ-catenin (negative selection), i.e. the design goal was **β-catenin-selective binders that do not cross-react with the very close paralog γ-catenin.** No experimental (wet-lab) results accompany this library — the CSV is sequences only, so our pipeline's ipTM/actifpTM predictions against both targets are the only evaluation available. This is a natural fit for CLAUDE.md's isoform-discrimination heuristic: for every candidate, compute Δ ipTM = ipTM(β-catenin) − ipTM(γ-catenin) and rank on that, not on raw β-catenin ipTM alone, since the whole point is *selectivity*, not just affinity.

**Confirmed by Zach (2026-09-03):** the `N_term` / `internal` name suffixes do map to (a) — the disordered N-terminal region vs. the folded armadillo groove, as distinct subdomain targets. So `beta_cat_N_term` / `gamma_cat_N_term` candidates target the disordered phosphodegron-mimic region, and `beta_cat_internal` / `gamma_cat_internal` candidates target the armadillo-repeat groove.

Everything else (exact construct boundaries, scramble-control handling, run scope) is **intentionally left unresolved here for the pre-flight pass to catch and ask about** — see Open questions below.

## Comparison plan
Per-candidate Δ ipTM (and Δ actifpTM) between β-catenin and γ-catenin complexes, ranked to surface the most β-selective binders. No ground-truth experimental labels exist here (unlike the AMHR2 library's `Best ipTM` column), so this run is purely computational discrimination — frame the report's confidence language accordingly (predicted selectivity, not validated selectivity).
