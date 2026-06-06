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
Primary use case: peptide-protein interaction screening (e.g., VEGF165 × peptide panel).

## Current Phase
**Phase 1 — complete.** The core prediction loop is end-to-end: DCC SLURM submission,
metric extraction, sequence/structure analysis, plotting, structural comparison, and markdown
report rendering all work. The panel prediction pipeline (primary MSA job + SLURM array) is
implemented and has been exercised on VEGF165 targets. The orchestrator (`tools/batch.py`)
connects the pre-flight roadmap to the execution loop. Next milestone: calibrate
`composite_score` weights on real binder/non-binder data and extend the reporting layer
to consume `StructureFeatures` and `SequenceFeatures` outputs alongside `ExtractedMetrics`.

## Execution model — DCC SLURM (not local)

ColabFold runs entirely on the Duke Compute Cluster (DCC), not locally. The workflow for
every prediction call is:

1. `tools/predict.py` uploads the FASTA (and optionally pre-built `.a3m` files) to DCC
   via SCP.
2. It calls `sbatch` via SSH to submit the job to the `scavenger-gpu` partition.
3. It polls `squeue` / `sacct` until the job reaches a terminal SLURM state.
4. It SCPs PDB files, score JSONs, and `.a3m` files back to the local `out_dir`.

For **panels** (`predict_panel()`), a primary job runs `colabfold_search` on the target
monomer (SEARCH_ONLY=1), then a SLURM array job runs one task per peptide. Each array task
calls `cluster/build_a3m.py` on the node itself (no round-trip) to construct the paired A3M
from the cached target MSA, then runs `colabfold_batch` independently. This eliminates N−1
redundant MSA searches for a panel of N peptides against the same target.

There is no local ColabFold install. The `--fast` mode and `--msa-only` flags described in
earlier notes are obsolete — replaced by the DCC SLURM workflow.

## Methodology — Pre-flight as a three-layer system

The pre-flight subsystem is organized as three orthogonal layers, mirroring the
[EDAM ontology](https://edamontology.org/page) split.

- **Ontology layer** — `preflight/heuristics.md` (and the mirror in CLAUDE.md
  § Domain heuristics). Conceptual rules: thresholds, sequence flags, metric
  interpretation, target-specific priors. Read by the agent in conversation.
- **Registry layer** — `preflight/capabilities/*.yaml` (machine-queryable) plus
  `preflight/queryables.md` (human-readable mirror). Three files:
    - `subtasks.yaml` — Topic axis: folding, binding, ppi, structural_analysis,
      mutation, msa_generation, disorder (sub-children: core, binding, conditional,
      ensemble, llps). Hierarchical; polyhierarchy allowed.
    - `data_types.yaml` — Data axis: aa_sequence, pdb_file, plddt_per_residue,
      pae_matrix, iptm_score, actifptm_score, sasa_per_residue, etc.
    - `tools.yaml` — each entry references subtask IDs, has_input / has_output data IDs,
      and a `wrapper` Python entrypoint (or `null` if not yet wrapped).
  Conventions in `capabilities/README.md`: stable IDs, namespaced (`subtask_*`,
  `data_*`, `tool_*`), deprecated entries get `deprecated: true` not deleted.
- **Execution layer** — `tools/*.py`, `preflight/reasoning.py`, and `tools/batch.py`.
  The reasoning module loads the registry once at import (cached via `lru_cache`) and
  exposes lookup helpers. `resolve_wrapper()` dynamically imports the callable from the
  dotted path in the registry (cached per `tool_id`). `tools/batch.py` is the
  task-agnostic orchestrator that walks the `Roadmap.jobs` list.

## What Has Been Built

### Prediction backend — `tools/predict.py` [COMPLETE]
- All predictions run on DCC via SLURM (`sbatch` → poll `squeue`/`sacct` → SCP results).
- `predict_structure(fasta, out_dir, ...)`: upload → sbatch → wait → download PDB + JSON + A3M.
  - `fast=True`: 1 model / 1 recycle for quick iteration.
  - `pre_msa_dir`: upload pre-built `.a3m` files before sbatch; predict.sh detects them and
    skips `colabfold_search`, running `colabfold_batch` directly.
  - Always downloads `.a3m` files to `out_dir/msas/` for reuse in subsequent calls.
- `predict_panel(target_fasta, peptides, ...)`: MSA-cached SLURM panel pipeline.
  - Primary job: SEARCH_ONLY=1, produces target `.a3m` in remote `msas/`.
  - Array job: `--dependency=afterok:{primary_id}`, one task per peptide; builds paired A3M
    on the DCC node via `cluster/build_a3m.py` (no local round-trip), then `colabfold_batch`.
  - Returns immediately (`PanelResult`); does not poll. Monitor via squeue.
  - Supports homo-oligomeric targets: extracts monomer chain for the MSA search, passes
    `N_TARGET_COPIES` to `array_predict.sh` so the cardinality is encoded in the A3M metadata.
- `predict()`: dispatcher to `predict_structure` or `run_esmfold_api` (stub).
- `PredictionResult` dataclass: `success`, `backend`, `output_dir`, `runtime_s`, `pdb_paths`, `a3m_paths`, `error`.
- `PanelResult` dataclass: `primary_job_id`, `array_job_id`, `n_peptides`, `remote_jobs_dir`.
- CLI: `python -m tools.predict --fasta X --out Y`, `--fast`, `--pre-msa-dir`, `--panel --manifest`,
  `--no-extra-ptm`, `--check`.
- ESMFold API: stub only (`run_esmfold_api` raises `NotImplementedError`).

### Cluster scripts — `cluster/` [COMPLETE]
- `cluster/04_run_prediction.sh`: SLURM prediction script deployed on DCC. Supports
  `SEARCH_ONLY=1` (MSA-only mode), `CALC_EXTRA_PTM`, `NUM_RECYCLE`, `NUM_MODELS`. Includes
  NumPy < 2 workaround for ColabFold 1.6.0 container.
- `cluster/array_predict.sh`: SLURM array job template for panel predictions. Reads peptide
  from `MANIFEST` by `SLURM_ARRAY_TASK_ID`, builds paired A3M on node, runs `colabfold_batch`.
- `cluster/build_a3m.py`: stdlib-only standalone (runs on DCC nodes outside Singularity) that
  mirrors `tools/msa.build_paired_a3m()`. Accepts `n_target_copies` for homo-oligomers.
- `cluster/predict.sh`: reference copy of the prediction script for local comparison.
- `cluster/vegf165_dimer.fasta`: VEGF165 homodimer FASTA used for panel submissions.

### Metric extraction — `tools/extract.py` [COMPLETE]
- `extract_colabfold(out_dir)`: globs `*_scores_rank_*_*.json`, parses per-residue pLDDT,
  PAE matrix, ptm, iptm, actifptm. Derives `per_chain_plddt` from PDB B-factor column.
- `ExtractedMetrics` dataclass: model_id, backend, pdb_path, pLDDT stats, per_chain_plddt,
  PAE, ipTM, actifpTM, ranking_score.
- `DEFAULT_SCORE_WEIGHTS = {"primary": 0.5, "mean_plddt": 0.3, "interface_plddt": 0.2}`.
- `composite_score(m, interface_plddt, weights)`: actifpTM used as primary only for 2-chain
  complexes — demoted for 3+ chains (e.g., homodimer + peptide) where it aggregates across
  all inter-chain interfaces and inflates the score independently of peptide binding quality.
- `to_json(metrics, path)`: serialize list of `ExtractedMetrics` to disk.
- ESMFold extraction: stub only.

### Sequence and structure analysis — `tools/analyze.py` [COMPLETE]
- `analyze_sequence(seq)` → `SequenceFeatures`: MW, pI, aromatic fraction, cysteine positions
  via Biopython `ProteinAnalysis`; window-based flags (window=10) for IDR/hydrophobic/PPII
  patterns; length flags at <5, <20, >40, >2000.
- `analyze_structure(pdb_path, interface_cutoff_a)` → `StructureFeatures`: total SASA and
  per-chain SASA via FreeSASA; interface residues (all-atom, configurable cutoff) via Biopython;
  BSA for two-chain complexes only (`isolated_sum − complex_SASA`); disulfide pairs via
  `detect_disulfides()`.
- `detect_disulfides(pdb_path, distance_cutoff_a=3.0)`: Cys SG–SG distance < 3.0 Å (permissive
  to catch unrelaxed structures). Returns `(chain_i, res_i, chain_j, res_j)` tuples.
- `query_aiupred(seq)`: per-residue disorder propensity via local **AIUPred** package (PyTorch).
  The IUPred3 REST API endpoint (`iupred3.elte.hu`) is defunct; AIUPred is the live replacement.
  `query_iupred3` kept as legacy alias so the `tool_iupred3` registry entry still resolves.

### PyRosetta analysis — `tools/rosetta.py` + `tools/pyrosetta_worker.py` [COMPLETE]

PyRosetta (2026.19, py314_0) is installed in a separate conda env (`proteinviz_pyrosetta`,
Python 3.14, JHU channel) because the current release has no Python 3.12 wheel compatible
with the main `proteinviz` env. The two envs are bridged via subprocess: `rosetta.py` serialises
call arguments to JSON, spawns `pyrosetta_worker.py` under
`/home/zach/miniconda3/envs/proteinviz_pyrosetta/bin/python`, and deserialises the result.

- `score_interface(pdb_path, interface="A_B")` → `InterfaceScore`: REF2015 dG_separated
  (kcal/mol REU) with `pack_separated=True`, dSASA (Å²), and interface residue count via
  `InterfaceAnalyzerMover`. Primary metric for binding ΔG assessment.
- `per_residue_energies(pdb_path)` → `list[ResidueEnergy]`: per-residue REF2015 total energy
  for all residues. Returns both Rosetta (sequential) and PDB residue numbering; use PDB numbers
  when cross-referencing ColabFold outputs.
- `alanine_scan(pdb_path, interface="A_B")` → `list[AlaScanEntry]`: single-point ala scan on
  interface residues, sorted by ΔΔG descending. Positive ΔΔG = wildtype contributes favorably
  (hot-spot candidate). Ala and Gly residues skipped. Current implementation uses full-complex
  score difference (approximation); rigorous version would run IAM on each mutant (~2× slower).
- `_call_worker(function, **kwargs)`: internal dispatcher — spawns worker, parses JSON response,
  raises `RuntimeError` on non-JSON output or worker-reported errors.

To add a new Rosetta function: implement in worker, add to `_FUNCTIONS`, add wrapper + dataclass
in `rosetta.py`. See module docstrings for the full pattern.

### py3Dmol visualization — `tools/visualize.py` [PARTIAL]
- `render_py3dmol(pdb_path, color_by)` → self-contained HTML string with inline 3Dmol.js viewer.
  Three color modes: `"plddt"` (RCSB B-factor gradient), `"chain"`, `"spectrum"`.
- `render_complex_py3dmol(pdb_path, peptide_chain)` → complex viewer that colors target chains by
  pLDDT gradient and highlights the designated peptide chain (default `"C"`) in a distinct color.
- `render_interface_surface(pdb_path, interface_residues, chains_to_show, peptide_chain)` → HTML with
  a pLDDT-coloured cartoon + Gaussian surface and interface residues highlighted (auto-computed if not
  supplied). `write_html`, `write_complex_html`, `write_interface_surface_html`: write viewers to disk.
- `write_html_from_metrics(metrics, out_dir, peptide_chain)`: dispatcher — uses complex viewer
  when `peptide_chain` is given, plain viewer otherwise.
- pLDDT legend included in the HTML when `color_by="plddt"`.

### Plotting — `tools/plot.py` [COMPLETE]
- `plot_plddt_profile()`, `plot_pae_heatmap()`, `plot_model_comparison()`, `generate_all()`.
  All headless (Agg backend). CLI: `python -m tools.plot --job-dir jobs/X --out-dir outputs/X`.

### Structural comparison — `tools/compare.py` + `tools/_compare_inner.py` [COMPLETE]
- TM-score and Cα RMSD via `tmtools`. `ComparisonResult` dataclass. CLI available.

### Report rendering — `tools/report.py` [COMPLETE]
- `render_report(inputs)`: 7-section per-complex markdown report; actifpTM automatically demoted
  in flags for 3+ chain complexes with a plain-language explanation; per-chain pLDDT in table.
- `render_panel_report(inp)` + `PanelEntry` / `PanelReportInputs`: aggregated report ranking all
  peptides against one target — §3 Ranked Binders is one row per complex; the actifpTM warning is
  emitted once for the whole panel. This is the deliverable for screening tasks.
- `executive_summary()` / `_panel_executive_summary()`: adapt language to monomer (pTM tiers) vs.
  complex (composite tiers); share `_binding_tier` / `_fold_tier` / `_actifptm_warning`.
- `write_report(inputs, out_path)`, `write_panel_report(inp, out_path)`.
- `ReportInputs` / `PanelEntry` carry optional `structure`, `sequence`, `disorder_iupred3`,
  `disorder_metapredict`, `cider`, `hotspots` analysis fields. §4 Structural Analysis renders
  both disorder predictors labeled separately (mean + fraction > 0.5 + plain-language gloss),
  a CIDER block (κ, FCR, NCPR, phase_region), and top-5 hotspot hits. Panel Per-Complex Notes
  shows per-peptide one-liners for all four. Populated by `build_report_inputs` /
  `build_panel_report_inputs` from the corresponding `job.results[tool_id]` keys.

### Orchestrator — `tools/batch.py` [COMPLETE]
- `run_batch(spec, roadmap, jobs_root, abort_on_failure, outputs_root=None)` → `BatchResult`. When
  `outputs_root` is set, calls `emit_reports` after the loop (default None = no side effects beyond jobs_root).
- `emit_reports()` + `build_panel_report_inputs()`: close the prompt→report path. Groups done jobs by
  target — binding/binding_panel → one `outputs/{target_id}/panel_report.md`; monomer → per-job report.
  CLI exposes `--outputs-root` (default `outputs/`), so the CLI is one-call prompt→report.
- Per-job loop: reads `done.flag` to skip already-completed jobs (resume support). **(B5)** Each
  step runs in its own try; a step with `optional=True` (the analysis steps) that raises is logged
  and skipped without failing the Job. The old `_run_post_plan_analysis` hook is retired — structure
  (`tool_freesasa`) and sequence (`tool_analyze_sequence`) analysis now run as optional plan steps,
  and `build_report_inputs` / `build_panel_report_inputs` read them from those tool-id keys.
- `_ensure_fasta()`: derives FASTA from `data_aa_sequence` if missing; handles `str` (monomer)
  and `list[str]` (multimer, ColabFold ':' convention).
- `_bind_inputs()`: resolves `JobStep.input_bindings` to call kwargs. Reserved keys:
  `_job_dir` → `Path`, `_msa_cache` → MSA cache path (reserved for v2).
- `_propagate_outputs(job, tool_id, result)`: **registry-driven (B2)** — drives propagation from
  the tool's `output_map` (resolved via `_resolve_output_expr`: `"."` / `attr` / `attr[0]`), writing
  each resolved value into `job.inputs[data_id]` for downstream bindings; skips None/empty. Terminal
  tools (no `output_map`) propagate nothing. Reproduces the old rank-1-PDB behaviour for ColabFold.
- Persistence: `job_meta.json` written per job; `batch.log` and `failure.log` in each job dir.
- CLI: `python -m tools.batch --targets ... --peptides ... [--abort-on-failure]`.

### Execution dataclasses — `preflight/jobs.py` [COMPLETE]
- `Job`: unit of work with `inputs`, `plan`, `status`, `results`, `job_dir`, `metadata`.
- `JobStep`: one tool invocation with `tool_id`, `kwargs`, `input_bindings`, `consumes`, `produces`,
  and `optional` (**B5** — supplemental step; failure is logged and skipped, not fatal).
- `BatchResult`: wraps all jobs with `n_done`, `n_failed`, `n_skipped` properties and
  `started_at` / `finished_at` timestamps.

### Pre-flight reasoning — `preflight/reasoning.py` [COMPLETE]
- Registry loader cached via `lru_cache`; `_reload_registry()` for tests.
- Lookup helpers: `lookup_tool`, `lookup_subtask`, `lookup_data_type`, `all_tools`,
  `all_subtasks`, `tools_for_subtask` (walks parent chain), `tools_providing`, `tools_requiring`.
- `resolve_wrapper(tool_id)` → callable: imports dotted path from registry; cached per `tool_id`.
  Raises `WrapperNotImplementedError` (null wrapper) or `WrapperResolutionError` (bad path).
- `select_tool(subtask_id, *, require_wrapper=True, max_tier=2)` → tool entry: **(B4)** explicit,
  tier-aware backend choice replacing positional `tools_for_subtask(...)[0]`. Skips deprecated /
  above-tier / null-wrapper tools; ranks by `(tier, -priority, id)`. Optional `priority:` in
  `tools.yaml` marks the preferred backend (`tool_colabfold: 10`). `_unimplemented_tool_notes`
  surfaces declared-but-not-callable tools for the active subtask as roadmap decision points.
- `validate_inputs(spec)` → warnings list: length flags, cysteine-pair flags per sequence.
- `suggest_analyses(spec)`: panel suggestion when N > 1 peptides; disorder check suggestion for
  long targets; isoform discrimination table for multi-target tasks; cysteine suggestion.
- `make_step(tool_id, *, kwarg_overrides, binding_overrides)` → `JobStep`: **(B3)** builds a step
  from the registry call contract (call_kwargs/bindings/has_input/has_output) with per-use overrides.
- `build_roadmap(spec, feasibility)` → `Roadmap`: populates both human-readable `steps` list
  and machine-executable `jobs` list. Dispatches to `_binding_branch` or `_monomer_branch`. All
  branches build steps via `make_step`. **(B5)** Every branch appends `_analysis_steps(...)` (always-on
  optional `tool_freesasa` + `tool_analyze_sequence`; the sequence analyzer binds the peptide for
  complexes via `data_peptide_sequence`, the target for monomers). `build_roadmap` then appends
  `spec.requested_analyses` opt-ins via `_append_requested_analyses`: sequence-based set
  (`_OPT_IN_SEQ_ANALYSES`: iupred3, metapredict, localcider) and structure-based Rosetta
  hotspot chain (`_HOTSPOT_MARKER = "tool_rosetta_confidence_filter"` → optional alanine_scan
  with per-kind `interface` + optional confidence_filter; skipped for monomers).
- `Job`, `JobStep`, `BatchResult` re-exported for callers.
- `estimate_runtime()`: still placeholder constants (uncalibrated).

### Pre-flight capability registry — `preflight/capabilities/` [LIVE]
- Three-axis YAML registry. Wired wrappers (non-null): `tool_colabfold` →
  `tools.predict.predict_structure`, `tool_predict_panel` → `tools.predict.predict_panel`,
  `tool_extract_colabfold` → `tools.extract.extract_colabfold`, `tool_iupred3` →
  `tools.analyze.query_aiupred`, `tool_tmtools` → `tools.compare.compare`,
  `tool_freesasa` → `tools.analyze.analyze_structure`, and four `tool_rosetta_*` entries
  → `tools.rosetta.*` (score_interface, per_residue_energies, alanine_scan, confidence_filter_scan).
- All entries carry `tier:` (1/2/3). Tier 3 = not yet implemented or web-only-no-API.
  AIUPred (`tool_iupred3`) covers disorder locally; MSA search lives inside `predict.sh`
  (SEARCH_ONLY=1) rather than as a standalone tool entry.
- `tool_predict_panel` is the preferred entry for panel screening tasks.
- `tool_metapredict` → `tools.metapredict.predict_disorder` and `tool_localcider` →
  `tools.analyze.analyze_cider` are also wired (non-null wrappers).
- **(B5)** `tool_analyze_sequence` → `tools.analyze.analyze_sequence` registered for the always-on
  sequence analysis step; new data types `data_peptide_sequence` and `data_sequence_features` added;
  `tool_colabfold` carries `priority: 10` (preferred backend for `select_tool`).
- **Part B / B1 — declarative call contract**: wrapped tools now carry optional `call_kwargs`
  (static defaults), `bindings` (wrapper kwarg → `data_*` id or reserved `_job_dir`/`_msa_cache`),
  and `output_map` (`data_*` id → `"."`/`attr`/`attr[0]` expression on the return value). This makes
  the registry the source of truth for *how to call and chain* a tool, not just what data flows.
  `tool_colabfold` maps its PDB + A3M outputs; `tool_rosetta_alanine_scan` maps its result for the
  confidence-filter step; analysis tools are terminal (read from `job.results[tool_id]`). Added
  `data_colabfold_out_dir` to `data_types.yaml` (was an unregistered `Job.inputs` key). Integrity
  enforced by new `tests/test_registry_integrity.py` cases.

### MSA utilities — `tools/msa.py` [REFACTORED]
- `run_msa()`: deprecated — raises `FileNotFoundError` immediately. MSA search is now
  integrated into `cluster/predict.sh` (SEARCH_ONLY=1) and called by `predict_structure()`.
- `build_paired_a3m(peptide_seq, peptide_id, target_a3m, out_dir, n_target_copies)`: still
  live; constructs ColabFold multimer A3M metadata format from a cached single-chain target MSA.
  `n_target_copies` encodes cardinality for homo-oligomeric targets in the `#len,len\t1,1` header.
- `_parse_a3m()`: preserves per-row `>header` lines (required by ColabFold's `unserialize_msa`).
- SSH/SCP helpers (`_ssh`, `_scp_to`, `_scp_from`, `_wait_for_job`) imported by `tools.predict`.
- `check_connection()` → `(ok, message)`.

### Test suite — `tests/` [COMPLETE]
- `test_batch.py`: `_ensure_fasta` coverage (monomer, multimer, skip-if-set, bad-type).
- `test_registry_integrity.py`: cross-reference consistency for all three YAML files; wrapper
  import/callable check for every non-null entry; re-export contract for `reasoning` module.
- `test_analyze_sequence.py`: sequence flagging rules.
- `test_visualize.py`: unit tests for `render_py3dmol` and `render_complex_py3dmol` using
  a synthetic PDB string; integration tests (skipped if real ColabFold output absent).
- `test_extract.py`, `test_report.py`, `test_reasoning.py`, `test_predict_paths.py`: existing.
- `validate_msa_caching.py`: integration test validating A3M reuse across panel submissions.
- `conftest.py`: shared pytest fixtures.

## Jobs Run So Far

| Job | Description | Status | Notes |
|---|---|---|---|
| `smoke_test` | Short test peptide | Complete | First end-to-end run; ~8h MSA wait on remote MMseqs2 |
| `3v03` | HSA monomer (583 aa) | Complete | 5 ranked models, plots in `outputs/3v03/` |
| `vegf_monomer` | VEGF165 monomer | Complete | Monomer fold, A3M captured for panel reuse |
| `vegf165_dimer` | VEGF165 homodimer | Complete | Dimer prediction; validated panel pipeline input |
| `vegf165_dimer_pep00` | VEGF165 dimer + peptide 0 | Complete | First panel array task tested end-to-end |
| `vegf_selectivity_screen` | VEGF selectivity panel | Complete | Report generated (`tests/vegf_selectivity_report.md`) |

## What Is Still Stubbed / Incomplete

| Module / Item | Status | Notes |
|---|---|---|
| `tools/visualize.py` PyMOL | [IN PROGRESS]
| `tools/predict.py` ESMFold | Stub | `run_esmfold_api` raises `NotImplementedError` |
| `tools/extract.py` ESMFold | Stub | |
| `tool_freesasa` registry wrapper | Done | Wired to `tools.analyze.analyze_structure` |
| `preflight/reasoning.py` `estimate_runtime` | Returns 0 | Uncalibrated; formula commented out; populate from `sacct` wall-clock data |
| `composite_score` weights | Uncalibrated | 0.5/0.3/0.2 split; needs labeled binder/non-binder set |
| Most `wrapper:` fields in `tools.yaml` | Null | Boltz-1, FoldX, FreeSASA, NetSurfP-3, metapredict, localCIDER, DSSP, ANCHOR2, PDBePISA |
| `tier:` field in `tools.yaml` | Done | All 15 entries carry `tier: 1` or `tier: 2`; `build_roadmap` can now filter/warn by tier |

## Open Design Questions

- **`composite_score` calibration**: 0.5/0.3/0.2 weights externalized as `DEFAULT_SCORE_WEIGHTS`
  and passable per-call; still uncalibrated. Needs a labeled binder/non-binder set.
- **`job_id` convention**: `{target_id}_{YYYYMMDD}_{short_hash}` proposed but not enforced.
- **Pre-flight session state**: does conversation state persist in a file (resumable sessions)
  or live only in agent context? Not decided.
- **Registry coverage vs. callable coverage** — *resolved (B4)*. `select_tool` chooses backends from
  callable (wrapped, ≤ tier 2, non-deprecated) candidates ranked by `(tier, -priority, id)`, while
  `build_roadmap` surfaces declared-but-not-callable tools for the active subtask as decision-point
  notes via `_unimplemented_tool_notes`. The capability map stays visible without silently routing to
  unimplemented steps; filtering the *catalog* itself was rejected as too conservative.

## Next Priorities (Phase 2)

1. **`composite_score` calibration/ablation**: run extraction + scoring on completed panel results;
   tune weights on any binder/non-binder labels available from the VEGF selectivity screen.
2. **`tools/visualize.py` PyMOL**: implement `render_pymol()` for publication-quality static images. [IN PROGRESS, MULTIPLE VISUALIZATION METHODS EXIST]
3. **`estimate_runtime()` calibration**: populate from `sacct` wall-clock data once enough panel
   jobs have completed; current implementation returns 0.

## Recent Bot Suggestions
<!-- Bot appends entries here after each run -->
<!-- Format: [YYYY-MM-DD HH:MM] suggestion_file.md — one-line summary -->
[2026-04-24] — Implemented `analyze_sequence()` in `tools/analyze.py`; confirmed `extract_colabfold()` FileNotFoundError guard already present; updated ARCHITECTURE.md and project_state.md.
[2026-04-25 19:00] suggestion_2026-04-25_19-00.md — DEPLOY: implement `render_report()` markdown renderer; NOVEL: pre-compute target MSA + reuse across panel via paired A3M (`--target-a3m`); REFACTOR: externalize `composite_score` weights as `DEFAULT_SCORE_WEIGHTS` dict. All three were subsequently implemented.
[2026-04-28] — Updated project_state.md to reflect new `preflight/capabilities/` registry, three-layer pre-flight methodology (ontology / registry / execution), live `validate_inputs` / `suggest_analyses` / `build_roadmap`, completed `tools/report.py`, and the `--target-a3m` / `--msa-only` paths in `tools/predict.py`.
[2026-05-11] — Rewrote project_state.md to reflect Phase 1 completion: DCC SLURM execution model (no local ColabFold), `predict_panel()` + SLURM array pipeline, `tools/batch.py` orchestrator, `preflight/jobs.py` dataclasses, full `analyze.py` (including `analyze_structure`, `detect_disulfides`, `query_aiupred`), py3Dmol `visualize.py`, expanded test suite, and VEGF panel jobs run.
[2026-05-11] — Added PyRosetta subprocess bridge: `tools/rosetta.py` (caller, proteinviz env, Python 3.12) + `tools/pyrosetta_worker.py` (worker, proteinviz_pyrosetta env, Python 3.14). Three functions implemented: `score_interface` (InterfaceAnalyzerMover, dG_separated), `per_residue_energies` (REF2015 per-residue), `alanine_scan` (single-point hot-spot scan). ARCHITECTURE.md and project_state.md updated in same change.
[2026-05-13b] — Registry housekeeping: wired tool_freesasa → analyze_structure; added tier: to all entries (user set Boltz-1/DSSP/Biotite/NetSurfP-3/PDBePISA/FoldX → tier 3); deprecated tool_anchor2 (REST defunct, AIUPred covers it); added tool_rosetta_* entries (score_interface, per_residue_energies, alanine_scan, confidence_filter_scan) wired to tools.rosetta.*; added data_interface_score, data_per_residue_energy, data_ala_scan_result, data_confident_hotspot_list to data_types.yaml.
[2026-05-13] — Reconciled project_state.md with actual code: (1) report.py now consumes StructureFeatures/SequenceFeatures/disorder — removed from stubs and open questions; (2) batch.py wires predict_panel via _run_panel_predictions for binding_panel jobs — removed from open questions; (3) tool_colabfold_search / tool_dcc_msa marked deprecated: true in tools.yaml — updated registry section and stubs table; (4) visualize.py gained render_complex_py3dmol and write_complex_html — description updated; (5) test_visualize.py and validate_msa_caching.py added to test suite; (6) Next Priorities pruned to 4 remaining items.
[2026-06-05] — Part A (report wiring) complete: closed the prompt→report break. Added aggregated panel report to tools/report.py (`PanelEntry`/`PanelReportInputs`/`render_panel_report`/`write_panel_report`; one row per peptide ranked by composite; panel-wide actifpTM warning) with shared `_binding_tier`/`_fold_tier`/`_actifptm_warning` helpers. Added `build_panel_report_inputs` + `emit_reports` to tools/batch.py and `run_batch(outputs_root=...)` + CLI `--outputs-root` so a run emits reports automatically. Baseline hygiene: added pytest.ini (testpaths=tests, fixes jobs/ mis-collection); refreshed stale test_report.py (7-section) and test_reasoning.py (panel routing) expectations; xfail'd estimate_runtime formula tests until calibrated. +9 tests; suite 126 passed / 2 skipped / 2 xfailed. Part B (orchestrator+registry hybrid refactor) deferred to a fresh session — plan in PART_B_REFACTOR_PLAN.md.
[2026-06-05] — B5 follow-ups complete: (1) Disorder/CIDER/hotspot rendering wired end-to-end — `ReportInputs` + `PanelEntry` gained `disorder_iupred3`, `disorder_metapredict`, `cider`, `hotspots` fields; `build_report_inputs` + `build_panel_report_inputs` populate them from `job.results`; `_render_structural_section` renders both disorder predictors labeled separately + CIDER block (κ/FCR/NCPR/phase_region + gloss) + hotspot block (top 5 hits, confident-first); panel Per-Complex Notes shows per-peptide one-liners. (2) Rosetta hotspot chain wired — `_append_requested_analyses` handles `"tool_rosetta_confidence_filter"` in `requested_analyses`, appending optional `alanine_scan` (with per-kind `interface`: `"A_B"` single-pair / `"AB_C"` panel homodimer) then optional `confidence_filter` for binding/panel jobs only. +21 tests; suite 198 passed / 2 skipped / 2 xfailed.
