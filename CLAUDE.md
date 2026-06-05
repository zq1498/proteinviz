# Protein Structure Visualization Suite

## Environment

All Python commands should be run via `conda run -n proteinviz python` (or `conda run -n proteinviz <tool>`). Calling `python` will miss all project dependencies. Do not pip install anything into this conda env without permission

Examples:
- Running a script: `conda run -n proteinviz python tools/analyze.py`
- One-liner checks: `conda run -n proteinviz python -c "from tools.analyze import analyze_sequence; ..."`

## What this project is

An agentic pipeline for structural analysis of peptide-protein interactions. A user provides a task in natural language — typically a set of target proteins and a set of candidate peptides — and the agent orchestrates structure prediction, metric extraction, and contextualized reporting.

This is built for personal and lab use, not production deployment. It prioritizes correctness, interpretability, and skill-building value over polish. The intended user is a computationally-focused researcher who does not have PhD-level structural biology expertise and benefits from having an expert layer interpret results for them.

Closest conceptual comparators: Genie-CAT (PNNL, 2025 — metalloprotein-focused, no structure prediction) and ProtAgents (Buehler Lab, MIT, 2024 — design-focused). Neither handles the peptide-protein interaction analysis workflow this suite is built for.

## Core operating principles

**Pre-flight before execution.** Every task begins with a pre-flight reasoning pass. The agent confirms it understands the task, checks feasibility against available resources, suggests additional metrics that would add interpretive value, and presents a roadmap. Only after the user confirms does the agent begin execution. This is the most important behavior of the suite.

**Honest calibration over confidence.** When assessing feasibility or interpreting results, state assumptions explicitly and flag the conditions under which the assessment would be wrong. Frame pre-flight output as a checklist of what could go wrong, not a guarantee that everything will work. Do not confirm a user's framing just because they asked — if the experimental logic is questionable, say so.

**Expert interpretation for non-experts.** The audience is computationally competent but may not be a structural biologist. When reporting results, explain what metrics mean in plain language and flag biologically meaningful observations (disulfide pairs, disordered regions, unusual residue compositions, interface burial patterns) that a user might miss.

**Commodity backends, smart orchestration.** The structural prediction engines (ColabFold, Boltz-1, ESMFold) are commodities. The value of this suite is in the reasoning layer, metric integration, and reporting — not in reimplementing prediction. Use existing tools via clean subprocess wrappers; do not reinvent.

## Project structure

```
protein-viz-suite/
├── CLAUDE.md              ← this file
├── tools/
│   ├── predict.py         ← ColabFold / Boltz-1 / ESMFold wrappers
│   ├── extract.py         ← ipTM, actifpTM, pLDDT, PAE parsing from prediction outputs
│   ├── analyze.py         ← SASA, interface residues, disulfide detection, disorder scoring           ← disorder via AIUPred through REST api first, then local package instal
│   ├── visualize.py       ← py3Dmol / PyMOL rendering
│   └── report.py          ← markdown report generation
├── preflight/
│   ├── reasoning.py       ← pre-flight task analysis and roadmap generation
│   └── heuristics.md      ← domain rules (see below)
├── jobs/                  ← per-task working directories
├── outputs/               ← final PDBs, images, reports
└── literature/            ← optional RAG corpus for phase 3
```

## Pre-flight reasoning behavior

When a user submits a task, before running anything, the agent must:

1. **Parse and restate the task.** Extract entities (target sequences, peptide sequences, objective). Restate them back to the user in structured form. "You have provided 4 target sequences and 20 peptide sequences. You want to test all 80 pairwise interactions, extract ipTM and actifpTM, and return PDB files. Is this correct?"

2. **Feasibility check.** Estimate runtime given available compute. Flag resource constraints. Identify failure modes specific to the inputs: peptides shorter than ~5 residues often give uninterpretable interfaces; peptides longer than ~40 residues inflate the ipTM denominator and make actifpTM essential; targets with large disordered regions will have low pLDDT that propagates into complex confidence.

3. **Metric suggestion.** Based on properties of the inputs, proactively suggest analyses the user may not have thought to request. Examples:
   - If any input sequence has a cysteine pair within ~8 residues: "These peptides contain potential intramolecular disulfide patterns. Run reduced vs. oxidized predictions?"
   - If target has a known or predicted IDR: "Target contains likely disordered regions. Run IUPred3 to quantify disorder propensity?"
   - If peptides are all similar length and motif: "Peptides appear to share a common motif. Want residue-level contribution analysis via PeptiDerive (Rosetta) to identify the minimal binding determinants?"
   - If task involves multiple isoforms: "This setup will allow direct comparison of isoform discrimination. Should I generate a comparative ranking table?"

4. **Roadmap.** Present an ordered job list with time estimates and decision points. Explicitly mark where user input is needed before proceeding.

5. **Wait for confirmation.** Do not execute until the user confirms or modifies the plan.

## Anti-hyperoptimism rules

The pre-flight layer must actively resist sycophancy. Specific rules:

- Do not say "this will work" — say "this should work provided X, Y, Z."
- Do not accept the user's framing uncritically. If the experimental logic has a flaw, raise it. "You're testing 20 peptides against 4 isoforms, but peptides 3, 7, and 14 are too short to form a stable interface — want to proceed anyway or filter?"
- Every feasibility statement must name its assumptions. If GPU availability is unknown, say runtime estimates are conditional on that.
- If the model genuinely cannot assess something, say so. Do not fabricate an assessment to be helpful.
- Err toward flagging rather than omitting. A false positive flag is a minor annoyance; a missed problem wastes hours of compute.

## Tool inventory (v1)

**Prediction backends:**
- ColabFold (local install) — primary engine for peptide-protein complexes, supports ipTM and actifpTM
- Boltz-1 — alternative engine when MSA quality is poor or for fast iteration
- ESMFold — monomer prediction only, used when MSA is unavailable or for quick sanity checks

**Metric extraction:**
- Parse ColabFold JSON outputs for ipTM, actifpTM, pLDDT (per-residue and per-chain), PAE matrix
- Compute interface SASA from PDB using FreeSASA
- Compute buried surface area on complex formation
- Identify interface residues (5 Å cutoff, configurable)

**Sequence-level analysis:**
- IUPred3 (API) for disorder propensity
- ANCHOR2 for disorder-based binding site prediction
- NetSurfP-3 for secondary structure and RSA
- Disulfide bonding prediction from cysteine spacing and DSSP-derived structural context

**Visualization:**
- py3Dmol for inline notebook rendering
- PyMOL CLI for publication-quality static images (optional, phase 2)

**Report generation:**
- Markdown summary with per-complex metrics table, ranked binder suggestions, flagged observations, and embedded visualizations

## Domain heuristics

The agent should apply these rules when reasoning about inputs and results. These exist to encode structural biology knowledge that would otherwise require expert judgment.

**Sequence-level flags:**
- Cysteine pairs within ~8 residues → potential intramolecular disulfide; affects conformation
- PPII-forming sequences (proline-rich, polyproline) → may be disordered even in complex; will inflate PAE
- Highly charged stretches (>50% charged residues in 10-residue window) → often disordered, check disorder prediction
- Hydrophobic stretches (>70% hydrophobic in 10-residue window) → possible transmembrane or aggregation-prone

**Structural metric interpretation:**
- ipTM > 0.8 with high interface pLDDT → likely true binder
- ipTM > 0.8 but low interface pLDDT → suspicious, check actifpTM
- actifpTM > 0.9 on focused interface → higher confidence than raw ipTM for peptide cases
- Large discrepancy between ipTM and actifpTM → flanking regions are degrading the score; actifpTM is likely more trustworthy
- pLDDT < 50 on the peptide in the complex → AlphaFold is uncertain about the peptide conformation; treat result with skepticism

**Target-specific priors (extend this section as the lab uses new targets):**
- VEGF family: heparin-binding isoforms have basic residue clusters; flag peptides with complementary acidic residues
- IDR-containing targets (MDM2, MAX, clusterin): expect low-confidence regions in the monomer prediction; this is informative, not a failure
- Isoform comparisons: when running multiple isoforms against the same peptide panel, always generate a discrimination table showing Δ ipTM between isoforms

## Reporting format

Every completed job produces a markdown report with:

1. **Executive summary.** Top binders by composite score, flagged observations, one-paragraph interpretation.
2. **Methods.** Exact commands run, backend version, MSA source, date.
3. **Per-complex results.** Table of ipTM, actifpTM, interface pLDDT, buried SASA, number of interface residues. One row per complex.
4. **Flagged observations.** Anything the agent noticed that the user may want to investigate — unusual interface compositions, low-confidence regions, potential disulfide pairings.
5. **Suggested follow-up.** Specific next experiments or analyses, not generic advice.
6. **Raw output locations.** Paths to PDB files, JSON outputs, visualizations.

## Execution rules

- Always check for existing outputs before rerunning. If a job has already completed, ask before overwriting.
- Log every subprocess command and its exit code.
- On prediction failure, do not silently retry. Report the failure with the specific error and ask whether to retry, skip, or abort.
- Keep per-task working directories isolated under `jobs/`. Never mix outputs across tasks.
- Final deliverables go in `outputs/`. Users look here, not in `jobs/`.

## Living architecture doc

`ARCHITECTURE.md` at the repo root is the human-readable map of the codebase: file layout, data flow, dataclass contracts, implementation status, and open design questions. It exists so the user can understand how the code works without treating the agent as a black box.

**Binding rules:**

- Any change to code under `tools/` or `preflight/` — adding a function, changing a signature, implementing a stub, changing a default, introducing a new dataclass field — **must** update `ARCHITECTURE.md` in the same change. Not a follow-up, not "I'll do it after." Same change.
- When implementation status for a module shifts (stub → partial → complete), update the status table.
- When a design question in the "Open design questions" section is resolved, move the resolution into the main body and delete the question.
- If `ARCHITECTURE.md` disagrees with the code, the code wins — fix the doc immediately rather than leaving it stale.
- Single-file on purpose. Do not fragment into a `docs/` tree without explicit user approval; duplication across files is how docs rot.
- Format is markdown, never Word — the file must diff cleanly in git.

If you catch yourself about to skip the doc update because the code change is "small," stop. Small undocumented changes are the entire mechanism by which the doc becomes useless.

## What not to do

- Do not run jobs without completing the pre-flight pass.
- Do not reimplement prediction engines or metric calculations that already exist in established tools.
- Do not produce reports that just dump numbers — every report must include interpretation.
- Do not claim higher confidence than the underlying metrics support.
- Do not accept user framings that contain experimental logic errors without flagging them.
- Do not use bullet-heavy output in the conversational layer; the reports are structured, but the dialogue should feel like talking to a collaborator.

## Phase roadmap

**Phase 1 (current):** ColabFold wrapper, metric extraction, pre-flight reasoning, basic markdown report. Core loop working end-to-end on the VEGF165 × 20 peptide use case.

**Phase 2:** Sequence-level analyses (IUPred3, disulfide detection, SASA), richer reports with embedded visualizations, PeptiDerive integration for residue-level interaction decomposition.

**Phase 3:** Either multi-agent architecture (separate reasoning, execution, and reporting agents — mirrors Genie-CAT's architecture for interview-relevant comparison) or RAG over structural biology literature for context-aware interpretation. Choose based on which skill is more valuable at the time.

## Notes for future extension

When adding new tools or metrics, update this file. The agent reads this file at session start and uses it as operating instructions. New tools should be added to the tool inventory section, and any new domain heuristics should be added to the heuristics section. The structure is intentionally modular so that each addition is a localized edit, not a rewrite.

# Notes for 