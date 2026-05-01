# Protein Structure Visualization Suite — Project State

<!--
  This file is the single source of truth the bot reads every run.
  You are expected to edit it by hand over time: update statuses, add sub-tasks,
  rewrite sections as the project evolves.

  When you replace or rewrite this file, please KEEP the "Recent Bot Suggestions"
  section and its format comment at the bottom — the bot appends log entries there
  after every run, and losing it means losing the running history (the per-run
  suggestion files in suggestions/ are preserved regardless).
-->

## Project Goal
An agentic pipeline that accepts natural language biological tasks and orchestrates
structure prediction, metric extraction, and expert-contextualized reporting for non-expert users.
Primary use case: peptide-protein interaction screening (e.g., VEGF165 × 20-peptide panel).

## Current Phase
**Phase 1** — core prediction loop end-to-end. ColabFold wrapper, metric extraction,
plotting, structural comparison, and markdown report rendering are all working.
Pre-flight reasoning has progressed from a hardcoded skeleton to a registry-driven
layer: an EDAM-inspired three-axis YAML capability catalog now backs the
deterministic pre-flight helpers. First real jobs have run successfully
(`smoke_test`, `3v03` / HSA). Next milestone: complete the sequence/structure
analysis layer (IUPred3, FreeSASA, disulfide detection) and validate end-to-end on
a real peptide panel.

## Methodology Update — Pre-flight as a three-layer system

The pre-flight subsystem is now organized as three orthogonal layers, mirroring
the [EDAM ontology](https://edamontology.org/page) split. This change is recent
and is the largest structural shift since the last state update.

- **Ontology layer** — `preflight/heuristics.md` (and the mirror in CLAUDE.md
  § Domain heuristics). Conceptual rules: thresholds, sequence flags, metric
  interpretation, target-specific priors. Read by the agent in conversation.
- **Registry layer** — `preflight/capabilities/*.yaml` (machine-queryable) plus
  `preflight/queryables.md` (human-readable mirror). Catalogs *what tools the
  agent has access to and what they produce/consume*. Three files:
    - `subtasks.yaml` — Topic axis: folding, binding, ppi, structural_analysis,
      mutation, msa_generation, disorder (with sub-children: core, binding,
      conditional, ensemble, llps). Hierarchical; polyhierarchy allowed.
    - `data_types.yaml` — Data axis: aa_sequence, pdb_file, plddt_per_residue,
      pae_matrix, iptm_score, actifptm_score, sasa_per_residue, etc. Things that
      flow between tools.
    - `tools.yaml` — each tool entry references subtask IDs, has_input data IDs,
      has_output data IDs, and a `wrapper` Python entrypoint (or `null` if not
      yet wrapped). Forward relations only — subtasks do not list their tools;
      that's a query.
  Conventions enforced in `capabilities/README.md`: stable IDs, namespaced
  (`subtask_*`, `data_*`, `tool_*`), removed entries get `deprecated: true` not
  deleted, one-sentence definitions.
- **Execution layer** — `tools/*.py` and `preflight/reasoning.py`. The
  reasoning module loads the registry once at import (cached via `lru_cache`)
  and exposes lookup helpers; the tool wrappers do the actual subprocess /
  Python calls.

The point of this split is that adding a new tool no longer requires editing
hardcoded conditionals in `reasoning.py`. Adding `tool_metapredict` to
`tools.yaml` makes it visible to `tools_for_subtask("subtask_disorder_core")`
automatically; when its `wrapper` is filled in, `build_roadmap` will start
proposing it without further code changes.

## What Has Been Built

### Prediction backend — `tools/predict.py` [COMPLETE for ColabFold]
- ColabFold via WSL fully wired: path translation, MSYS_NO_PATHCONV, subprocess logging
- `--fast` mode: `--msa-mode single_sequence --num-recycle 1` (~5 min, no MSA accuracy, dev/test only)
- `--target-a3m PATH`: pre-built target MSA reused across a peptide panel via
  `build_paired_a3m()` (peptide single-seq + `*` chain-break + target A3M
  verbatim). Eliminates N−1 redundant MSA jobs in panel runs.
- `--msa-only`: runs `colabfold_search` via WSL to emit a `.a3m` and exit
  before structure prediction. Implemented by `run_colabfold_msa_only()`.
  Requires `COLABFOLD_DB` env var.
- `write_fasta()`, `check_backend()`, `predict()` dispatcher all implemented
- CLI: `python -m tools.predict --check`, `--fasta X --out Y`, `--sequence SEQ --out Y`,
  with flags `--fast`, `--target-a3m PATH`, `--msa-only`
- ESMFold API: stub only
- GPU: RTX 3060 Ti, 8 GB VRAM. ColabFold binary at `/home/zach/localcolabfold/.pixi/envs/default/bin/colabfold_batch`

### Metric extraction — `tools/extract.py` [COMPLETE for ColabFold]
- `extract_colabfold()`: globs `*_scores_rank_*_*.json`, parses per-residue pLDDT, PAE matrix, ptm
- `ExtractedMetrics` dataclass: model_id, backend, pdb_path, pLDDT stats, PAE, ipTM, actifpTM, ranking_score
- `DEFAULT_SCORE_WEIGHTS = {"primary": 0.5, "mean_plddt": 0.3, "interface_plddt": 0.2}` —
  module-level dict, single source of truth. `composite_score(m, weights=...)`
  takes an optional override so calibration experiments don't require code edits.
- `composite_score()`: still placeholder weighting; needs calibration on real binder/non-binder data
- ColabFold JSON schema confirmed from smoke test: `plddt` (list), `pae` (N×N), `max_pae`, `ptm`; `iptm`/`actifptm` only in multimer/complex runs
- ESMFold extraction: stub only

### Plotting — `tools/plot.py` [COMPLETE]
- `plot_plddt_profile()`: per-residue line plot, confidence tiers match RCSB/UniProt colouring
- `plot_pae_heatmap()`: N×N green-scale heatmap, 0–30 Å range
- `plot_model_comparison()`: grouped bar chart of mean pLDDT + pTM across 5 ranked models; optional TM-score overlay
- `generate_all()`: convenience wrapper, saves to `outputs/{job_id}/`
- All headless (Agg backend). CLI: `python -m tools.plot --job-dir jobs/X --out-dir outputs/X`

### Structural comparison — `tools/compare.py` + `tools/_compare_inner.py` [COMPLETE]
- TM-score and Cα RMSD via `tmtools` (runs inside WSL because tmtools requires MSVC on Windows)
- `ComparisonResult` dataclass: tmscore_pred, tmscore_ref, rmsd_ca, n_pred, n_ref, optional pLDDT fields
- `print_report()`: plain-language TM-score interpretation included
- CLI: `python -m tools.compare --pred X --ref Y [--scores Z]`

### Report rendering — `tools/report.py` [COMPLETE]
- `render_report(inputs)` produces a 6-section markdown report: executive
  summary, methods, per-complex metrics table, flagged observations, suggested
  follow-up, raw output paths.
- `executive_summary(ranked, flags, weights)` — confidence tier interpretation
  (`>0.70` strong / `0.50–0.70` moderate / `<0.50` weak) plus human-readable
  metric summary.
- `write_report(inputs, out_path)` — disk writer; creates parent dirs.
- `ReportInputs` dataclass carries an optional `weights` dict that flows
  through to `composite_score`; the weights used are echoed into the report.

### MSA on DCC cluster — `tools/msa.py` + `cluster/msa_search.sh` [IMPLEMENTED, UNTESTED]
- Full upload (SCP) → SLURM submit → poll squeue → download `.a3m` cycle
- `MSAResult` dataclass; SSH uses BatchMode=yes (key auth, no prompts). Key: `~/.ssh/id_dcc`
- `build_paired_a3m()` lives here as well — used both for DCC-produced MSAs and
  local ones, by `run_colabfold(target_a3m=...)`.
- SLURM template: `common` partition, 8 CPUs / 32 GB RAM, email on END/FAIL to `zq36@duke.edu`
- Status: pending DCC setup and first test run. `cluster/install_localcolabfold.sh` is the one-time DCC install script.
- Env vars required: `DCC_HOST`, `DCC_KEY`, `DCC_WORK`, `DCC_CF_BIN`, `DCC_DB_DIR`

### Pre-flight heuristics — `preflight/heuristics.md` [COMPLETE]
- Full rule tables: sequence-level flags, metric interpretation thresholds, target-specific priors, analysis suggestions
- Mirrored summary in CLAUDE.md § Domain heuristics

### Pre-flight capability registry — `preflight/capabilities/` [LIVE]
- Three-axis EDAM-style YAML registry (see Methodology Update above for full
  description). Files: `subtasks.yaml`, `data_types.yaml`, `tools.yaml`, plus a
  `README.md` that documents the schema and conventions.
- Currently catalogs ~15 tools across folding, binding, ppi, structural
  analysis, disorder (core/binding/ensemble), MSA generation, and mutation.
  Most entries have `wrapper: null` (declared capability, not yet callable).
  Wired wrappers: `tool_colabfold`, `tool_colabfold_search`, `tool_dcc_msa`,
  `tool_iupred3` (wrapper points to a stub function), `tool_tmtools`.
- `preflight/queryables.md` is the human-readable companion that lists tools
  per subtask with install tier (Tier 1 clean / Tier 2 non-trivial / Tier 3
  web-only) and notes. Kept in sync with `capabilities/` the same way
  ARCHITECTURE.md is kept in sync with code.

### Pre-flight reasoning — `preflight/reasoning.py` [MOSTLY LIVE]
- Dataclasses: `TaskSpec`, `FeasibilityReport`, `Roadmap`.
- Registry loader (`_load_registry`) caches all three YAML files at import via
  `lru_cache`; `_reload_registry()` provided for tests / interactive sessions.
- Lookup helpers — implemented: `lookup_tool`, `lookup_subtask`,
  `lookup_data_type`, `all_tools`, `all_subtasks`, `tools_for_subtask`
  (walks the parent chain via `_descendant_subtasks`), `tools_providing`,
  `tools_requiring`.
- `validate_inputs(spec)` — implemented: applies sequence-level heuristics
  (peptide <5, peptide >40, target >2000, cysteine pairs ≤8 residues apart)
  per target / peptide.
- `suggest_analyses(spec)` — implemented: proposes additions resolved against
  the registry (multi-target → ΔipTM table; long targets → "run disorder check
  ({tools_for_subtask('subtask_disorder_core')})"; cysteine-bearing peptides →
  reduced vs. oxidized predictions).
- `build_roadmap(spec, feasibility)` — implemented: composes ordered execution
  plan, dispatching to whichever binding/folding tool the registry returns
  first. Adds decision points based on input properties.
- `estimate_runtime()` still a placeholder: `pairs · (60 + 0.5·avg_length)`
  seconds, uncalibrated.

## What Is Still Stubbed

| Module | Status | Notes |
|---|---|---|
| `tools/analyze.py` | Partial | `analyze_sequence()` implemented; `analyze_structure()`, `detect_disulfides()`, `query_iupred3()` still stubs |
| `tools/visualize.py` | All stubs | py3Dmol HTML + PyMOL PNG rendering |
| `tools/predict.py` ESMFold | Stub | `run_esmfold_api()` raises NotImplementedError |
| `tools/extract.py` ESMFold | Stub | |
| `preflight/reasoning.py` `estimate_runtime` | Placeholder | Constants uncalibrated |
| Most `wrapper:` fields in `tools.yaml` | Null | Tool declared, not yet callable (FoldX, FreeSASA, Boltz-1, NetSurfP-3, metapredict, localCIDER, biotite, biopython DSSP, ANCHOR2, PDBePISA) |

### `analyze_sequence()` — implemented 2026-04-24
- MW, pI, aromatic fraction, cysteine positions via Biopython `ProteinAnalysis` (pixi ColabFold env)
- Window-based flags (window=10): >50% charged → likely IDR / run IUPred3; >70% hydrophobic → possible TM/aggregation-prone; ≥25% proline or PPP stretch → PPII-forming / PPII warning
- Cysteine-pair flag: any two cysteines ≤8 residues apart → potential intramolecular disulfide; suggests reduced vs. oxidized predictions
- Length flags: <5 (recommend exclusion), <20 (confirm intent), >40 (actifpTM essential), >2000 (ColabFold OOM risk)
- Each window flag fires at most once per sequence (first offending window) to avoid flag spam

## Jobs Run So Far

| Job | Sequence | Status | Notes |
|---|---|---|---|
| `smoke_test` | Short test peptide | Complete | First end-to-end run; ~8h total, ~7h MSA wait on remote MMseqs2 |
| `3v03` | HSA (583 aa) | Complete | 5 ranked models, PDBs + score JSONs present; plots generated in `outputs/3v03/` |

## Open Design Questions

- **`composite_score` weights**: 0.5/0.3/0.2 split now externalized as
  `DEFAULT_SCORE_WEIGHTS` and passable per-call, but still uncalibrated. Needs a
  labeled binder/non-binder set.
- **`job_id` convention**: `{target_id}_{YYYYMMDD}_{short_hash}` proposed but not enforced. Jobs currently named by hand.
- **`done.flag`**: proposed completion sentinel but not implemented. `*.done.txt` exists in current jobs ad hoc.
- **Pre-flight session state**: does conversation state persist in a file (resumable sessions) or live only in agent context? Not decided.
- **Registry coverage vs. callable coverage**: `tools.yaml` describes many
  tools that the wrappers don't actually call yet. The agent can present them
  in a roadmap, but `build_roadmap` will return a "no X tool registered" string
  if no wrapper exists. Decide whether `wrapper: null` entries should be
  hidden from `tools_for_subtask` or surfaced with a tier label.
- **MSA bottleneck**: remote MMseqs2 server throttles heavily (~7h for smoke test). Options evaluated: local DB (~1 TB, fast), `--fast` mode (implemented), DCC cluster (implemented, untested), ESMFold API (no MSA needed, low accuracy), reused target A3M via `--target-a3m` (implemented — best ROI for panel runs). Recommend `--fast` for dev runs; reused target A3M for panels; DCC for first-time targets in production.

## Next Priorities (Phase 1 completion)

1. **`tools/analyze.py`**: `query_iupred3()` (REST API, highest value — also wires up
   the existing `tool_iupred3` registry entry to a real wrapper). Then
   `analyze_structure()` and `detect_disulfides()` (need FreeSASA, also
   declared in registry, wrapper still null).
2. **`tools/visualize.py`**: py3Dmol inline viewer — user noted this as the next session goal.
3. **DCC MSA**: run `cluster/install_localcolabfold.sh` on login node and do a first test of `tools/msa.py`. Unblocks production-speed MSA.
4. **Reasoning calibration**: replace placeholder constants in
   `estimate_runtime()` after a few real runs; tune `composite_score` weights
   on real binder/non-binder data.
5. **Registry hygiene pass**: add `tier:` field to `tools.yaml` entries
   (mirroring `queryables.md`) so `build_roadmap` can prefer Tier 1 tools and
   warn when only Tier 3 web-only options exist.

## Recent Bot Suggestions
<!-- Bot appends entries here after each run -->
<!-- Format: [YYYY-MM-DD HH:MM] suggestion_file.md — one-line summary -->
[2026-04-24] — Implemented `analyze_sequence()` in `tools/analyze.py`; confirmed `extract_colabfold()` FileNotFoundError guard already present; updated ARCHITECTURE.md and project_state.md.
[2026-04-25 19:00] suggestion_2026-04-25_19-00.md — DEPLOY: implement `render_report()` markdown renderer; NOVEL: pre-compute target MSA + reuse across panel via paired A3M (`--target-a3m`); REFACTOR: externalize `composite_score` weights as `DEFAULT_SCORE_WEIGHTS` dict. All three were subsequently implemented.
[2026-04-28] — Updated project_state.md to reflect new `preflight/capabilities/` registry, three-layer pre-flight methodology (ontology / registry / execution), live `validate_inputs` / `suggest_analyses` / `build_roadmap`, completed `tools/report.py`, and the `--target-a3m` / `--msa-only` paths in `tools/predict.py`.
