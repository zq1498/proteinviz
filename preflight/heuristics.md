# Pre-flight Heuristics

Rules the agent applies when reasoning about inputs and interpreting results. Mirror of the "Domain heuristics" section in [CLAUDE.md](../CLAUDE.md); keep them in sync when editing.

## Sequence-level flags

| Pattern | Signal | Suggested action |
|---|---|---|
| Cysteine pair within ~8 residues | Potential intramolecular disulfide | Offer reduced vs. oxidized predictions |
| Proline-rich / PPII motifs | Likely disordered even bound; inflates PAE | Warn on confidence interpretation |
| >50% charged residues in 10-residue window | Likely IDR | Run IUPred3 for disorder score |
| >70% hydrophobic residues in 10-residue window | Possible TM / aggregation-prone | Flag; may not fold as soluble monomer |
| Length < 20 residues | Too short for informative monomer fold | Ask whether the user wants to proceed |
| Length > 2000 residues | ColabFold often OOMs | Suggest domain-wise prediction |
| Peptide < 5 residues | Uninterpretable interface | Recommend exclusion |
| Peptide > 40 residues | ipTM denominator inflation | actifpTM becomes essential |

## Structural metric interpretation

| Observation | Interpretation |
|---|---|
| ipTM > 0.8 AND high interface pLDDT | Likely true binder |
| ipTM > 0.8 AND low interface pLDDT | Suspicious — check actifpTM |
| actifpTM > 0.9 on focused interface | Higher confidence than raw ipTM for peptide cases |
| Large ipTM vs actifpTM gap | Flanking regions degrading score; trust actifpTM |
| Peptide pLDDT < 50 in complex | AF uncertain about peptide conformation; skeptical |

## Target-specific priors

Extend as the lab touches new targets. Each entry: target family, expected behavior, action.

- **VEGF family.** Heparin-binding isoforms have basic residue clusters. Flag peptides with complementary acidic residues as candidate electrostatic binders.
- **IDR-containing targets (MDM2, MAX, clusterin).** Expect low-confidence regions in monomer predictions — this is informative, not failure.
- **Isoform panels.** When running multiple isoforms against the same peptides, always generate a Δ ipTM discrimination table.

## Analysis suggestions triggered by inputs

- Multiple isoforms of the same target → isoform-discrimination table
- Peptides share a common motif → residue-level contribution analysis (PeptiDerive, alanine scan)
- Any input has known PTM sites (from UniProt) → flag whether the user wants PTM-aware analysis
- All peptides have similar length and charge → comparative binder ranking only meaningful if normalized

## Pre-flight output checklist

Before execution, the agent should have:

- [ ] Restated the task in structured form (targets, peptides, objective)
- [ ] Flagged any inputs that trip the sequence-level rules above
- [ ] Estimated runtime and named the GPU assumption
- [ ] Suggested at least one analysis the user did not request (when applicable)
- [ ] Listed decision points requiring user input
- [ ] Waited for explicit user confirmation before running anything
