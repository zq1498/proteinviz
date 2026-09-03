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
connects the pre-flight roadmap to the execution loop, with registry-driven step dispatch (Part B)
and optional-failure isolation for supplemental analysis steps. Next milestone: calibrate
`composite_score` weights on real binder/non-binder data.

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
from the cached target MSA, then runs `colabfold_batch` independently. `predict_panel` **blocks**
until the array reaches a terminal SLURM state, then downloads per-peptide outputs to
`jobs_root/{peptide_id}/` before returning. This eliminates N−1 redundant MSA searches for a
panel of N peptides against the same target.

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
      ensemble, llps), `subtask_negative_controls`. Hierarchical; polyhierarchy allowed.
    - `data_types.yaml` — Data axis: aa_sequence, pdb_file, plddt_per_residue,
      pae_matrix, iptm_score, actifptm_score, sasa_per_residue, `data_peptide_sequence`,
      `data_sequence_features`, `data_colabfold_out_dir`, etc.
    - `tools.yaml` — each entry references subtask IDs, has_input / has_output data IDs,
      and a `wrapper` Python entrypoint (or `null` if not yet wrapped). Wrapped tools carry
      optional `call_kwargs` (static defaults), `bindings` (kwarg → data_* id or reserved
      token), and `output_map` (data_* id → attribute expression on the return value) —
      the declarative call contract that makes the registry the source of truth for how to
      call and chain tools, not just what data flows.
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
  - `calc_extra_ptm`: passes `CALC_EXTRA_PTM=1` to enable actifpTM; defaults to `CF_CALC_EXTRA_PTM`
    env var (default 1). Pass `False` for monomer runs.
- `predict_panel(target_fasta, peptides, ...)`: MSA-cached SLURM panel pipeline. **Blocks** until
  the array reaches a terminal SLURM state, then downloads per-peptide outputs to
  `jobs_root/{peptide_id}/` before returning. Peptides whose DCC task failed are absent from
  `local_output_dirs` (logged, not raised).
  - Primary job: SEARCH_ONLY=1, produces target `.a3m` in remote `msas/`.
  - Array job: `--dependency=afterok:{primary_id}`, one task per peptide; builds paired A3M
    on the DCC node via `cluster/build_a3m.py` (no local round-trip), then `colabfold_batch`.
  - Supports homo-oligomeric targets: extracts monomer chain for the MSA search, passes
    `N_TARGET_COPIES` to `array_predict.sh` so the cardinality is encoded in the A3M metadata.
  - `with_controls=True` (default off): appends composition-matched scrambled controls via
    `tools.controls.make_scrambled_controls`; manifest gains a third `is_control` column (0/1).
    Control IDs follow `{peptide_id}__scram{k}`.
- `predict()`: dispatcher to `predict_structure` or `run_esmfold_api` (stub).
- `PredictionResult` dataclass: `success`, `backend`, `output_dir`, `runtime_s`, `pdb_paths`, `a3m_paths`, `error`.
- `PanelResult` dataclass: `primary_job_id`, `array_job_id`, `n_peptides`, `remote_jobs_dir`,
  `local_output_dirs: dict[str, Path]` (peptide_id → local download dir; absent = failed task).
- CLI: `python -m tools.predict --fasta X --out Y`, `--fast`, `--pre-msa-dir`, `--panel --manifest`,
  `--no-extra-ptm`, `--check`.
- ESMFold API: stub only (`run_esmfold_api` raises `NotImplementedError`).

### Cluster scripts — `cluster/` [COMPLETE]
- `cluster/predict.sh`: SLURM prediction script deployed on DCC. Supports
  `SEARCH_ONLY=1` (MSA-only mode), `CALC_EXTRA_PTM`, `NUM_RECYCLE`, `NUM_MODELS`. Includes
  NumPy < 2 workaround for ColabFold 1.6.0 container.
- `cluster/array_predict.sh`: SLURM array job template for panel predictions. Reads peptide
  from `MANIFEST` by `SLURM_ARRAY_TASK_ID`, builds paired A3M on node, runs `colabfold_batch`.
  Supports `N_TARGET_COPIES` for homo-oligomeric targets.
- `cluster/build_a3m.py`: stdlib-only standalone (runs on DCC nodes outside Singularity) that
  imports from co-uploaded `tools/_a3m.py`. Accepts `n_target_copies` for homo-oligomers.
- `cluster/vegf165_dimer.fasta`: VEGF165 homodimer FASTA used for panel submissions.

### A3M utilities — `tools/_a3m.py` [COMPLETE]
Canonical, stdlib-only A3M format utilities. Single source of truth for both local pipeline
and DCC cluster tasks (co-uploaded by `predict_panel()`).
- `parse_a3m(path)` → `(query_seq, [(header, seq), ...])`: parses single-chain A3M; preserves
  `>` headers required by ColabFold's `unserialize_msa`.
- `build_paired_a3m(peptide_seq, peptide_id, target_a3m, out_dir, ...)`: ColabFold multimer A3M
  from a pre-computed single-chain target MSA. `n_target_copies > 1` for homo-oligomeric targets.
- Re-exported by `tools/msa.py`; imported by `cluster/build_a3m.py`.

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
- `analyze_sequence(seq)` → `SequenceFeatures`: 20+ scalar fields (MW, pI, GRAVY, instability,
  net charge, charge asymmetry, aromatic fraction, max-window hydrophobicity, sequence entropy,
  low-complexity fraction, longest homopolymer, liability motifs, camsollike score, BLOSUM mean,
  Boman index, aliphatic index, hydrophobic moment, cysteine positions); window-based flags for
  IDR/hydrophobic/PPII patterns; length flags at <5, <20, >40, >2000.
- `DEFAULT_SCREEN_THRESHOLDS`: module-level dict of hard pass/fail cutoffs for batch screening.
- `screen_sequences(sequences, ...)`: runs `analyze_sequence` on every input, applies thresholds,
  returns `(passing, figures)` with 3 diagnostic plots.
- `analyze_structure(pdb_path, interface_cutoff_a)` → `StructureFeatures`: total SASA and
  per-chain SASA via FreeSASA; interface residues (all-atom, configurable cutoff) via Biopython;
  BSA for two-chain complexes only; disulfide pairs via `detect_disulfides()`.
- `detect_disulfides(pdb_path, distance_cutoff_a=3.0)`: Cys SG–SG distance < 3.0 Å (permissive
  to catch unrelaxed structures). Returns `(chain_i, res_i, chain_j, res_j)` tuples.
- `query_aiupred(seq)`: per-residue disorder propensity via local **AIUPred** package (PyTorch).
  The IUPred3 REST API endpoint (`iupred3.elte.hu`) is defunct; AIUPred is the live replacement.
  `query_iupred3` kept as legacy alias so the `tool_iupred3` registry entry still resolves.
- `CIDERFeatures` (dataclass): `sequence`, `fcr`, `ncpr`, `kappa`, `omega`, `scd`, `delta`,
  `mean_hydropathy`, `ppii_propensity`, `isoelectric_point`, `phase_region` (int 1–5, Uversky
  diagram). All from localCIDER; global scalar descriptors only.
- `analyze_cider(seq)` → `CIDERFeatures`: localCIDER direct python_api call (proteinviz env).

### metapredict disorder — `tools/metapredict.py` + `tools/metapredict_worker.py` [COMPLETE]
metapredict V3 disorder prediction via subprocess bridge (same pattern as rosetta.py).
metapredict is only available in the `proteinviz_metapredict` conda env (Python 3.11).
- `MetapredictResult` (dataclass): `sequence`, `disorder_scores: list[float]`, `percent_disorder`,
  `disordered_domain_boundaries`, `folded_domain_boundaries`. Boundaries are 0-indexed `[start, end]`.
- `predict_disorder(seq)` → `MetapredictResult`: per-residue disorder scores [0, 1], percent
  disorder (threshold 0.5), and disordered/folded domain boundaries.
- Worker strips `tools/` from `sys.path[0]` before import to avoid shadow-importing the caller bridge.
- Stdout prefix stripping: strips everything before the first `{` (metapredict may emit warnings
  before JSON output); mirrors the PyRosetta banner-stripping pattern.

### Contact fingerprinting — `tools/fingerprint.py` [COMPLETE — Pass 1]
Epitope clustering for binding panel results: groups peptides by which target-chain
residues they contact, so a panel report can flag "these 6 peptides are all hitting the
same site" rather than listing them as 6 independent binders.
- `make_fingerprint(peptide_id, structure, target_chains=("A",), universe_size=0)` →
  `ContactFingerprint | None`: extracts target-chain contacts from
  `StructureFeatures.interface_residues`; `None` if no target-chain contacts found.
  `universe_size=0` uses `max(res_ids)`; callers should pass an explicit value for
  consistent cross-panel comparisons.
- `cluster_fingerprints(fps, threshold=0.3)` → `dict[peptide_id, "Epitope-N"]`:
  single-linkage hierarchical clustering (scipy) on Jaccard distance between contact
  vectors; `threshold` is a distance cutoff (0.3 = merge at ≥70% shared contacts).
  Handles n=0 and n=1 without invoking scipy.
- Called from `build_panel_report_inputs` (batch.py) after per-job analysis: computes a
  panel-wide `universe_size`, fingerprints every non-control entry with structure data,
  clusters, and attaches labels to `PanelEntry.epitope_cluster` via `dataclasses.replace`.
- No pLDDT gating yet — Pass 2 will add per-residue confidence filtering once real-run
  data validates the signal.

### Scrambled controls — `tools/controls.py` [COMPLETE]
Composition-matched scrambled control peptide generation for per-panel null distribution calibration.
- `make_scrambled_controls(peptides, n_per_peptide=1, seed=0, preserve_termini=False)`: takes
  `list[tuple[str, str]]` (peptide_id, seq), returns same-format controls. IDs follow
  `{peptide_id}__scram{k}`. Deterministic (same seed + inputs = same output).
- `is_control_id(peptide_id)`, `parent_id_of(control_id)`: ID classification and parent lookup.
- `CONTROL_SUFFIX_RE`: compiled regex `^(.+)__scram(\d+)$`; single source of truth for naming.
- `__scram` is a reserved delimiter; peptide IDs must not contain it.

### PyRosetta analysis — `tools/rosetta.py` + `tools/pyrosetta_worker.py` [COMPLETE]
PyRosetta (2026.19, py314_0) in a separate conda env (`proteinviz_pyrosetta`, Python 3.14,
JHU channel). Bridged via subprocess: `rosetta.py` serialises to JSON, spawns the worker,
deserialises the result. Protocol: `{"function": "<name>", "kwargs": {...}}` on stdin →
`{"result": <data>, "error": null}` on stdout.
- `score_interface(pdb_path, interface="A_B")` → `InterfaceScore`: REF2015 dG_separated
  (kcal/mol REU) with `pack_separated=True`, dSASA (Å²), and interface residue count via
  `InterfaceAnalyzerMover`. Primary metric for binding ΔG assessment.
- `per_residue_energies(pdb_path)` → `list[ResidueEnergy]`: per-residue REF2015 total energy.
  Returns both Rosetta (sequential) and PDB residue numbering.
- `alanine_scan(pdb_path, interface="A_B", rigorous=False)` → `list[AlaScanEntry]`: single-point
  ala scan on interface residues sorted by ΔΔG descending. `rigorous=False` (default): fast
  complex score delta; `rigorous=True`: ΔG_separated via IAM + FastRelax per mutant (~2–5× slower).
- `confidence_filter_scan(scan, plddt_pdb, plddt_threshold=70.0)` → `list[ConfidentHotSpot]`:
  cross-references existing scan with per-residue pLDDT from ColabFold PDB B-factor column.
  Returns confident entries first, then by ΔΔG descending. `plddt_pdb` must be the **unrelaxed**
  ColabFold PDB (relaxation zeroes B-factors).
- `ConfidentHotSpot` (dataclass): `rosetta_resnum`, `pdb_resnum`, `chain`, `resname`, `ddG`,
  `plddt`, `confident`.

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
- PyMOL PNG rendering: phase 2 stub (raises `NotImplementedError`).

### Plotting — `tools/plot.py` [COMPLETE]
- `plot_plddt_profile()`, `plot_pae_heatmap()`, `plot_model_comparison()`, `generate_all()`.
  All headless (Agg backend). CLI: `python -m tools.plot --job-dir jobs/X --out-dir outputs/X`.

### Structural comparison — `tools/compare.py` [COMPLETE]
- TM-score and Cα RMSD via `tmtools`. `ComparisonResult` dataclass. CLI available.
- `_compare_inner.py`: superseded — error stub, safe to delete.

### Report rendering — `tools/report.py` [COMPLETE]
- `render_report(inputs)`: 7-section per-complex markdown report. §4 Structural Analysis renders
  SASA/BSA/disulfide pairs (from `structure`), sequence flags (from `sequence`), both disorder
  predictors labeled separately (IUPred3 + metapredict; mean + fraction > 0.5 + plain-language
  gloss), CIDER block (κ, FCR, NCPR, phase_region), and top-5 hotspot hits. §5 auto-appends
  actifpTM reliability warning for 3+ chain complexes.
- `render_panel_report(inp)`: aggregated panel report. §3 Ranked Binders = one row per test
  peptide (controls filtered out), with an **Epitope** column when any entry has a non-null
  `epitope_cluster`. A conditional **Epitope Families** section (cluster membership bullets +
  methodology note) is inserted when clusters are present. When scrambled controls are present
  (any `z_score` not None), a **Selectivity vs Scrambled Controls** section (z-score table) is
  also inserted. Section numbering uses combined offsets so Epitope Families, Controls, and
  subsequent sections shift consistently regardless of which are present. Per-Complex Notes
  shows per-peptide disorder/CIDER/hotspot one-liners.
- `ReportInputs` (dataclass): carries optional `structure`, `sequence`, `disorder_iupred3`,
  `disorder_metapredict`, `cider`, `hotspots` analysis fields alongside core `metrics`.
- `PanelEntry` (dataclass): one complex in a panel. Control-aware fields: `is_control: bool`,
  `null_mean: float | None`, `null_sd: float | None`, `z_score: float | None` (populated by
  `build_panel_report_inputs` when controls are present). Analysis fields: `disorder_iupred3`,
  `disorder_metapredict`, `cider`, `hotspots`. Epitope field: `epitope_cluster: str | None`
  (e.g. `"Epitope-1"`; set by `build_panel_report_inputs` via `tools.fingerprint`; `None` when
  structure analysis was unavailable or the entry is a control).
- `PanelReportInputs` (dataclass): `target_id`, `task_description`, `complexes: list[PanelEntry]`,
  `methods`, `flags`, `follow_up`, `output_paths`, `weights`.
- `executive_summary()` / `_panel_executive_summary()`: adapt language to monomer vs. complex;
  share `_binding_tier` / `_fold_tier` / `_actifptm_warning`.
- `write_report(inputs, out_path)`, `write_panel_report(inp, out_path)`.

### Orchestrator — `tools/batch.py` [COMPLETE]
- `run_batch(spec, roadmap, jobs_root, abort_on_failure, outputs_root=None)` → `BatchResult`. When
  `outputs_root` is set, calls `emit_reports` after the loop (default None = no side effects beyond jobs_root).
- `_run_panel_predictions(spec, jobs, jobs_root)`: groups binding_panel jobs by target_idx; for each
  target writes a FASTA, calls `predict_panel` (blocking — waits for SLURM array, downloads results),
  then populates `data_colabfold_out_dir` / `data_pdb_complex` / `data_pdb_file` on each job. Resume:
  if `jobs_root/panel_{target_id}/done.flag` exists, skips submission and reconstructs dirs from
  filesystem. Jobs with no DCC output are marked failed before the loop.
- Per-job loop: reads `done.flag` to skip already-completed jobs. Each step runs in its own try;
  a step with `optional=True` (supplemental analysis steps) that raises is logged and skipped
  without failing the Job. Required steps that raise fail the Job. The old `_run_post_plan_analysis`
  hook is retired — analysis now runs as optional plan steps routed through the registry.
- `build_report_inputs(job, ...)` → `ReportInputs`: pulls metrics from `job.results["tool_extract_colabfold"]`,
  structure from `tool_freesasa`, sequence from `tool_analyze_sequence`, `disorder_iupred3` from
  `tool_iupred3`, `disorder_metapredict` from `tool_metapredict.disorder_scores`, `cider` from
  `tool_localcider`, `hotspots` from `tool_rosetta_confidence_filter`. All analysis fields are `None`
  when the step was not requested or failed.
- `build_panel_report_inputs(target_jobs, ...)` → `PanelReportInputs`: aggregates per-peptide Jobs;
  computes z-scores when controls are present (sets `null_mean`, `null_sd`, `z_score` on each entry);
  then runs a fingerprinting pass over non-control entries with structure data (collects target-chain
  interface residues, computes `universe_size`, calls `tools.fingerprint.make_fingerprint` +
  `cluster_fingerprints`) and attaches `epitope_cluster` labels via `dataclasses.replace`.
- `emit_reports(batch_result, ...)`: groups done jobs by `metadata["target_id"]`. binding/binding_panel
  → one aggregated `outputs/{target_id}/panel_report.md`; monomer → per-job `outputs/{job_id}/report.md`.
- Registry-driven output propagation (`_propagate_outputs`): reads the tool's `output_map` from the
  registry; resolves expressions (`"."` / `attr` / `attr[0]`) against the return value; writes into
  `job.inputs[data_id]` for downstream bindings. Terminal tools (no `output_map`) propagate nothing.
- CLI: `python -m tools.batch --targets ... --peptides ... [--outputs-root outputs/] [--abort-on-failure]`.
  Default `--outputs-root outputs/`; pass `''` to skip report emission.

### Execution dataclasses — `preflight/jobs.py` [COMPLETE]
- `Job`: unit of work with `inputs`, `plan`, `status`, `results`, `job_dir`, `metadata`.
- `JobStep`: one tool invocation with `tool_id`, `kwargs`, `input_bindings`, `consumes`, `produces`,
  and `optional: bool` (supplemental step; failure is logged and skipped, not fatal).
- `BatchResult`: wraps all jobs with `n_done`, `n_failed`, `n_skipped` properties and
  `started_at` / `finished_at` timestamps.

### Pre-flight reasoning — `preflight/reasoning.py` [COMPLETE]
- Registry loader cached via `lru_cache`; `_reload_registry()` for tests.
- Lookup helpers: `lookup_tool`, `lookup_subtask`, `lookup_data_type`, `all_tools`,
  `all_subtasks`, `tools_for_subtask` (walks parent chain), `tools_providing`, `tools_requiring`.
- `resolve_wrapper(tool_id)` → callable: imports dotted path from registry; cached per `tool_id`.
  Raises `WrapperNotImplementedError` (null wrapper) or `WrapperResolutionError` (bad path).
- `select_tool(subtask_id, *, require_wrapper=True, max_tier=2)` → tool entry: **(B4)** explicit,
  tier-aware backend choice. Skips deprecated / above-tier / null-wrapper tools; ranks by
  `(tier, -priority, id)`. Optional `priority:` in `tools.yaml` marks the preferred backend
  (`tool_colabfold: 10`). `_unimplemented_tool_notes` surfaces declared-but-not-callable tools
  for the active subtask as roadmap decision points.
- `make_step(tool_id, *, kwarg_overrides, binding_overrides, optional=False)` → `JobStep`: **(B3)**
  builds a step from the registry call contract (call_kwargs/bindings/has_input/has_output) with
  per-use overrides.
- `validate_inputs(spec)` → warnings list: length flags, cysteine-pair flags per sequence.
- `suggest_analyses(spec)`: panel suggestion when N > 1 peptides; disorder check suggestion for
  long targets; isoform discrimination table for multi-target tasks; cysteine suggestion.
- `build_roadmap(spec, feasibility)` → `Roadmap`: populates both human-readable `steps` list
  and machine-executable `jobs` list. Dispatches to `_binding_branch` or `_monomer_branch`. All
  branches build steps via `make_step`. **(B5)** Every branch appends `_analysis_steps(...)` (always-on
  optional `tool_freesasa` + `tool_analyze_sequence`; sequence analyzer binds the peptide for
  complexes via `data_peptide_sequence`, the target for monomers). `build_roadmap` then appends
  `spec.requested_analyses` opt-ins via `_append_requested_analyses`:
  - Sequence-based (`_OPT_IN_SEQ_ANALYSES`): `tool_iupred3`, `tool_metapredict`, `tool_localcider`.
  - Structure-based Rosetta hotspot chain (`_HOTSPOT_MARKER = "tool_rosetta_confidence_filter"`):
    appends optional `tool_rosetta_alanine_scan` (interface `"A_B"` for single-pair, `"AB_C"` for
    panel homodimer) then optional `tool_rosetta_confidence_filter`; skipped for monomers.
- `Job`, `JobStep`, `BatchResult` re-exported for callers.
- `estimate_runtime()`: still placeholder constants (uncalibrated).

### Pre-flight capability registry — `preflight/capabilities/` [LIVE]
- Three-axis YAML registry. Wired wrappers (non-null): `tool_colabfold` →
  `tools.predict.predict_structure`, `tool_predict_panel` → `tools.predict.predict_panel`,
  `tool_extract_colabfold` → `tools.extract.extract_colabfold`, `tool_iupred3` →
  `tools.analyze.query_aiupred`, `tool_tmtools` → `tools.compare.compare`,
  `tool_freesasa` → `tools.analyze.analyze_structure`, `tool_analyze_sequence` →
  `tools.analyze.analyze_sequence`, `tool_metapredict` → `tools.metapredict.predict_disorder`,
  `tool_localcider` → `tools.analyze.analyze_cider`, `tool_scrambled_controls` →
  `tools.controls.make_scrambled_controls`, and four `tool_rosetta_*` entries →
  `tools.rosetta.*` (score_interface, per_residue_energies, alanine_scan, confidence_filter_scan).
- All entries carry `tier:` (1/2/3). `tool_colabfold` carries `priority: 10` (preferred backend).
- New subtask `subtask_negative_controls` added for scrambled control generation.
- New data types: `data_peptide_sequence` (binder chain for sequence analysis), `data_sequence_features`
  (analyze_sequence output), `data_colabfold_out_dir` (downloaded-results directory for extractor).
- Declarative call contract (B1): `call_kwargs`, `bindings`, `output_map` on each wrapped tool.
  `tool_rosetta_alanine_scan` maps `"." → data_ala_scan_result` (consumed by confidence_filter's
  `scan` binding). Integrity enforced by `tests/test_registry_integrity.py`.

### MSA utilities — `tools/msa.py` [REFACTORED]
- `run_msa()`: deprecated — raises `FileNotFoundError` immediately. MSA search is now
  integrated into `cluster/predict.sh` (SEARCH_ONLY=1) and called by `predict_structure()`.
- `build_paired_a3m`: re-exported from `tools._a3m` (canonical source).
- SSH/SCP helpers (`_ssh`, `_scp_to`, `_scp_from`, `_wait_for_job`) imported by `tools.predict`.
- `check_connection()` → `(ok, message)`.

### Test suite — `tests/` [COMPLETE]
Suite: **239 passed, 2 skipped, 3 xfailed** (xfail: `estimate_runtime` formula tests, uncalibrated;
`strict=True` on all three so a calibration fix that makes them pass forces the xfail marker's
removal instead of silently xpassing).
- `test_batch.py`: `_ensure_fasta` coverage (monomer, multimer, skip-if-set, bad-type).
- `test_registry_integrity.py`: cross-reference consistency for all three YAML files; wrapper
  import/callable check for every non-null entry; re-export contract for `reasoning` module.
- `test_analyze_sequence.py`: sequence flagging rules.
- `test_visualize.py`: unit tests for `render_py3dmol` and `render_complex_py3dmol` using
  a synthetic PDB string; integration tests (skipped if real ColabFold output absent).
- `test_controls.py`: scrambled controls generation and ID classification.
- `test_fingerprint.py`: `_to_vector`, `jaccard_matrix`, `make_fingerprint`, `cluster_fingerprints`
  (n=0/n=1 edge cases, threshold behavior).
- `test_a3m.py`: `parse_a3m` (multiline rows, `#` chain-break markers, malformed input) and
  `build_paired_a3m` (metadata header, `n_target_copies`, custom complex name, MSA row passthrough)
  against synthetic A3M fixtures.
- `test_compare.py`: `parse_ca_coords` (chain selection, duplicate-residue-number guard) and
  `compare` (identical-structure TM-score/RMSD sanity check, optional score-JSON enrichment).
- `test_plot.py`: `plot_plddt_profile`, `plot_pae_heatmap` (including the no-PAE `ValueError`),
  `plot_model_comparison` (with/without TM-score overlay) against a synthetic `ExtractedMetrics`.
- `test_extract.py`, `test_report.py`, `test_reasoning.py`, `test_predict_paths.py`: existing.
- `validate_msa_caching.py`: integration test validating A3M reuse across panel submissions.
- `conftest.py`: shared pytest fixtures.
- `pytest.ini`: `testpaths = tests` (prevents jobs/ mis-collection).

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
| `tools/visualize.py` PyMOL | In progress | `render_pymol()` raises `NotImplementedError`; multiple other viewers exist |
| `tools/predict.py` ESMFold | Stub | `run_esmfold_api` raises `NotImplementedError` |
| `tools/extract.py` ESMFold | Stub | |
| `preflight/reasoning.py` `estimate_runtime` | Returns placeholder | Uncalibrated; populate from `sacct` wall-clock data |
| `composite_score` weights | Uncalibrated | 0.5/0.3/0.2 split; needs labeled binder/non-binder set |
| Boltz-1, FoldX, NetSurfP-3, DSSP, PDBePISA | Null wrappers | Tier 2/3 entries in tools.yaml; declared but not callable |

## Open Design Questions

- **`composite_score` calibration**: 0.5/0.3/0.2 weights externalized as `DEFAULT_SCORE_WEIGHTS`
  and passable per-call; still uncalibrated. Needs a labeled binder/non-binder set.
- **`job_id` convention**: `{target_id}_{YYYYMMDD}_{short_hash}` proposed but not enforced.
- **Pre-flight session state**: does conversation state persist in a file (resumable sessions)
  or live only in agent context? Not decided.
- **Heterodimeric targets**: `build_paired_a3m` only supports single-chain or homo-oligomeric
  targets. Heterodimers would require two monomer MSAs, a 3-chain metadata header, and interleaved
  MSA rows. Out of scope until a concrete use case arises.

## Next Priorities (Phase 2)

1. **`composite_score` calibration/ablation**: run extraction + scoring on completed panel results;
   tune weights on any binder/non-binder labels available from the VEGF selectivity screen.
2. **`tools/visualize.py` PyMOL**: implement `render_pymol()` for publication-quality static images.
3. **`estimate_runtime()` calibration**: populate from `sacct` wall-clock data once enough panel
   jobs have completed; current implementation returns placeholder constants.

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
[2026-06-06] — Synced project_state.md with ARCHITECTURE.md: fixed stale `predict_panel` description (now blocking, not fire-and-forget); added `PanelResult.local_output_dirs`; added sections for `tools/_a3m.py`, `tools/metapredict.py`, and `tools/controls.py`; updated reasoning.py and batch.py for Part B additions (make_step B3, select_tool B4, _analysis_steps/_append_requested_analyses B5, optional-step isolation, _run_post_plan_analysis retirement); updated report.py and registry for controls/z-score/§4 selectivity section; updated test count to 198/2/2; pruned resolved stubs from the stubs table.
[2026-06-07] — Pass 1 epitope fingerprinting: added `tools/fingerprint.py` (`ContactFingerprint`, `make_fingerprint`, `_to_vector`, `jaccard_matrix`, `cluster_fingerprints`); `PanelEntry` gained `epitope_cluster: str | None`; `build_panel_report_inputs` computes fingerprints per entry and assigns cluster labels; `render_panel_report` adds a conditional Epitope Families section + an Epitope column in Ranked Binders when clusters are present. No pLDDT gating in Pass 1. This backfills a project_state.md gap — ARCHITECTURE.md was updated at the time but this file wasn't; caught during the 2026-09-02 tightening pass below.
[2026-09-02] — Architecture tightening pass (bounded, mechanical — see the `project_architecture_tightening` plan; job_id/done.flag convention and real `estimate_runtime` calibration were explicitly deferred, both need info only Zach has). (1) Landed the in-flight Pass 1 fingerprinting + scrambled-controls + `tools/_a3m.py` refactor work that had been sitting uncommitted. (2) Fixed `test_estimate_runtime_scales_with_pairs`, which passed vacuously (0 == 80*0) under the uncalibrated placeholder — added an explicit `one > 0` assertion and set `strict=True` on all three `estimate_runtime` xfails so a future calibration fix is caught here instead of silently xpassing. (3) Added `tests/test_compare.py`, `tests/test_plot.py`, `tests/test_a3m.py` — previously-untested pure modules. (4) Doc consolidation: expanded the `composite_score` open design question in ARCHITECTURE.md to summarize `composite_score_minimum_implementation.md` (what's implemented — mean/sd z-score only — vs. not — robust z-score, empirical p-value, multiple-testing correction); documented `papers_index.yaml`'s intentional root placement (per its own guide doc, not a stray file); backfilled this file's missing fingerprint.py section (see entry above). +41 tests; suite 239 passed / 2 skipped / 3 xfailed.
