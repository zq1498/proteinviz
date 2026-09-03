# Architecture

Living description of how the codebase is laid out and how data flows through it. **This document must be updated in the same change that modifies code** (see CLAUDE.md § Living architecture doc). If you are reading this and it disagrees with the code, trust the code and fix this file.

For annotated payload examples (ColabFold JSON schema, `ExtractedMetrics` instances, `Roadmap` / `Job` shapes, smoke fixture notes) see [DATA_SHAPES.md](DATA_SHAPES.md).

Last verified against: 2026-09-02 session (2) — removed `estimate_runtime()` entirely (Zach: scavenger-partition GPU wall-clock is too variable for a numeric ETA to be useful; most of it is CPU-bound MSA search time anyway). `FeasibilityReport` lost its `estimated_runtime_s` field — it was already unread inside `build_roadmap` (the `feasibility` param was accepted but never inspected), so this is a pure removal, not a behavior change. Deleted the three now-obsolete xfail tests in `test_reasoning.py` rather than leaving dead placeholder assertions around. CLAUDE.md's pre-flight § "Feasibility check" and § "Roadmap" steps were reworded to ask for qualitative runtime-risk flags (MSA depth, sequence length, queue contention) instead of numeric time estimates. Suite: 239 passed, 2 skipped, 0 xfailed.

Last verified against: 2026-09-02 session (1) — architecture tightening pass (no functional code changes): added `tests/test_compare.py`, `tests/test_plot.py`, `tests/test_a3m.py` (previously untested pure modules); fixed `tests/test_reasoning.py::test_estimate_runtime_scales_with_pairs`, which was passing vacuously (0 == 80*0) under `estimate_runtime`'s uncalibrated placeholder — added an explicit `one > 0` assertion and flipped all three `estimate_runtime` xfail markers to `strict=True` so a future calibration fix is caught here instead of silently xpassing (superseded by session (2) above — the function was removed outright); doc consolidation — expanded the `composite_score` open design question to summarize [composite_score_minimum_implementation.md](composite_score_minimum_implementation.md) and what's implemented vs. not; documented `papers_index.yaml`'s intentional root placement.

2026-06-07 session — Pass 1 epitope fingerprinting: `tools/fingerprint.py` added (`ContactFingerprint`, `make_fingerprint`, `_to_vector`, `jaccard_matrix`, `cluster_fingerprints`); `PanelEntry` gained `epitope_cluster: str | None`; `build_panel_report_inputs` computes fingerprints per entry and assigns cluster labels before returning; `render_panel_report` adds a conditional "§4 Epitope Families" section (cluster membership bullets + methodology note) and an "Epitope" column in the Ranked Binders table when clusters are present; section numbering extended with `ep_offset` so Epitope Families, Controls, and subsequent sections shift consistently. No pLDDT gating in Pass 1; Pass 2 will add per-residue filtering once real-run data validates the signal. Suite: all prior tests pass; new `tests/test_fingerprint.py` + epitope tests in `tests/test_report.py` added.

2026-06-05 session — Part A (report wiring) complete: `tools/report.py` gained the aggregated panel report (`PanelEntry`, `PanelReportInputs`, `render_panel_report`, `write_panel_report`) with shared `_binding_tier`/`_fold_tier`/`_actifptm_warning` helpers; `tools/batch.py` gained `build_panel_report_inputs` + `emit_reports` and `run_batch(outputs_root=...)` / CLI `--outputs-root`, closing the prompt→report path. Baseline test hygiene: added `pytest.ini` (`testpaths = tests`); refreshed stale `test_report.py` (7-section format) and `test_reasoning.py` (panel routing) expectations; `estimate_runtime` formula tests marked xfail until calibrated. Suite: 126 passed, 2 skipped, 2 xfailed.

2026-06-05 — Scrambled controls: added `tools/controls.py` (`make_scrambled_controls`, `is_control_id`, `parent_id_of`); `predict_panel` gained `with_controls`/`controls_per_peptide`/`controls_seed` params that inject composition-matched scrambled peptides into the panel manifest (3-column TSV: `peptide_id`, `seq`, `is_control`); `PanelEntry` gained `is_control`, `null_mean`, `null_sd`, `z_score` fields; `build_panel_report_inputs` post-processes entries to compute z-scores; `render_panel_report` adds a conditional "Selectivity vs Scrambled Controls" section (section 4) with section renumbering when controls are present; registry: `subtask_negative_controls` + `tool_scrambled_controls` added.

2026-06-05 — B5 follow-ups: (1) Disorder/CIDER/hotspot rendering wired end-to-end: `ReportInputs` gained `disorder_iupred3`, `disorder_metapredict`, `cider`, `hotspots` fields (replacing the formerly-unpopulated `disorder` stub); `PanelEntry` gained the same four fields; `build_report_inputs` + `build_panel_report_inputs` populate them from `job.results`; `_render_structural_section` renders both disorder predictors labeled separately + a CIDER block (κ, FCR, NCPR, phase_region with plain-language gloss) + a hotspot block (top 5 hits). Panel Per-Complex Notes shows per-peptide disorder/CIDER/hotspot one-liners. (2) Rosetta hotspot chain wired: `_append_requested_analyses` now handles `"tool_rosetta_confidence_filter"` in `requested_analyses` — for binding/panel jobs it appends optional `tool_rosetta_alanine_scan` (with per-kind interface: `"A_B"` for single-pair, `"AB_C"` for panel homodimer) then optional `tool_rosetta_confidence_filter`; monomers skip these steps. `_HOTSPOT_MARKER`, `_INTERFACE_SINGLE_PAIR`, `_INTERFACE_PANEL_HOMODIMER` constants added to `reasoning.py`. Suite: 198 passed, 2 skipped, 2 xfailed.

## Quick navigation — key functions by stage (may not be exhaustive)

**Pre-flight:** `build_roadmap`, `validate_inputs`, `suggest_analyses` (all in `preflight/reasoning.py`)

**Prediction & extraction:** `predict_structure`, `predict_panel` (`tools/predict.py`) → `extract_colabfold` (`tools/extract.py`) → `composite_score` (`tools/extract.py`)

**Analysis:** `analyze_sequence`, `analyze_structure`, `analyze_cider`, `query_aiupred` (`tools/analyze.py`); `predict_disorder` (`tools/metapredict.py`); `score_interface`, `alanine_scan` (`tools/rosetta.py`); `build_paired_a3m` (`tools/msa.py`)

**Controls:** `make_scrambled_controls`, `is_control_id`, `parent_id_of` (`tools/controls.py`)

**Visualization & reporting:** `write_html_from_metrics` (`tools/visualize.py`) → `render_report`, `write_report` (`tools/report.py`)

**Orchestration:** `run_batch`, `build_report_inputs` (`tools/batch.py`)

## Agent flow (end-to-end)

How a natural-language task becomes an executed pipeline. The agent (Claude in conversation) drives steps 1–4; the orchestrator drives step 5; the report layer drives step 6.

```
                 ┌──────────────────────────────────────────────┐
                 │ 1. INTAKE                                    │
                 │    user task (natural language)              │
                 │           │                                  │
                 │           ▼                                  │
                 │    agent parses → TaskSpec                   │
                 │    (targets, peptides, objective, metrics)   │
                 └────────────────────┬─────────────────────────┘
                                      │
                 ┌────────────────────▼─────────────────────────┐
                 │ 2. CAPABILITY LOOKUP                         │
                 │    agent queries preflight.reasoning:        │
                 │      tools_for_subtask(subtask_binding)      │
                 │      tools_providing(data_iptm_score)        │
                 │      tools_requiring(data_pdb_complex)       │
                 │    ─ reads capabilities/*.yaml               │
                 │    ─ returns matching tool entries           │
                 └────────────────────┬─────────────────────────┘
                                      │
                 ┌────────────────────▼─────────────────────────┐
                 │ 3. PRE-FLIGHT REASONING                      │
                 │    validate_inputs(spec)                     │
                 │      ─ sequence-level flags from heuristics  │
                 │    suggest_analyses(spec)                    │
                 │      ─ "target has IDR → run IUPred3 first?" │
                 │    build_roadmap(spec, capabilities)         │
                 │      ─ ordered job list w/ decision points   │
                 └────────────────────┬─────────────────────────┘
                                      │
                 ┌────────────────────▼─────────────────────────┐
                 │ 4. USER CONFIRMATION                         │
                 │    agent presents roadmap + flags            │
                 │    user confirms or modifies                 │
                 │    ─ HARD GATE: no execution before approval │
                 └────────────────────┬─────────────────────────┘
                                      │
                 ┌────────────────────▼─────────────────────────┐
                 │ 5. ORCHESTRATION (per pair)                  │
                 │    for each (target, peptide):               │
                 │      write_fasta() ──────────► jobs/{id}/    │
                 │      tool.wrapper(...)  via tools.yaml       │
                 │        e.g. run_colabfold → PDB + JSON       │
                 │      extract_colabfold() → ExtractedMetrics  │
                 │      analyze_sequence() → SequenceFeatures   │
                 │      analyze_structure() → StructureFeatures │
                 │      composite_score() → ranking             │
                 └────────────────────┬─────────────────────────┘
                                      │
                 ┌────────────────────▼─────────────────────────┐
                 │ 6. REPORT                                    │
                 │    render_report(ReportInputs) →             │
                 │       outputs/{job_id}/report.md             │
                 │    plus PNGs (plot.py), comparison tables    │
                 └──────────────────────────────────────────────┘
```

Three layers, mirroring EDAM's separation:

- **Ontology layer** (`heuristics.md`, `task_ontology_documents_v2.html`): conceptual knowledge — what metrics matter, what thresholds mean, what flags to raise. Read by the agent.
- **Registry layer** (`capabilities/*.yaml`): operational catalog — which tool produces what, what it requires. Queried by `reasoning.py` and the agent.
- **Execution layer** (`tools/*.py` + orchestrator): the actual subprocess and Python calls.

## Data flow (per-pair execution)

```
sequence ──► predict ──► PDB + scores JSON
                            │
                            ▼
                         extract ──► ExtractedMetrics
                            │
                            ▼
                         analyze ──► SequenceFeatures + StructureFeatures
                            │
                            ▼
                         visualize / plot ──► PNG + HTML
                            │
                            ▼
                         report ──► outputs/{job_id}/report.md
```

## Module reference

### [tools/predict.py](tools/predict.py)

Prediction backend wrappers. Backend selected via `PREDICT_BACKEND` env var or `--backend` flag.

ColabFold runs on DCC via SLURM. SSH/SCP helpers are imported from `tools.msa`. The cluster job template lives at `cluster/predict.sh` (deployed at `DCC_PREDICT_SCRIPT`; see its own section below). Workflow: upload FASTA → (optionally upload pre-computed A3Ms) → sbatch the template → poll via `_wait_for_job` → download PDB + score JSON + A3M files.

- `PredictionResult` (dataclass): `success`, `backend`, `output_dir`, `runtime_s`, `pdb_paths`, `a3m_paths`, `error`.
- `check_backend(backend) -> (ok, msg)` — **implemented**: SSH echo check against DCC.
- `predict_structure(fasta, out_dir, fast=False, num_recycle=None, num_models=None, job_name=None, calc_extra_ptm=None, pre_msa_dir=None)` — **implemented**: uploads FASTA to `DCC_CF_JOBS_DIR/{job_name}/`, submits via `sbatch --export=ALL,...` pointing at the existing DCC script (`DCC_PREDICT_SCRIPT`), polls for completion, downloads PDB + JSON + A3Ms. `fast=True` uses 1 model / 1 recycle. `calc_extra_ptm` passes `CALC_EXTRA_PTM=1` to the cluster script to enable `--calc-extra-ptm` (actifpTM); defaults to `CF_CALC_EXTRA_PTM` env var (default 1). Pass `False` explicitly for monomer runs where actifpTM is meaningless. `pre_msa_dir`: local directory of pre-computed `.a3m` files; when set, files are uploaded to `remote_out/msas/` before sbatch — `predict.sh` detects existing A3Ms and skips `colabfold_search`. After any successful job, A3Ms are downloaded to `out_dir/msas/` and returned in `result.a3m_paths` for reuse with `build_paired_a3m`.
- `_parse_target_fasta(fasta) -> (name, monomer_seq, n_chains)` — **implemented**: extracts the first chain sequence and chain count from a (possibly multimer) FASTA. Used by `predict_panel` to detect homo-oligomeric targets and avoid feeding a paired-format A3M (which `_parse_a3m` cannot consume) to the array tasks.
- `predict_panel(target_fasta, peptides, jobs_root, job_name=None, num_recycle=None, num_models=None, calc_extra_ptm=None, with_controls=False, controls_per_peptide=1, controls_seed=0)` — **implemented**: two-stage panel submission with automatic homodimer support. If the target FASTA contains multiple chains (`:` separator), extracts the unique monomer chain, writes a temp monomer FASTA for the primary MSA job, and passes `N_TARGET_COPIES={n_chains}` to the array job so each task's paired A3M gets the correct cardinality. Uploads target FASTA + manifest TSV + `build_a3m.py` + `array_predict.sh` to DCC; submits primary job with `SEARCH_ONLY=1` (colabfold_search only, no GPU); submits array job with `--dependency=afterok:{primary_id} --array=0-{N-1}`. **Blocks** until the array reaches a terminal SLURM state, then downloads per-peptide outputs to `jobs_root/{peptide_id}/`. Peptides whose DCC task failed are absent from `local_output_dirs` (logged, not raised). Each array task builds its paired A3M on the DCC node from the primary's cached MSA (no local→DCC round-trip for per-peptide A3Ms) and runs colabfold_batch. `peptides` is `list[tuple[str, str]]` (peptide_id, peptide_seq). `with_controls=True` (default off) appends composition-matched scrambled controls (via `tools.controls.make_scrambled_controls`) before the manifest is written; manifest gains a third `is_control` column (0/1). The SLURM script ignores the third column. Control IDs follow `{peptide_id}__scram{k}`.  `n_peptides` in the returned `PanelResult` counts test + controls.
- `PanelResult` (dataclass): `primary_job_id`, `array_job_id`, `n_peptides`, `remote_jobs_dir`, `local_output_dirs: dict[str, Path]` (peptide_id → local download dir; absent entries indicate failed array tasks).
- `run_esmfold_api(fasta, out_dir)` — stub.
- `write_fasta(sequence, name, path)` — **implemented**: convenience helper for inline sequences.
- `predict(fasta, out_dir, backend, fast=False, num_recycle=None, num_models=None, job_name=None, calc_extra_ptm=None, pre_msa_dir=None)` — single-prediction dispatcher.
- CLI: `python -m tools.predict --check`, `--fasta X --out Y`, or `--sequence MKTAYIAK... --out Y`. Flags: `--fast`, `--num-recycle N`, `--num-models N`, `--job-name LABEL`, `--no-extra-ptm`, `--pre-msa-dir DIR`. Panel mode: `--panel --fasta TARGET.fasta --manifest peptides.tsv [--jobs-root jobs/]`.

Env vars: `PREDICT_BACKEND` (default: colabfold), `CF_NUM_MODELS` (default: 5), `CF_NUM_RECYCLES` (default: 3), `CF_CALC_EXTRA_PTM` (default: 1 — enables actifpTM), `DCC_PREDICT_SCRIPT` (default: `{DCC_WORK}/colabfold/install_scripts/04_run_prediction.sh`), `DCC_CF_JOBS_DIR` (default: `{DCC_WORK}/colabfold/jobs`). DCC connectivity vars (`DCC_HOST`, `DCC_KEY`, `DCC_WORK`) are read via helpers imported from `tools.msa`.

### [tools/extract.py](tools/extract.py)

Parses backend outputs into a uniform record regardless of backend.

- `ExtractedMetrics` (dataclass): `model_id`, `backend`, `pdb_path`, pLDDT stats, per-residue pLDDT, `per_chain_plddt` (dict, chain-id → mean pLDDT), PAE, ipTM, actifpTM, ranking_score.
- `_parse_pdb_chain_plddt(pdb_path) -> dict[str, float]` — **implemented**: reads ATOM records, groups B-factor (pLDDT) values by chain ID, returns per-chain means. Returns `{}` if PDB not found. Called by `extract_colabfold` for every ranked model.
- `extract_colabfold(out_dir)` — **implemented**: globs `*_scores_rank_*_*.json`, parses pLDDT list, PAE matrix, ptm; matches each JSON to its unrelaxed PDB; populates `per_chain_plddt` via `_parse_pdb_chain_plddt`.
- `extract_esmfold` — stub.
- `DEFAULT_SCORE_WEIGHTS` — module-level dict `{"primary": 0.5, "mean_plddt": 0.3, "interface_plddt": 0.2}`. Single source of truth for score weights; pass a different dict to experiment without touching the function.
- `composite_score(m, interface_plddt=None, weights=DEFAULT_SCORE_WEIGHTS)` — **implemented as placeholder**: `w["primary"]·primary + w["mean_plddt"]·mean_plddt/100 + w["interface_plddt"]·interface_plddt/100`. Primary signal: actifpTM if present **and** n_chains ∈ {0, 2}; else ipTM; else ptm. The n_chains guard prevents actifpTM from inflating scores in 3+ chain complexes (e.g. homodimer + peptide) where it is dominated by the well-structured dimer interface rather than the peptide-target interface. Weights untuned; recalibrate on first real runs.
- `to_json(metrics, path)` — serializer for per-job metrics.json.

ColabFold score JSON schema (confirmed from smoke test): `plddt` (list[float], per-residue), `pae` (N×N list[list[float]]), `max_pae` (float), `ptm` (float). `iptm`/`actifptm` only present in multimer/complex runs.

### [tools/analyze.py](tools/analyze.py)

Sequence and structure feature extraction. Rules live in [preflight/heuristics.md](preflight/heuristics.md).

- `SequenceFeatures` (dataclass): `sequence`, `length`, `molecular_weight`, `pI`, `gravy` (mean Kyte-Doolittle), `instability_index` (Biopython; flagged only when > 40 AND GRAVY > 1.0 — hydrophobic+unstable = aggregation risk; not flagged unconditionally because >40 is common in globular proteins), `net_charge` (Henderson-Hasselbalch at pH 7.4), `charge_asymmetry` (|pos_frac − neg_frac|, pos = K/R/H, neg = D/E), `aromatic_fraction` (F/W/Y), `max_window_hydrophobicity` (max mean KD over any 5-residue window), `sequence_entropy` (Shannon bits; max ≈ 4.32), `low_complexity_fraction` (fraction of residues in entropy < 1.5 windows), `longest_homopolymer_run`, `contains_liability_motif` (NG/DP/NP/PP/CP), `camsollike_score` (solubility heuristic: gravy − 0.5·charge_asym + 2·aromatic), `blosum_mean_self_score` (mean BLOSUM62 diagonal), `boman_index` (mean Boman ΔGsolv kcal/mol; > 1.0 = protein-binding potential), `aliphatic_index` (100·(X_A + 2.9·X_V + 3.9·(X_I + X_L)); thermal stability proxy), `hydrophobic_moment` (max Eisenberg μH over 18-res windows at α-helix δ=100°; > ~0.4 = amphiphilic helix), `cysteine_positions`, `flags`.
- `StructureFeatures` (dataclass): SASA (total and per-chain), interface residues `list[tuple[str, int]]`, buried surface area, `disulfide_pairs: list[tuple[str, int, str, int]]` — chain_i, res_i, chain_j, res_j.
- `analyze_sequence(seq)` — **implemented**: all `SequenceFeatures` scalar fields plus window-based flags for charged IDR (>50% DEKR in 10-res), hydrophobic/TM (>70% ACFILMVWY in 10-res), PPII/proline-rich (≥25% Pro in 10-res or PPP stretch), disulfide-prone cysteine pairs (≤8 res apart), extreme net charge (|charge| > 6), low-complexity sequence (entropy < 2.2 bits, only for n ≥ 10), high low-complexity fraction (> 0.5), homopolymer runs (≥ 3), liability motifs, and instability > 40 with GRAVY > 1.0 (aggregation risk). Module-level helpers: `_net_charge`, `_sequence_entropy`, `_low_complexity_fraction`, `_max_window_hydrophobicity`, `_longest_homopolymer_run`, `_blosum_mean_self_score`, `_boman_index`, `_max_hydrophobic_moment`. Module-level constants: `_KD_SCALE`, `_PKA_TABLE`, `_BLOSUM62` (loaded once at import), `_LIABILITY_MOTIFS`, `_BOMAN_SCALE`, `_EISENBERG_SCALE`.
- `DEFAULT_SCREEN_THRESHOLDS` — module-level dict of hard pass/fail cutoffs for batch screening (distinct from `analyze_sequence` flags, which are expert commentary). Keys are `SequenceFeatures` field names; values are `(threshold, direction)` tuples where direction ∈ `{"max", "min", "absmax"}`. Defaults: entropy ≥ 2.2, |net\_charge| ≤ 8, homopolymer ≤ 2, charge\_asymmetry ≤ 0.5, max\_window\_hydrophobicity ≤ 2.5, aromatic\_fraction ≤ 0.35, low\_complexity\_fraction ≤ 0.5, no liability motifs.
- `screen_sequences(sequences, thresholds=None, output_dir=None)` — **implemented**: runs `analyze_sequence` on every input sequence, applies `DEFAULT_SCREEN_THRESHOLDS` (or caller override), returns `(passing: list[SequenceFeatures], figures: list[Figure])`. Figures: [0] per-threshold pass/fail stacked bar chart, [1] 4×2 metric distribution grid (passing vs failing, with threshold lines), [2] |net charge| vs max-window hydrophobicity scatter. Saves PNGs to `output_dir` if provided. Lazily imports `numpy` and `matplotlib` (Agg backend).
- `analyze_structure(pdb, interface_cutoff_a=5.0)` — **implemented**: FreeSASA for total SASA and per-chain SASA; Biopython + NumPy for all-atom interface residue detection (any atom within cutoff of any atom on another chain); BSA = SASA(A_isolated) + SASA(B_isolated) − SASA(complex) via `freesasa.structureArray` (two-chain only, `None` for monomers/3+ chains); calls `detect_disulfides()` internally.
- `detect_disulfides(pdb, distance_cutoff_a=3.0)` — **implemented**: Biopython PDBParser; iterates chains explicitly so intra- vs inter-chain pairs are unambiguous; returns `(chain_i, res_i, chain_j, res_j)` tuples. Default 3.0 Å is permissive for unrelaxed structures (canonical SG–SG bond ~2.05 Å).
- `query_aiupred(seq)` — **implemented**: local AIUPred package (PyTorch, proteinviz conda env); returns per-residue disorder scores [0,1]. `query_iupred3` is a module-level alias for backward compatibility. The iupred3.elte.hu REST endpoint is defunct; `invocation` in tools.yaml updated to `local_python`.
- `CIDERFeatures` (dataclass): `sequence`, `fcr`, `ncpr`, `kappa`, `omega`, `scd`, `delta`, `mean_hydropathy`, `ppii_propensity`, `isoelectric_point`, `phase_region`. All from localCIDER `SequenceParameters`; global scalar descriptors only (no per-residue linear profiles in initial implementation). `mean_hydropathy` uses the Uversky normalized scale [0, 1], not Kyte-Doolittle. `phase_region` is an integer 1–5 from the Uversky phase diagram; region 1 = strongly disordered.
- `analyze_cider(seq)` — **implemented**: localCIDER direct python_api call (available in proteinviz env — no subprocess bridge needed). Returns `CIDERFeatures`.

### [tools/fingerprint.py](tools/fingerprint.py)

Contact fingerprinting and epitope clustering for binding panel results. Called by `build_panel_report_inputs` after per-job analysis is complete; operates on `StructureFeatures.interface_residues` (already collected via `tool_freesasa`).

- `ContactFingerprint` (dataclass): `peptide_id`, `target_chains: tuple[str, ...]`, `residue_ids: list[int]` (1-indexed target contact residues, deduplicated and sorted), `vector: np.ndarray` (bool, length = universe_size).
- `make_fingerprint(peptide_id, structure, target_chains=("A",), universe_size=0) -> ContactFingerprint | None` — **implemented**: extracts target-chain contacts from `structure.interface_residues` (tuples, not objects — index access `r[0]`/`r[1]`); returns None if no target-chain contacts found. `universe_size=0` uses `max(res_ids)` as vector length; callers must pass an explicit `universe_size` for consistent cross-panel comparisons.
- `_to_vector(res_ids, universe_size) -> np.ndarray` — converts sorted 1-indexed residue list to a bool array; clips silently if any id exceeds `universe_size`.
- `jaccard_matrix(fps) -> np.ndarray` — symmetric n×n pairwise Jaccard similarity matrix; `union=0` case (two empty vectors) returns 0.
- `cluster_fingerprints(fps, threshold=0.3) -> dict[str, str]` — **implemented**: single-linkage hierarchical clustering (scipy) on Jaccard distance; returns `peptide_id → "Epitope-N"` label. Guards for n=0 (empty dict) and n=1 (single "Epitope-1" without invoking scipy). `threshold` is a Jaccard *distance* cutoff (default 0.3 = at least 70% shared contacts to merge).

**Integration:** `build_panel_report_inputs` (batch.py) runs a pre-pass over `fp_candidates` to find `universe_size = max(target chain residue numbers)`, then calls `make_fingerprint` per entry and `cluster_fingerprints` across the panel. Results are attached to `PanelEntry.epitope_cluster` via `dataclasses.replace`. Controls (`is_control=True`) and entries without structure analysis are excluded from fingerprinting.

**Pass 1 limitation:** No pLDDT gating — all interface residues are included regardless of confidence. Pass 2 will add per-residue pLDDT filtering (requires mapping `ExtractedMetrics.per_residue_plddt` flat list to `(chain, res_id)` using chain boundary offsets from sequence lengths).

### [tools/controls.py](tools/controls.py)

Composition-matched scrambled control peptide generation for per-panel null distribution calibration.

- `CONTROL_SUFFIX_RE` — compiled regex `^(.+)__scram(\d+)$`; the single source of truth for the control-ID naming convention.
- `make_scrambled_controls(peptides, n_per_peptide=1, seed=0, preserve_termini=False)` — **implemented**: takes `list[tuple[str, str]]` (peptide_id, seq), returns same-format list of controls. Each control ID is `{peptide_id}__scram{k}`. Same seed + same inputs = same output (deterministic). `preserve_termini=True` fixes the first and last residue and shuffles the inner positions only (useful when terminal residues are constrained by the assay).
- `_shuffle(seq, rng, preserve_termini)` — internal; shuffles in-place via `list`, `rng.shuffle`, join.
- `is_control_id(peptide_id)` — returns `True` if the ID matches `CONTROL_SUFFIX_RE`.
- `parent_id_of(control_id)` — returns the parent test-peptide ID (group 1 of the regex), or `None` if not a control ID.

**Warning:** `__scram` is a reserved delimiter. Peptide IDs that contain `__scram` but do not match the full `{parent}__scram{k}` pattern are undefined behaviour in `build_panel_report_inputs`.

### [tools/metapredict.py](tools/metapredict.py) + [tools/metapredict_worker.py](tools/metapredict_worker.py)

metapredict V3 disorder prediction via subprocess bridge. metapredict is only available in the `proteinviz_metapredict` conda env (Python 3.11). Mirrors the rosetta.py/pyrosetta_worker.py pattern exactly.

Protocol: `{"function": "<name>", "kwargs": {...}}` on stdin → `{"result": <data>, "error": null}` on stdout. metapredict may emit warnings to stdout before the JSON (e.g. window-size adjustments for short sequences); the caller strips everything before the first `{` before parsing, mirroring the pyrosetta banner-stripping pattern.

**Implementation note:** `metapredict_worker.py` must strip its own directory from `sys.path` before `import metapredict as mp`. Because the worker lives in `tools/`, Python adds `tools/` to `sys.path[0]` when running it as a script — without this guard, the import resolves to `tools/metapredict.py` (the caller bridge) and creates an infinite subprocess loop.

**tools/metapredict.py** — caller side (proteinviz env):
- `MetapredictResult` (dataclass): `sequence`, `disorder_scores: list[float]`, `percent_disorder: float`, `disordered_domain_boundaries: list[list[int]]`, `folded_domain_boundaries: list[list[int]]`. Boundaries are 0-indexed `[start, end]` pairs as returned by `metapredict.predict_disorder_domains()`.
- `_call_worker(function, **kwargs)` — spawns the worker, parses JSON response, raises `RuntimeError` on error or non-JSON stdout.
- `predict_disorder(seq) -> MetapredictResult` — **implemented**: per-residue disorder scores [0, 1], percent disorder (threshold 0.5), and disordered/folded domain boundaries.

**tools/metapredict_worker.py** — worker side (proteinviz_metapredict env, Python 3.11):
- `_predict_disorder(sequence)` — calls `metapredict.predict_disorder()`, `predict_disorder_domains()`, and `percent_disorder()`; returns JSON-serialisable dict.
- `_FUNCTIONS` dispatch table maps function names to callables.

To add a new metapredict analysis: implement `_my_function(**kwargs)` in the worker, add it to `_FUNCTIONS`, add a public wrapper + dataclass in `metapredict.py`.

### [tools/rosetta.py](tools/rosetta.py) + [tools/pyrosetta_worker.py](tools/pyrosetta_worker.py)

PyRosetta analysis via subprocess bridge. PyRosetta (2026.19, py314_0) is only available in the `proteinviz_pyrosetta` conda env (Python 3.14, JHU channel). The main proteinviz env runs Python 3.12 and has no compatible wheel. Rather than maintain two full-stack environments, PyRosetta is isolated and called via subprocess: `rosetta.py` serialises arguments to JSON, spawns `pyrosetta_worker.py` under `/home/zach/miniconda3/envs/proteinviz_pyrosetta/bin/python`, and deserialises the result. All data crosses the boundary as plain JSON (no shared memory).

Protocol: `{"function": "<name>", "kwargs": {...}}` on stdin → `{"result": <data>, "error": null}` on stdout.

**tools/rosetta.py** — caller side (proteinviz env):
- `InterfaceScore` (dataclass): `interface`, `total_score_reu`, `dG_separated`, `dSASA`, `n_interface_residues`.
- `ResidueEnergy` (dataclass): `rosetta_resnum`, `pdb_resnum`, `chain`, `resname`, `total_energy`.
- `AlaScanEntry` (dataclass): `rosetta_resnum`, `pdb_resnum`, `chain`, `resname`, `ddG`.
- `_call_worker(function, **kwargs)` — spawns the worker, parses JSON response, raises `RuntimeError` on worker error or non-JSON stdout. Strips the PyRosetta license banner (written to C-level fd 1 at import, unsuppressable via `sys.stdout`) by scanning stdout for the first `{` before calling `json.loads`.
- `score_interface(pdb_path, interface="A_B") -> InterfaceScore` — **implemented**: REF2015 dG_separated (pack_separated=True), dSASA, interface residue count via InterfaceAnalyzerMover.
- `per_residue_energies(pdb_path) -> list[ResidueEnergy]` — **implemented**: per-residue total REF2015 energy; returns both Rosetta and PDB residue numbers.
- `alanine_scan(pdb_path, interface="A_B", rigorous=False) -> list[AlaScanEntry]` — **implemented**: single-point ala scan on interface residues sorted by ΔΔG descending; Ala/Gly skipped. `rigorous=False` (default): ΔΔG = sfxn(complex_mut) − sfxn(complex_wt) — fast, suitable for relative ranking. `rigorous=True`: ΔΔG = ΔG_separated(mut) − ΔG_separated(wt) via InterfaceAnalyzerMover (pack_separated=True) with FastRelax applied to each mutant — correct binding ddG, ~2–5× slower, use for hit characterisation.
- `confidence_filter_scan(scan, plddt_pdb, plddt_threshold=70.0) -> list[ConfidentHotSpot]` — **implemented**: cross-references an existing `list[AlaScanEntry]` with per-residue pLDDT read from the ColabFold PDB B-factor column. Flags each entry as `confident` (pLDDT ≥ threshold) or uncertain; returns confident entries first, then by ΔΔG descending within each tier. Takes the scan as input rather than re-running `alanine_scan` so rigorous-mode results are not recomputed. `plddt_pdb` must be the **unrelaxed** ColabFold PDB — Amber relaxation zeroes B-factors, so pass the relaxed PDB to `alanine_scan` for scoring geometry and the unrelaxed PDB here for pLDDT annotation.
- `_plddt_map_from_pdb(pdb_path) -> dict[tuple[str, int], float]` — reads ATOM B-factor column, returns `{(chain, pdb_resnum): mean_plddt}`. Private helper for `confidence_filter_scan`; no new dependencies.
- `ConfidentHotSpot` (dataclass): `rosetta_resnum`, `pdb_resnum`, `chain`, `resname`, `ddG`, `plddt`, `confident`.

**tools/pyrosetta_worker.py** — worker side (proteinviz_pyrosetta env, Python 3.14):
- `_FUNCTIONS` dispatch table maps function names to callables.
- `_score_interface`, `_per_residue_energies`, `_alanine_scan(pdb_path, interface, rigorous)` — PyRosetta implementations of the above. `_alanine_scan` branches on `rigorous`: fast path uses total-complex score delta; rigorous path applies FastRelax then IAM per mutant for binding-specific ddG.
- `__main__` block: calls `pyrosetta.init("-mute all")`, dispatches, writes JSON to stdout.

To add a new PyRosetta analysis: implement `_my_function` in the worker, add it to `_FUNCTIONS`, add a public wrapper + dataclass in `rosetta.py`.

PyRosetta 2026.19 API quirks (verify on upgrade): `InterfaceAnalyzerMover.get_interface_set()` returns interface residue numbers as `std::set<unsigned long>` — `get_interface_residues()` does not exist in this release. `mutate_residue()` requires single-letter amino acid codes (`"A"` not `"ALA"`); three-letter codes raise a C++ character-type error.

### [tools/visualize.py](tools/visualize.py)

py3Dmol HTML rendering (browser-based, CDN-loaded). PyMOL PNG rendering planned for phase 2.

- `render_py3dmol(pdb_path, color_by="plddt")` — **implemented**: returns a self-contained HTML string. `color_by` options: `"plddt"` (RCSB B-factor gradient, orange→yellow→cyan→blue), `"chain"` (chainHetatm), `"spectrum"` (N→C rainbow). PDB text is embedded in a JS template literal with backtick/dollar/backslash escaping.
- `render_complex_py3dmol(pdb_path, peptide_chain="C")` — **implemented**: homodimer+peptide viewer. Target chains are coloured by pLDDT gradient; the designated peptide chain is rendered as hot-pink cartoon + thin sticks. Legend labels the peptide chain. Default `"C"` matches the ColabFold chain assignment for panel screens (A:B = target homodimer, C = peptide).
- `write_html(pdb_path, out_path, color_by="plddt")` — **implemented**: writes `render_py3dmol` output to disk, creates parent dirs.
- `write_complex_html(pdb_path, out_path, peptide_chain="C")` — **implemented**: writes `render_complex_py3dmol` output to disk.
- `write_html_from_metrics(metrics, out_dir, peptide_chain=None)` — **implemented**: pipeline integration point; takes an `ExtractedMetrics`, derives filename from `metrics.model_id`, calls the complex viewer if `peptide_chain` is given, else the standard viewer.
- `render_interface_surface(pdb_path, interface_residues=None, chains_to_show=None, peptide_chain="C")` — **implemented**: self-contained HTML showing the cartoon coloured by pLDDT + a Gaussian surface with interface residues highlighted. If `interface_residues` is None it computes them — via `_get_peptide_interface_residues` when a `peptide_chain` is present, otherwise from `analyze_structure().interface_residues`. `chains_to_show` filters which chains get a bright surface (others render as desaturated grey context).
- `write_interface_surface_html(pdb_path, out_path, interface_residues=None, chains_to_show=None, peptide_chain="C")` — **implemented**: writes `render_interface_surface` output to disk.
- `_get_peptide_interface_residues(pdb_path, peptide_chain, interface_cutoff_a=5.0)` — **implemented**: interface residues (chain_id, resnum) within the cutoff of the peptide chain.
- `render_pymol(pdb_path, out_png, highlight_chains=None)` — phase 2 stub; raises `NotImplementedError`.
- `_escape_pdb`, `_plddt_legend`, `_html_page`, `_html_page_raw` — shared helpers.

The former `render_interface` stub (type error: `chain_b: Path` instead of `str`) was replaced by the working `render_interface_surface` / `write_interface_surface_html` pair above.

### [tools/report.py](tools/report.py)

Markdown report generation, two granularities: **per-complex** (`render_report` — one complex, its ranked models) and **aggregated panel** (`render_panel_report` — one target, many peptides, one row per complex). `emit_reports` in `tools/batch.py` picks the right one per task kind.

- `ReportInputs` (dataclass): `job_id`, `task_description`, `metrics: list[ExtractedMetrics]`, `methods`, `flags`, `follow_up`, `output_paths`, `weights` (optional — passed to `composite_score`; defaults to `DEFAULT_SCORE_WEIGHTS`), `structure: StructureFeatures | None` (from `analyze_structure()`), `sequence: SequenceFeatures | None` (from `analyze_sequence()`), `disorder_iupred3: list[float] | None` (per-residue scores from `tool_iupred3`), `disorder_metapredict: list[float] | None` (from `tool_metapredict.disorder_scores`), `cider: CIDERFeatures | None` (from `tool_localcider`), `hotspots: list[ConfidentHotSpot] | None` (from `tool_rosetta_confidence_filter`). All analysis fields default to `None`.
- `render_report(inputs)` — **implemented**: 7-section per-complex markdown report. §1 Executive Summary, §2 Methods, §3 Per-Complex Results, **§4 Structural Analysis** (SASA, BSA, disulfide pairs from `structure`; sequence flags from `sequence`; disorder from `disorder_iupred3` + `disorder_metapredict` each labeled separately with mean score + percent > 0.5 + plain-language gloss; CIDER block with κ, FCR, NCPR, phase_region label; hotspot block with top 5 hits; shows "_No structural analysis data available._" when all fields are None), §5 Flagged Observations, §6 Suggested Follow-Up, §7 Raw Output Locations. Per-complex table includes a "Per-chain pLDDT" column. §5 auto-appends an actifpTM reliability warning for 3+ chain complexes.
- `_render_structural_section(inp) -> list[str]` — renders §4; always present so section numbers are stable across runs.
- `write_report(inputs, out_path)` — writes to disk, creates parent dirs.
- `PanelEntry` (dataclass): one complex in a panel — `peptide_id`, `metrics: ExtractedMetrics` (the best/rank-1 model), optional `structure`, `sequence`, `label`, `job_dir`. Control-aware fields: `is_control: bool` (True for scrambled controls), `null_mean: float | None`, `null_sd: float | None`, `z_score: float | None` (populated by `build_panel_report_inputs` when controls are present). Analysis fields: `disorder_iupred3`, `disorder_metapredict`, `cider`, `hotspots` (same semantics as `ReportInputs`; rendered in Per-Complex Notes). Epitope field: `epitope_cluster: str | None` (e.g. `"Epitope-1"` — set by `build_panel_report_inputs` via `tools.fingerprint`; None when structure analysis was unavailable or the entry is a control).
- `PanelReportInputs` (dataclass): `target_id`, `task_description`, `complexes: list[PanelEntry]`, `methods`, `flags`, `follow_up`, `output_paths`, `weights`. `complexes` may include both test and control entries; the render function filters controls out of the ranked table.
- `render_panel_report(inp)` — **implemented**: aggregated panel report; section count depends on which optional sections are triggered. Base: 7 sections. With epitopes (`any PanelEntry.epitope_cluster is not None`): inserts §4 **Epitope Families** (cluster membership bullets + methodology note), adds "Epitope" column to §3 Ranked Binders table, shifts all later sections by 1 (`ep_offset`). With controls (`any z_score is not None`): inserts Selectivity section (z-score table) at `§(4+ep_offset)`, shifts later sections by 1 more (`zscore_offset`). Section numbers use `total_offset = ep_offset + zscore_offset`. Control entries excluded from §3 Ranked Binders. §(5+total_offset) emits actifpTM warning once for the whole panel. Matches CLAUDE.md § Reporting format.
- `write_panel_report(inp, out_path)` — writes to disk, creates parent dirs.
- `_format_chain_plddt(per_chain)` formats `{"A": 75.9, ...}` as `"A:75.9, ..."`; `_peptide_chain_plddt(m)` returns the last chain id's pLDDT (ColabFold assigns the peptide after the target chains).
- `executive_summary(ranked, flags, weights)` — **implemented**: one-paragraph per-complex interpretation with confidence tier. `_panel_executive_summary(...)` is the panel equivalent (top candidate + strong/moderate counts + score span). Both share `_binding_tier(score)` (>0.70 strong / 0.50–0.70 moderate / <0.50 weak), `_fold_tier(ptm)` (monomer tiers), and `_actifptm_warning(m)` (3+ chain reliability warning), extracted so per-complex and panel reports stay consistent.

### [preflight/reasoning.py](preflight/reasoning.py)

Deterministic helpers the agent calls during pre-flight. The agent's natural-language reasoning still drives the conversation with the user; this module exists so calculations (runtime, validation, capability lookup) are reproducible.

Loads the `capabilities/*.yaml` registry once at import (cached via `lru_cache`) and exposes lookup helpers consumed by the pre-flight reasoning functions. Re-exports `Job`, `JobStep`, `BatchResult` from `preflight.jobs` for caller convenience.

Dataclasses: `TaskSpec` (targets, peptides, objective, user_requested_metrics, and **`requested_analyses: list[str]`** — opt-in analysis tool ids the agent confirmed in pre-flight, read by `build_roadmap`), `FeasibilityReport`, `Roadmap` (with `jobs: list[Job]` field carrying the executable plan).

Registry helpers — **implemented**:
- `lookup_tool(tool_id)`, `lookup_subtask(subtask_id)`, `lookup_data_type(data_id)` — direct ID resolution.
- `all_tools()`, `all_subtasks()` — enumerate.
- `tools_for_subtask(subtask_id, include_children=True)` — walks the subtask parent tree, returns tools annotated with the subtask or any descendant.
- `tools_providing(data_type_id)` — tools whose `has_output` includes the data type.
- `tools_requiring(data_type_id)` — tools whose `has_input` includes the data type.
- `resolve_wrapper(tool_id) -> Callable` — imports and returns the Python callable named in the tool's `wrapper` field. Cached per tool_id. Raises `WrapperNotImplementedError` if the field is null (tool declared but not yet wrapped) or `WrapperResolutionError` if the dotted path can't be imported. The orchestrator uses this to dispatch each `JobStep` to its concrete implementation without hardcoding tool→callable mappings.
- `make_step(tool_id, *, kwarg_overrides=None, binding_overrides=None, optional=False) -> JobStep` — **(B3/B5)** builds a `JobStep` from the tool's registry call contract: `kwargs` = `call_kwargs` merged with `kwarg_overrides`; `input_bindings` = `bindings` merged with `binding_overrides`; `consumes`/`produces` = `has_input`/`has_output`; `optional` marks supplemental (analysis) steps. The roadmap branches use it so each branch is "a list of tool ids + minimal overrides" instead of hand-written `JobStep`s. Overrides win on collision (e.g. `_single_pair_branch` overrides `calc_extra_ptm=True`; `_panel_branch` rebinds `out_dir → data_colabfold_out_dir`).
- `_analysis_steps(*, peptide_sequence)` — **(B5)** returns the always-on, optional analysis steps appended to every binding/monomer plan: `tool_freesasa` (structure) + `tool_analyze_sequence` (sequence). For binding/panel jobs (`peptide_sequence=True`) the sequence analyzer is rebound `seq → data_peptide_sequence` so it runs on the peptide; monomers use the default `data_aa_sequence` binding. Replaces the retired `batch._run_post_plan_analysis` hook.
- `_append_requested_analyses(jobs, requested)` — **(B5 + follow-up)** appends opt-in analyses from `TaskSpec.requested_analyses` to each job's plan in-place. Sequence-based (`_OPT_IN_SEQ_ANALYSES`: `tool_iupred3`, `tool_metapredict`, `tool_localcider`) — added for all job kinds, peptide-bound for complexes. Structure-based hotspot chain: `_HOTSPOT_MARKER = "tool_rosetta_confidence_filter"` triggers optional `tool_rosetta_alanine_scan` (with per-kind `interface` kwarg: `"A_B"` for single-pair, `"AB_C"` for homodimer panels) followed by optional `tool_rosetta_confidence_filter`; skipped for monomers (no interface). Unknown ids are silently skipped. Returns the sorted list of ids actually added.
- `select_tool(subtask_id, *, require_wrapper=True, max_tier=2) -> dict` — **(B4)** picks the preferred callable tool for a subtask *explicitly* (replaces the old positional `tools_for_subtask(...)[0]`). Drops deprecated tools, tools above `max_tier`, and (unless `require_wrapper=False`) null-wrapper tools; ranks the rest by `(tier, -priority, id)`. The optional `priority: int` field in `tools.yaml` (higher wins) marks the preferred backend — `tool_colabfold` has `priority: 10` so it wins over a future `tool_boltz1`. Raises `RuntimeError` if nothing qualifies. The prediction branches now call `select_tool("subtask_binding")` / `select_tool("subtask_folding")`.
- `_unimplemented_tool_notes(subtask_id, *, max_tier=2) -> list[str]` — **(B4)** decision-point notes for tools registered under the active subtask but not yet callable (null wrapper or above `max_tier`). `build_roadmap` appends these so the capability map stays visible (e.g. "Boltz-1 … registered … but not yet callable") instead of silently ignoring or routing to an unimplemented step. This realises the resolution of the former "registry coverage vs callable coverage" open question.

Pre-flight reasoning — **implemented**:
- `validate_inputs(spec)` — applies sequence-level heuristics (length flags, cysteine-pair disulfide hint, OOM warning) per target/peptide.
- `suggest_analyses(spec)` — proposes additions the user did not request, resolved against the registry (e.g. "run disorder check ({tools_for_subtask('subtask_disorder_core')})").
- `build_roadmap(spec, feasibility)` — composes the ordered execution plan; populates both `Roadmap.steps` (human-readable summary) and `Roadmap.jobs` (executable `Job` list consumed by the orchestrator). Branches: `_binding_branch(spec)` routes by peptide count — for N > 1 calls `_panel_branch`, for N = 1 calls `_single_pair_branch`; `_monomer_branch(spec)` emits one Job per target (single chain). `_panel_branch` emits extraction-only Jobs (kind="binding_panel") with `data_colabfold_out_dir=None`; `run_batch` fills this before the job loop. `_single_pair_branch` emits full predict + extract Jobs (kind="binding"). **As of B3 all three branches build their `JobStep`s via `make_step`** (registry call contract), so they no longer hand-write `input_bindings`/`kwargs`. **As of B5 every branch appends `_analysis_steps(...)`** (always-on optional structure + sequence analysis), and `build_roadmap` then appends any `spec.requested_analyses` opt-ins via `_append_requested_analyses`. Other task shapes (mutation, ppi, disorder-only) get their own branches as those tasks come up.

### [tools/_a3m.py](tools/_a3m.py)

Canonical, stdlib-only A3M format utilities. Single source of truth for both the local pipeline and the DCC cluster tasks.

- `parse_a3m(path: Path | str) -> (query_seq, [(header, seq), ...])` — parses a single-chain A3M, returning the query sequence and a list of `(header_line, sequence)` tuples. Headers are preserved — ColabFold requires a `>` header before every MSA row; discarding them causes the entire file to be read as one giant concatenated sequence. Does not handle the `#{chain_lens}\t{cardinalities}` multimer metadata format (single-chain target A3Ms only).
- `build_paired_a3m(peptide_seq, peptide_id, target_a3m, out_dir, complex_name=None, n_target_copies=1) -> Path` — builds a ColabFold multimer A3M for a target:peptide complex from a pre-computed single-chain target MSA. Uses the metadata header format that `colabfold/batch.py:unserialize_msa` expects — first line is `#{target_len},{pep_len}\t{n_target_copies},1`; query sequence is concatenated (no `:` separator); MSA rows are written with their original `>` headers; peptide entry uses `{target_len}` gap characters followed by the peptide sequence so `_split_by_aln` assigns it to chain B. `n_target_copies` > 1 for homo-oligomeric targets (e.g. VEGF homodimer → 2). Eliminates redundant `colabfold_search` calls when screening a peptide panel against one target.

`tools/msa.py` re-exports `build_paired_a3m` from here. `cluster/build_a3m.py` imports from `_a3m` at runtime on DCC nodes (co-uploaded by `predict_panel()`).

### [tools/msa.py](tools/msa.py)

DCC connectivity helpers and MSA construction utilities.

SSH/SCP helpers (`_ssh`, `_scp_to`, `_scp_from`, `_ssh_opts`, `_dcc_host`, `_dcc_key`, `_dcc_work`) are imported by `tools.predict` for SLURM submission. `_wait_for_job` and `_job_status` are also re-used by `tools.predict`.

- `build_paired_a3m` — re-exported from `tools._a3m`; see that module for full docs.
- `check_connection()` — SSH health check, used by `--check` flag.
- `run_msa(fasta, out_dir, job_name)` — **deprecated stub**: raises `FileNotFoundError` immediately. `cluster/msa_search.sh` was removed when MSA search was folded into `cluster/predict.sh`. Obtain target A3Ms via `predict_structure()` on a monomer FASTA and use `result.a3m_paths`.
- `MSAResult` (dataclass): `success`, `job_id`, `local_a3m`, `final_state`, `error`.
- CLI: `python -m tools.msa --check`.

Env vars: `DCC_HOST`, `DCC_KEY` (SSH key path), `DCC_WORK`.

SSH uses `BatchMode=yes` (key auth only, no password prompts).

### cluster/msa_search.sh

**Deleted.** MSA search is now handled inside `cluster/predict.sh` (runs `colabfold_search` then `colabfold_batch` in a single GPU job). `tools/msa.run_msa()` is a deprecated stub that raises immediately with an explanation.

### [cluster/predict.sh](cluster/predict.sh)

SLURM job template for structure prediction. Deployed on DCC at `DCC_PREDICT_SCRIPT`; submitted by `tools/predict.py` via `sbatch --export=ALL,FASTA=...,OUTDIR=...,NUM_RECYCLE=...,NUM_MODELS=...,CALC_EXTRA_PTM=...`. Runs `colabfold_search` (MSA, CPU-bound) then `colabfold_batch` (prediction, GPU-bound) inside a Singularity container. Optional overrides: `NUM_SEEDS`, `USE_AMBER`, `MSA_MODE`, `MODEL_TYPE`, `CALC_EXTRA_PTM` (default 1 — enables `--calc-extra-ptm` so actifpTM appears in score JSON; set to 0 for monomer runs where it is meaningless), `SEARCH_ONLY` (default 0 — set to 1 to exit after `colabfold_search`, skipping prediction; used as the primary job in panel screens). The `colabfold_batch` call uses a `BATCH_FLAGS` array, keeping conditional flags (amber, calc-extra-ptm) in one place.

### [cluster/build_a3m.py](cluster/build_a3m.py)

Thin CLI wrapper for DCC compute nodes. Uploaded alongside `tools/_a3m.py` by `predict_panel()` and called by each array task — no container required.

Imports `build_paired_a3m` and `parse_a3m` from `_a3m` (co-uploaded sibling). Exposes a `build(target_a3m, peptide_seq, peptide_id, ...)` adapter whose argument order matches the shell invocation in `array_predict.sh` (target path first), delegating to `_a3m.build_paired_a3m` with reordered args.

CLI: `python3 build_a3m.py <target_a3m> <peptide_seq> <peptide_id> <out_dir> [<complex_name>] [<n_target_copies>]`.

### [cluster/array_predict.sh](cluster/array_predict.sh)

SLURM array job template for parallel panel predictions. Submitted by `predict_panel()` with `--dependency=afterok:{primary_id} --array=0-{N-1}`. Each task reads its peptide from the manifest TSV using `$SLURM_ARRAY_TASK_ID`, calls `build_a3m.py` to construct the paired A3M on the DCC node (no local→DCC round-trip per peptide), then runs the `colabfold_batch` stage with the same Singularity + numpy workaround as `predict.sh`. Per-task logs at `/work/zq36/colabfold/logs/array_{array_job_id}_{task_id}.log`. Per-peptide outputs land at `JOBS_DIR/{peptide_id}/output/`. Required exports: `PRIMARY_MSA_DIR`, `MANIFEST`, `JOBS_DIR`, `BUILD_A3M_SCRIPT`. Optional: `N_TARGET_COPIES` (default 1 — set to 2 for homodimer targets), `NUM_RECYCLE`, `NUM_MODELS`, `CALC_EXTRA_PTM`.

### [cluster/install_localcolabfold.sh](cluster/install_localcolabfold.sh)

One-time setup script to run on DCC login node. Installs pixi to `/work/zq36/.pixi` and LocalColabFold to `/work/zq36/localcolabfold`. No sudo required — pixi and bundled CUDA runtime are fully user-space; cluster GPU nodes supply the driver.

### [tools/plot.py](tools/plot.py)

Matplotlib visualizations, all headless (Agg backend — no display required). Outputs saved as PNG to `outputs/{job_id}/`.

- `plot_plddt_profile(metrics, out_path)` — **implemented**: per-residue pLDDT line plot, filled by AlphaFold confidence tier (matches RCSB/UniProt colouring scheme).
- `plot_pae_heatmap(metrics, out_path)` — **implemented**: N×N PAE matrix as green-scale heatmap, 0–30 Å range.
- `plot_model_comparison(all_metrics, out_path, tmscore_vs_crystal=None)` — **implemented**: grouped bar chart of mean pLDDT and pTM across all 5 ranked models; optionally overlays TM-score vs crystal as a red dashed line. Parameter name matches `generate_all`'s `tmscore_vs_crystal`.
- `generate_all(job_dir, out_dir, tmscore_vs_crystal=None)` — convenience wrapper; calls all three and returns saved paths.
- CLI: `python -m tools.plot --job-dir jobs/X --out-dir outputs/X [--tmscore 0.976]`.

### [tools/batch.py](tools/batch.py)

Task-agnostic orchestrator. Walks `Roadmap.jobs` from `preflight.reasoning.build_roadmap`, dispatches each `JobStep` through `resolve_wrapper`, isolates per-job failures, and supports done.flag-based resume. Owns no domain knowledge — which tools to call is decided upstream by the registry and the Roadmap branch.

- `run_batch(spec, roadmap, *, jobs_root=Path("jobs"), abort_on_failure=False, outputs_root=None) -> BatchResult` — **implemented**: if any jobs have kind="binding_panel", calls `_run_panel_predictions` first (blocking); then walks every non-pre-failed Job through the per-job loop. If `outputs_root` is set, calls `emit_reports` after the loop to write user-facing reports; default `None` keeps `run_batch` side-effect-free apart from `jobs_root` (tests rely on this).
- `_run_panel_predictions(spec, jobs, jobs_root)` — **implemented**: groups binding_panel jobs by target_idx; for each target writes a FASTA, calls `predict_panel` (blocking — waits for SLURM array, downloads results), then populates `data_colabfold_out_dir` / `data_pdb_complex` / `data_pdb_file` on each job. Resume: if `jobs_root/panel_{target_id}/done.flag` exists, skips submission and reconstructs dirs from the filesystem. Jobs with no DCC output are marked failed before the loop.
- `_run_one_job` — per-job loop: resume check → mkdir → ensure_fasta → for each step: resolve_wrapper, bind_inputs, call, propagate outputs → write done.flag → log + persist `job_meta.json`. **(B5)** Each step runs in its own try: a step marked `optional=True` (the analysis steps) that raises is logged and skipped without failing the Job; a required step that raises fails the Job as before. The old `_run_post_plan_analysis` hook is retired — analysis now runs as real (optional) plan steps routed through the registry.
- `build_report_inputs(job, task_description, methods, flags, follow_up, weights) -> ReportInputs` — **implemented**: assembles a `ReportInputs` from a completed Job. Pulls `ExtractedMetrics` from `job.results["tool_extract_colabfold"]`, `StructureFeatures` from `job.results["tool_freesasa"]`, `SequenceFeatures` from `job.results["tool_analyze_sequence"]`, `disorder_iupred3` from `tool_iupred3`, `disorder_metapredict` from `tool_metapredict.disorder_scores`, `cider` from `tool_localcider`, `hotspots` from `tool_rosetta_confidence_filter`. All analysis fields are `None` when the step was not requested or failed (optional steps).
- `build_panel_report_inputs(target_jobs, target_id, task_description, *, methods, flags, follow_up, weights) -> PanelReportInputs` — **implemented**: aggregates a target's per-peptide Jobs into one ranked panel input. For each job picks the best model (max `composite_score`) from `job.results["tool_extract_colabfold"]` and attaches structure/sequence/disorder/CIDER/hotspot analysis from the same tool-id result keys; jobs with no metrics are skipped. After z-score enrichment: runs a fingerprinting pass — collects all target-chain (chain A) interface residues across non-control entries with structure, computes `universe_size`, calls `tools.fingerprint.make_fingerprint` + `cluster_fingerprints`, and attaches cluster labels to entries via `dataclasses.replace`.
- `emit_reports(batch_result, *, outputs_root, task_description, methods=None, weights=None) -> list[Path]` — **implemented**: groups done jobs by `metadata["target_id"]`. binding / binding_panel kinds → one aggregated `outputs/{target_id}/panel_report.md`; monomer kinds → per-job `outputs/{job_id}/report.md`. Returns the paths written. This is the closing hop of the prompt→report path; `run_batch(outputs_root=...)` and the CLI call it automatically.
- `_bind_inputs(step, job_inputs, job_dir)` — resolves `step.input_bindings` keys against `job.inputs`, with reserved value `_job_dir` resolving to the working directory.
- `_ensure_fasta(inputs, job_dir)` — derives `data_fasta_file` from `data_aa_sequence`. `str` → monomer FASTA, `list[str]` → ColabFold multimer FASTA (chains joined with `:`).
- `_propagate_outputs(job, tool_id, result)` — **registry-driven (B2)**: reads the tool's `output_map` from the registry and, for each `data_* id -> expr`, resolves `expr` against the wrapper's return value via `_resolve_output_expr` and writes it into `job.inputs[data_id]` so later steps' bindings can consume it. Skips `None` and empty-collection outputs (a step that produced nothing does not shadow an upstream value). A tool with no `output_map` (analysis tools, read straight from `job.results[tool_id]`) propagates nothing. Reproduces the old `pdb_paths[0] -> data_pdb_complex/data_pdb_file` behaviour for `tool_colabfold`.
- `_resolve_output_expr(result, expr)` — resolves an `output_map` expression with no `eval`: `"."` → whole result, `attr` → `getattr(result, attr)`, `attr[0]` → first element of a list attribute (`None` if empty/missing).
- Logging: per-job `batch.log`, `failure.log`, and `job_meta.json` written to each `jobs/{job_id}/` directory.
- CLI: `python -m tools.batch --targets SEQ1 [SEQ2 ...] [--peptides P1 P2 ...] [--objective "..."] [--jobs-root jobs/] [--outputs-root outputs/] [--abort-on-failure]`. `--outputs-root` defaults to `outputs/`; pass `''` to skip report emission. With defaults this is a one-call prompt→report path.

Plan shape (post Part B): a binding/monomer Job's plan is `predict → extract → tool_freesasa (optional) → tool_analyze_sequence (optional)`, plus any opt-in analyses appended from `TaskSpec.requested_analyses`. Panel Jobs skip the predict step (handled upstream by `_run_panel_predictions`) and start at `extract`. Single-pair `_single_pair_branch` uses `predict_structure` per pair (no MSA caching); multi-peptide panels route through `_panel_branch` + `_run_panel_predictions` (MSA-cached SLURM array). The analysis layer reaches the report through the registry — no hardcoded post-plan hook.

### [tools/compare.py](tools/compare.py)

Structural comparison using `tmtools` imported directly (native venv install; no subprocess layer).

- `ComparisonResult` (dataclass): `pred_pdb`, `ref_pdb`, `tmscore_pred`, `tmscore_ref`, `rmsd_ca`, `n_pred`, `n_ref`, optional pLDDT fields.
- `parse_ca_coords(pdb_path, chain=None) -> (coords, seq)` — **implemented**: extracts Cα (N, 3) float32 array and one-letter sequence from PDB ATOM records. Chain defaults to first chain encountered. Inlined from the former `_compare_inner.py`.
- `compare(pred_pdb, ref_pdb, score_json=None, pred_chain=None, ref_chain=None)` — **implemented**: calls `tmtools.tm_align()` directly; optionally attaches pLDDT + pTM from ColabFold score JSON.
- `print_report(r)` — **implemented**: metrics table + plain-language TM-score interpretation.
- CLI: `python -m tools.compare --pred X --ref Y [--scores Z]`.

`tools/_compare_inner.py` — superseded; file replaced with an error stub. Safe to delete.

### [preflight/heuristics.md](preflight/heuristics.md)

Human-readable rule set. Tables for sequence-level flags, metric interpretation, target-specific priors, analysis suggestions, pre-flight checklist. Mirrored in CLAUDE.md § Domain heuristics; keep in sync.

### [preflight/jobs.py](preflight/jobs.py)

Task-agnostic execution dataclasses consumed by the orchestrator:

- `JobStep` — one tool invocation with `tool_id`, static `kwargs`, `input_bindings` (wrapper kwarg name → `Job.inputs` key), advisory `consumes` / `produces` mirroring registry I/O, and `optional: bool` (**B5** — when True a failing step is logged and skipped instead of failing the Job; used for supplemental analysis steps). Reserved binding values: `_job_dir` resolves to the job's working directory at call time. `_msa_cache` (a cached A3M path keyed by sequence sha1) is declared in the docstring and `BatchResult.msa_cache_keys` is scaffolded, but **not yet implemented** in `_bind_inputs` — the field is a placeholder for future MSA caching in single-pair runs.
- `Job` — one unit of work end-to-end. `inputs` is a named bundle keyed by data_type ids; `results` is keyed by tool_id; `metadata` holds task-specific labels (target_id, kind, etc.) that have no role in execution. `status` ∈ `{pending, running, done, skipped, failed}`.
- `BatchResult` — the run result with `spec`, `roadmap`, `jobs`, `msa_cache_keys`, and `n_done` / `n_failed` / `n_skipped` properties.

Lives in its own module to avoid a circular import once `tools/batch.py` consumes these and also calls back into `preflight.reasoning.lookup_tool`.

### [preflight/queryables.md](preflight/queryables.md)

Human-readable catalog of machine-queryable tools per ontology subtask (folding, structural analysis, disorder, binding, mutation, PPI, MSA). Each entry tagged Tier 1 (clean REST/pip), Tier 2 (installable but non-trivial), or Tier 3 (web-only/too heavy). Companion to the structured `capabilities/` registry below.

### [preflight/capabilities/](preflight/capabilities/)

Three orthogonal YAML files forming the machine-queryable capability registry, design borrowed from EDAM:

- `subtasks.yaml` — high-level task categories (Topic axis): folding, binding, disorder_core, etc., with parent hierarchy.
- `data_types.yaml` — inputs and outputs that flow between tools (Data axis): aa_sequence, pdb_file, iptm_score, etc.
- `tools.yaml` — the registry itself; each tool entry carries `tier` (1 = clean REST/pip, 2 = non-trivial setup, 3 = unimplemented/web-only), an optional `priority` (higher wins in `select_tool`), subtask IDs, has_input/has_output data_type IDs, and a `wrapper` Python entrypoint (null = declared but not yet callable). Four `tool_rosetta_*` entries (score_interface, per_residue_energies, alanine_scan, confidence_filter) are wired to `tools.rosetta.*`, Tier 1 (the PyRosetta env was non-trivial to set up but the functions are clean/callable).

  **Declarative call contract (Part B / B1).** Each *wrapped* tool may carry three optional fields that make the registry the source of truth for *how to call and chain* a tool (not just what data flows):
  - `call_kwargs` — static default kwargs for the call (e.g. `tool_colabfold: {fast: false}`). A roadmap branch overrides these per-step (e.g. `calc_extra_ptm`).
  - `bindings` — wrapper kwarg name → a `data_*` id (read from `Job.inputs` at call time) or a reserved token (`_job_dir`, `_msa_cache`). `tool_extract_colabfold` binds `out_dir: _job_dir` as the single-pair default; `_panel_branch` passes a per-use `binding_overrides` of `out_dir: data_colabfold_out_dir`.
  - `output_map` — `data_*` id → an attribute expression on the wrapper's return value, used by the orchestrator's registry-driven `_propagate_outputs` (B2) to expose a step's outputs to later steps. Expressions support exactly three forms: `"."` (whole result), `attr`, `attr[0]`. Only chaining outputs need a map: `tool_colabfold` maps `pdb_paths[0] → data_pdb_complex/data_pdb_file` and `a3m_paths → data_a3m_msa`; `tool_rosetta_alanine_scan` maps `"." → data_ala_scan_result` (consumed by `tool_rosetta_confidence_filter`'s `scan` binding). Analysis tools whose results are read straight from `job.results[tool_id]` by the report carry no `output_map` (terminal). Integrity is enforced in `tests/test_registry_integrity.py` (binding sources resolve, output_map keys ∈ has_output and well-formed, call_kwargs/binding kwargs accepted by the wrapper signature). `tool_tmtools` (two PDB inputs) and `tool_predict_panel` (dispatched by `_run_panel_predictions`, not as a JobStep) intentionally carry no call contract.
  - `data_colabfold_out_dir` added to `data_types.yaml` — the downloaded-results directory the extractor parses; previously used as a `Job.inputs` key without a registry entry.
  - **B5 additions:** `tool_analyze_sequence` (→ `tools.analyze.analyze_sequence`) registered so the always-on sequence analysis routes through the registry like every other step; new data types `data_peptide_sequence` (the binder chain, so sequence analysis runs on the peptide not the packed `[target, peptide]` list) and `data_sequence_features` (the `analyze_sequence` output). `tool_colabfold` gains `priority: 10`.

Conventions: stable IDs, forward relations only, polyhierarchy permitted. See [preflight/capabilities/README.md](preflight/capabilities/README.md) for the full schema rules. Consumed by `preflight/reasoning.py` (loader and lookup helpers planned).

## Filesystem conventions

- `jobs/{job_id}/` — per-task working directory. All intermediate files. Never cross-contaminate tasks.
- `outputs/{job_id}/` — final deliverables (report.md, PDBs, PNGs). User-facing.
- `job_id` format: `{target_id}_{YYYYMMDD}_{short_hash}` (proposed — not yet enforced).
- Completion signal: a `done.flag` file in the job directory (proposed — not yet enforced).
- `papers_index.yaml` (root) — structured literature index for the phase 3 RAG corpus; deliberately kept at repo root (not under `literature/`) per its own schema/usage doc, [literature/Papers_Index_Guide.md](literature/Papers_Index_Guide.md). `literature/` holds the PDFs and guide it indexes.

## Key contracts

- `predict()` returns a `PredictionResult`. Every downstream step starts from this object, not raw paths.
- `extract()` returns `list[ExtractedMetrics]` (one per ranked model). Uniform across backends.
- `composite_score()` is the single source of truth for binder ranking. Tune the weights, do not define alternate scorers elsewhere.
- All subprocess calls must be logged (command + exit code). Failures are surfaced, not swallowed.

## Implementation status

| Module | Status |
|---|---|
| `tools/predict.py` | `predict_structure` implemented (`pre_msa_dir`, A3M download); `predict_panel` implemented (blocking: submits primary + array, waits, downloads per-peptide outputs); ESMFold API stub |
| `tools/extract.py` | `extract_colabfold` implemented (populates `per_chain_plddt`); `composite_score` demotes actifpTM for 3+ chain complexes; ESMFold stub |
| `tools/compare.py` | Implemented (TM-score + Cα RMSD via tmtools, native venv) |
| `tools/_compare_inner.py` | Superseded — stub file, safe to delete |
| `tools/_a3m.py` | Implemented — canonical stdlib-only source of truth for `parse_a3m` and `build_paired_a3m`; imported by both `tools/msa.py` and `cluster/build_a3m.py` |
| `tools/msa.py` | SSH/SCP/SLURM helpers implemented; `build_paired_a3m` re-exported from `tools._a3m`; `run_msa` deprecated stub (raises immediately) |
| `cluster/predict.sh` | Implemented (`CALC_EXTRA_PTM` default 1; `SEARCH_ONLY` flag for primary panel jobs) |
| `cluster/build_a3m.py` | Thin CLI wrapper — imports `build_paired_a3m` from co-uploaded `_a3m.py`; `build()` adapter reorders args to match `array_predict.sh` invocation |
| `cluster/array_predict.sh` | Implemented (SLURM array template; `N_TARGET_COPIES` export for homo-oligomeric targets) |
| `cluster/msa_search.sh` | **Deleted** — MSA search folded into `cluster/predict.sh` |
| `cluster/install_localcolabfold.sh` | Implemented (one-time DCC setup) |
| `tools/plot.py` | Implemented (pLDDT profile, PAE heatmap, model comparison bar chart) |
| `tools/analyze.py` | `analyze_sequence` expanded (11 new scalar metrics + 5 new flags); `screen_sequences` + `DEFAULT_SCREEN_THRESHOLDS` implemented (batch screening with 3-figure diagnostic output); `analyze_structure`, `detect_disulfides`, `query_aiupred` implemented; `analyze_cider` + `CIDERFeatures` implemented (localCIDER global descriptors); `query_iupred3` is a legacy alias |
| `tools/metapredict.py` | `predict_disorder` implemented and validated; `MetapredictResult` dataclass; stdout prefix stripping; 60s timeout + `CUDA_VISIBLE_DEVICES=""` |
| `tools/metapredict_worker.py` | `_predict_disorder` validated end-to-end (Python 3.11 / metapredict V3.0.1); sys.path guard prevents tools/metapredict.py shadow-import |
| `tools/rosetta.py` | `score_interface`, `per_residue_energies`, `alanine_scan` implemented (subprocess bridge to proteinviz_pyrosetta) |
| `tools/pyrosetta_worker.py` | `_score_interface`, `_per_residue_energies`, `_alanine_scan` implemented (runs under Python 3.14 / PyRosetta 2026.19) |
| `tools/visualize.py` | `render_py3dmol`, `render_complex_py3dmol`, `render_interface_surface`, `write_html`, `write_complex_html`, `write_interface_surface_html`, `write_html_from_metrics` implemented; `render_pymol` phase 2 stub |
| `tools/fingerprint.py` | Implemented (Pass 1): `ContactFingerprint`, `make_fingerprint`, `_to_vector`, `jaccard_matrix`, `cluster_fingerprints`; no pLDDT gating |
| `tools/controls.py` | Implemented: `make_scrambled_controls`, `_shuffle`, `is_control_id`, `parent_id_of`; `CONTROL_SUFFIX_RE` pattern |
| `tools/report.py` | Implemented; per-complex `render_report` (7-section) + aggregated `render_panel_report` (`PanelEntry`/`PanelReportInputs`, one row per peptide, panel-wide actifpTM warning); `PanelEntry` carries `epitope_cluster`; conditional §4 Epitope Families section (ep_offset) + conditional Selectivity section (zscore_offset); section numbering uses `total_offset = ep_offset + zscore_offset`; `ReportInputs`/`PanelEntry` carry `disorder_iupred3`, `disorder_metapredict`, `cider`, `hotspots` |
| `preflight/reasoning.py` | Registry loader + lookup helpers + validate_inputs + suggest_analyses + build_roadmap implemented; Part B: `make_step` (B3), `select_tool` + `_unimplemented_tool_notes` (B4), `_analysis_steps` + `_append_requested_analyses` + `TaskSpec.requested_analyses` (B5 + follow-up); `_panel_branch` + `_single_pair_branch` + monomer branch all build via `make_step`; `_append_requested_analyses` handles sequence opt-ins + Rosetta hotspot chain (`_HOTSPOT_MARKER`) |
| `tools/batch.py` | Implemented: panel pre-processing (`_run_panel_predictions` — blocking predict_panel + done.flag resume per target); per-job orchestrator loop with per-step optional-failure isolation (B5), FASTA derivation, registry-driven output propagation (B2), per-job logging, CLI entrypoint; analysis runs as optional plan steps (the `_run_post_plan_analysis` hook is retired); `emit_reports` + `build_panel_report_inputs` (read `tool_freesasa`/`tool_analyze_sequence`) + `run_batch(outputs_root=...)` + CLI `--outputs-root` close the prompt→report path |
| `preflight/heuristics.md` | Complete |

## Open design questions

- Exact `composite_score` weighting needs calibration on a labeled binder/non-binder set. `tools/controls.py` + the panel z-score fields on `PanelEntry` implement the minimum viable slice of the scramble-null design in [composite_score_minimum_implementation.md](composite_score_minimum_implementation.md) (conventional z-score only, mean/sd). Not yet implemented from that doc: robust z-score (median/MAD), empirical percentile/p-value, multiple-testing correction, and the ≥20-scramble minimum (current default is 1 per peptide). Supporting references: [literature/composite_score_calibration_references.bib](literature/composite_score_calibration_references.bib).
- `job_id` and `done.flag` conventions are proposed but not implemented — lock down before first real run.
- Decide whether pre-flight conversation state lives in a file (for resumable sessions) or only in the agent's conversation context.
- **Heterodimeric targets**: `build_paired_a3m` and `build_a3m.py` only support single-chain or homo-oligomeric targets. Heterodimers (two different protein chains) would require two separate monomer MSAs, a 3-chain metadata header, and interleaved MSA rows. Out of scope until a concrete use case arises.
