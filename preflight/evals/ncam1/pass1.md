# Pre-flight eval: NCAM1 — pass 1

See [../TEMPLATE.md](../TEMPLATE.md) for methodology.

## Run metadata

- **Scenario:** NCAM1 — source: [test_libraries/moPPIt_V3_NCAM1_IgG_FN3_library.task.md](../../../test_libraries/moPPIt_V3_NCAM1_IgG_FN3_library.task.md)
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

Pulled from [moPPIt_V3_NCAM1_IgG_FN3_library.task.md](../../../test_libraries/moPPIt_V3_NCAM1_IgG_FN3_library.task.md) as of 2026-09-03:

- [ ] *(modeling)* Confirm the UniProt-derived domain boundaries, or supply exact ones, before building target FASTAs
- [ ] *(modeling)* Confirm Ig1–Ig5 and FN3-1/FN3-2 should be two separate target constructs, not one full-ectodomain target
- [ ] *(modeling)* Where the "limited" experimental results Zach mentioned actually live and what they measure
- [ ] *(modeling)* Full sub-panels vs. subset for pass 1, and how the 8 scrambled controls get used per sub-panel

## Precision notes

_Not yet run._

## Confirmation gate — pass/fail

- [ ] PASS
- [ ] FAIL — describe:

## Score summary

- Global recall: _/10 (excluding G6)
- Scenario recall: _/4
- Gate: _
