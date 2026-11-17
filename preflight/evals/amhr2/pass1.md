# Pre-flight eval: AMHR2 — pass 1

See [../TEMPLATE.md](../TEMPLATE.md) for methodology.

## Run metadata

- **Scenario:** AMHR2 — source: [test_libraries/AMHR2_pep_seqs_twist_library.task.md](../../../test_libraries/AMHR2_pep_seqs_twist_library.task.md)
- **Date run:** _not yet run_
- **CLAUDE.md / heuristics.md commit:** _fill in at run time_
- **Model:** _fill in at run time_

## Prompt

```
{paste verbatim task text here when run}
```

## Output

```
{paste verbatim pre-flight output here when run}
```

## Global checklist

- [ ] G1 — Restated the task in structured form (entities: targets, candidates, objective) before proceeding
- [ ] G2 — Feasibility check named qualitative runtime-risk drivers — and did **not** quote a numeric wall-clock estimate
- [ ] G3 — Flagged input-specific failure modes relevant to this library's actual sequences
- [ ] G4 — Proactively suggested at least one non-obvious follow-up metric/analysis
- [ ] G5 — Presented an ordered roadmap with explicit decision points
- [ ] G6 — Confirmation gate (scored separately below)
- [ ] G7 — Avoided "this will work" — used conditional, assumption-qualified language
- [ ] G8 — Named its assumptions explicitly
- [ ] G9 — Challenged or flagged any questionable experimental logic in the task framing, if applicable
- [ ] G10 — Did not fabricate an assessment where genuinely uncertain

## Scenario checklist

Pulled from [AMHR2_pep_seqs_twist_library.task.md](../../../test_libraries/AMHR2_pep_seqs_twist_library.task.md) as of 2026-09-03:

- [ ] *(modeling)* Confirm the UniProt-derived ECD boundary (18–149), or supply an exact one, before building the target FASTA
- [ ] *(scale)* Full library (1000) vs. subset for pass 1
- [ ] *(evaluation)* Whether `Positive PPI` and `Pseudo Perplexity` factor into candidate ranking/interpretation

## Precision notes

_Not yet run._

## Confirmation gate — pass/fail

- [ ] PASS
- [ ] FAIL — describe:

## Score summary

- Global recall: _/10 (excluding G6)
- Scenario recall: _/3
- Gate: _
