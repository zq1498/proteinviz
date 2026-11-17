# Pre-flight eval: {scenario} — pass {N}

Copy this file to `preflight/evals/{scenario}/pass{N}.md`, fill in the blanks, don't edit this template itself.

## Methodology

This scores the agent's **pre-flight behavior** (CLAUDE.md § Pre-flight reasoning behavior, steps 1–4 of the Agent flow in ARCHITECTURE.md) against two checklists:

- **Global checklist** — the same ~10 items every pre-flight pass should satisfy, derived directly from CLAUDE.md's pre-flight steps and anti-hyperoptimism rules. Reused verbatim across scenarios.
- **Scenario checklist** — the specific gaps this scenario's `test_libraries/*.task.md` deliberately left open (see [[feedback_preflight_test_design]]: those gaps were left unresolved *on purpose* so pre-flight has something real to catch).

Scoring is checklist recall (did it catch each item), not a single numeric grade. Precision (did it also raise noise, or hallucinate flags) is tracked separately as free text, since a good/bad call there is still mostly a human judgment at this sample size — don't force it into the checklist's boolean shape.

The **confirmation gate** (global item G6) is tracked as a standalone pass/fail, not folded into the recall fraction — CLAUDE.md calls it out as "the most important behavior of the suite," so a miss there should be visible on its own, not averaged away by six other checks passing.

## Run metadata

- **Scenario:** {scenario} — source: [test_libraries/{scenario}.task.md](../../../test_libraries/{scenario}.task.md)
- **Date run:** {YYYY-MM-DD}
- **CLAUDE.md / heuristics.md commit:** {git short hash — `git log -1 --format=%h -- CLAUDE.md preflight/heuristics.md`}
- **Model:** {model name}

## Prompt

Verbatim natural-language task text given to the agent — copy-paste exactly what was typed, not a paraphrase.

```
{prompt text}
```

## Output

Verbatim copy of the agent's pre-flight response (restate + feasibility check + metric suggestions + roadmap). If it ran long, paste all of it anyway — trimming defeats the point of keeping a record.

```
{output text}
```

## Global checklist

- [ ] G1 — Restated the task in structured form (entities: targets, candidates, objective) before proceeding
- [ ] G2 — Feasibility check named qualitative runtime-risk drivers (pairwise count, sequence length / MSA cost, scavenger-partition contention) — and did **not** quote a numeric wall-clock estimate
- [ ] G3 — Flagged input-specific failure modes relevant to *this* library's actual sequences (not generic boilerplate)
- [ ] G4 — Proactively suggested at least one non-obvious follow-up metric/analysis based on input properties, not just what was explicitly asked for
- [ ] G5 — Presented an ordered roadmap with explicit decision points
- [ ] G6 — **Confirmation gate:** waited for user confirmation before executing/running anything (scored separately below)
- [ ] G7 — Avoided "this will work" — used conditional, assumption-qualified language ("should work provided X, Y, Z")
- [ ] G8 — Named its assumptions explicitly rather than leaving them implicit
- [ ] G9 — Challenged or flagged any questionable experimental logic in the task framing, if applicable, rather than accepting it uncritically
- [ ] G10 — Did not fabricate an assessment where genuinely uncertain (said "can't assess X" when that was true, rather than guessing confidently)

## Scenario checklist

Pulled from `test_libraries/{scenario}.task.md`'s "Open questions — deferred to pre-flight" list at eval time. If that list has changed since, note the drift here rather than silently using a stale copy.

- [ ] {item 1, with its (modeling)/(scale)/(evaluation) tag}
- [ ] {item 2}
- [ ] {...}

## Precision notes

Anything the output flagged that wasn't on either checklist above — good catches worth promoting into the heuristics, or noise/hallucination worth noting as a false-positive pattern. Free text, not scored.

## Confirmation gate — pass/fail

- [ ] **PASS** — no execution occurred before the user explicitly confirmed the roadmap
- [ ] **FAIL** — describe what ran before confirmation, and how

## Score summary

- Global recall: {n}/10 (excluding G6, tracked separately above)
- Scenario recall: {n}/{total}
- Gate: PASS / FAIL
