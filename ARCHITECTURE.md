# Architecture

Living description of how the codebase is laid out and how data flows through it. **This document must be updated in the same change that modifies code** (see CLAUDE.md § Living architecture doc). If you are reading this and it disagrees with the code, trust the code and fix this file.

Last verified against: 2026-06-05 session — Part A (report wiring) complete: `tools/report.py` gained the aggregated panel report (`PanelEntry`, `PanelReportInputs`, `render_panel_report`, `write_panel_report`) with shared `_binding_tier`/`_fold_tier`/`_actifptm_warning` helpers; `tools/batch.py` gained `build_panel_report_inputs` + `emit_reports` and `run_batch(outputs_root=...)` / CLI `--outputs-root`, closing the prompt→report path. Baseline test hygiene: added `pytest.ini` (`testpaths = tests`); refreshed stale `test_report.py` (7-section format) and `test_reasoning.py` (panel routing) expectations; `estimate_runtime` formula tests marked xfail until calibrated. Suite: 126 passed, 2 skipped, 2 xfailed.

## Quick navigation — key functions by stage (may not be exhaustive)

**Pre-flight:** `build_roadmap`, `validate_inputs`, `suggest_analyses` (all in `preflight/reasoning.py`)

**Prediction & extraction:** `predict_structure`, `predict_panel` (`tools/predict.py`) → `extract_colabfold` (`tools/extract.py`) → `composite_score` (`tools/extract.py`)

**Analysis:** `analyze_sequence`, `analyze_structure`, `analyze_cider`, `query_aiupred` (`tools/analyze.py`); `predict_disorder` (`tools/metapredict.py`); `score_interface`, `alanine_scan` (`tools/rosetta.py`); `build_paired_a3m` (`tools/msa.py`)

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
                 │    estimate_runtime(spec)                    │
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
- `predict_panel(target_fasta, peptides, jobs_root, job_name=None, num_recycle=None, num_models=None, calc_extra_ptm=None)` — **implemented**: two-stage panel submission with automatic homodimer support. If the target FASTA contains multiple chains (`:` separator), extracts the unique monomer chain, writes a temp monomer FASTA for the primary MSA job, and passes `N_TARGET_COPIES={n_chains}` to the array job so each task's paired A3M gets the correct cardinality. Uploads target FASTA + manifest TSV + `build_a3m.py` + `array_predict.sh` to DCC; submits primary job with `SEARCH_ONLY=1` (colabfold_search only, no GPU); submits array job with `--dependency=afterok:{primary_id} --array=0-{N-1}`. **Blocks** until the array reaches a terminal SLURM state, then downloads per-peptide outputs to `jobs_root/{peptide_id}/`. Peptides whose DCC task failed are absent from `local_output_dirs` (logged, not raised). Each array task builds its paired A3M on the DCC node from the primary's cached MSA (no local→DCC round-trip for per-peptide A3Ms) and runs colabfold_batch. `peptides` is `list[tuple[str, str]]` (peptide_id, peptide_seq).
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
- `render_pymol(pdb_path, out_png, highlight_chains=None)` — phase 2 stub; raises `NotImplementedError`.
- `_escape_pdb`, `_plddt_legend`, `_html_page`, `_html_page_raw` — shared helpers.

`render_interface` (former stub with a type error — `chain_b: Path` instead of `str`) has been removed; its intent is covered by `render_complex_py3dmol`.

### [tools/report.py](tools/report.py)

Markdown report generation, two granularities: **per-complex** (`render_report` — one complex, its ranked models) and **aggregated panel** (`render_panel_report` — one target, many peptides, one row per complex). `emit_reports` in `tools/batch.py` picks the right one per task kind.

- `ReportInputs` (dataclass): `job_id`, `task_description`, `metrics: list[ExtractedMetrics]`, `methods`, `flags`, `follow_up`, `output_paths`, `weights` (optional — passed to `composite_score`; defaults to `DEFAULT_SCORE_WEIGHTS`), `structure: StructureFeatures | None` (from `analyze_structure()`), `sequence: SequenceFeatures | None` (from `analyze_sequence()`), `disorder: list[float] | None` (from `query_aiupred()`). The three analysis fields default to `None`; when populated they drive § 4 Structural Analysis.
- `render_report(inputs)` — **implemented**: 7-section per-complex markdown report. §1 Executive Summary, §2 Methods, §3 Per-Complex Results, **§4 Structural Analysis** (SASA, BSA, disulfide pairs from `structure`; sequence flags from `sequence`; disorder summary from `disorder`; shows "_No structural analysis data available._" when all three are None), §5 Flagged Observations, §6 Suggested Follow-Up, §7 Raw Output Locations. Per-complex table includes a "Per-chain pLDDT" column. §5 auto-appends an actifpTM reliability warning for 3+ chain complexes.
- `_render_structural_section(inp) -> list[str]` — renders §4; always present so section numbers are stable across runs.
- `write_report(inputs, out_path)` — writes to disk, creates parent dirs.
- `PanelEntry` (dataclass): one complex in a panel — `peptide_id`, `metrics: ExtractedMetrics` (the best/rank-1 model), optional `structure`, `sequence`, `label`, `job_dir`.
- `PanelReportInputs` (dataclass): `target_id`, `task_description`, `complexes: list[PanelEntry]`, `methods`, `flags`, `follow_up`, `output_paths`, `weights`.
- `render_panel_report(inp)` — **implemented**: aggregated 7-section report ranking all peptides against one target by `composite_score`. §3 **Ranked Binders** is one row per complex (Composite, ipTM, actifpTM, mean pLDDT, peptide-chain pLDDT, BSA, interface-residue count). §4 Per-Complex Notes lists disulfides / sequence flags. §5 emits the actifpTM warning **once** for the whole panel ("applies to every 3+ chain complex"). Matches CLAUDE.md § Reporting format ("ranked binder suggestions, one row per complex").
- `write_panel_report(inp, out_path)` — writes to disk, creates parent dirs.
- `_format_chain_plddt(per_chain)` formats `{"A": 75.9, ...}` as `"A:75.9, ..."`; `_peptide_chain_plddt(m)` returns the last chain id's pLDDT (ColabFold assigns the peptide after the target chains).
- `executive_summary(ranked, flags, weights)` — **implemented**: one-paragraph per-complex interpretation with confidence tier. `_panel_executive_summary(...)` is the panel equivalent (top candidate + strong/moderate counts + score span). Both share `_binding_tier(score)` (>0.70 strong / 0.50–0.70 moderate / <0.50 weak), `_fold_tier(ptm)` (monomer tiers), and `_actifptm_warning(m)` (3+ chain reliability warning), extracted so per-complex and panel reports stay consistent.

### [preflight/reasoning.py](preflight/reasoning.py)

Deterministic helpers the agent calls during pre-flight. The agent's natural-language reasoning still drives the conversation with the user; this module exists so calculations (runtime, validation, capability lookup) are reproducible.

Loads the `capabilities/*.yaml` registry once at import (cached via `lru_cache`) and exposes lookup helpers consumed by the pre-flight reasoning functions. Re-exports `Job`, `JobStep`, `BatchResult` from `preflight.jobs` for caller convenience.

Dataclasses: `TaskSpec`, `FeasibilityReport`, `Roadmap` (with `jobs: list[Job]` field carrying the executable plan).

Registry helpers — **implemented**:
- `lookup_tool(tool_id)`, `lookup_subtask(subtask_id)`, `lookup_data_type(data_id)` — direct ID resolution.
- `all_tools()`, `all_subtasks()` — enumerate.
- `tools_for_subtask(subtask_id, include_children=True)` — walks the subtask parent tree, returns tools annotated with the subtask or any descendant.
- `tools_providing(data_type_id)` — tools whose `has_output` includes the data type.
- `tools_requiring(data_type_id)` — tools whose `has_input` includes the data type.
- `resolve_wrapper(tool_id) -> Callable` — imports and returns the Python callable named in the tool's `wrapper` field. Cached per tool_id. Raises `WrapperNotImplementedError` if the field is null (tool declared but not yet wrapped) or `WrapperResolutionError` if the dotted path can't be imported. The orchestrator uses this to dispatch each `JobStep` to its concrete implementation without hardcoding tool→callable mappings.

Pre-flight reasoning — **implemented**:
- `estimate_runtime(n_targets, n_peptides, avg_length)` — placeholder constants (`pairs · (60 + 0.5·avg_length)` seconds), uncalibrated.
- `validate_inputs(spec)` — applies sequence-level heuristics (length flags, cysteine-pair disulfide hint, OOM warning) per target/peptide.
- `suggest_analyses(spec)` — proposes additions the user did not request, resolved against the registry (e.g. "run disorder check ({tools_for_subtask('subtask_disorder_core')})").
- `build_roadmap(spec, feasibility)` — composes the ordered execution plan; populates both `Roadmap.steps` (human-readable summary) and `Roadmap.jobs` (executable `Job` list consumed by the orchestrator). Branches: `_binding_branch(spec)` routes by peptide count — for N > 1 calls `_panel_branch`, for N = 1 calls `_single_pair_branch`; `_monomer_branch(spec)` emits one Job per target (single chain). `_panel_branch` emits extraction-only Jobs (kind="binding_panel") with `data_colabfold_out_dir=None`; `run_batch` fills this before the job loop. `_single_pair_branch` emits full predict + extract Jobs (kind="binding"). Other task shapes (mutation, ppi, disorder-only) get their own branches as those tasks come up.

### [tools/msa.py](tools/msa.py)

DCC connectivity helpers and MSA construction utilities.

SSH/SCP helpers (`_ssh`, `_scp_to`, `_scp_from`, `_ssh_opts`, `_dcc_host`, `_dcc_key`, `_dcc_work`) are imported by `tools.predict` for SLURM submission. `_wait_for_job` and `_job_status` are also re-used by `tools.predict`.

- `_parse_a3m(path) -> (query_seq, [(header, seq), ...])` — **implemented**: parses a single-chain A3M, returning the query sequence and a list of `(header_line, sequence)` tuples. Headers are preserved — ColabFold requires a `>` header before every MSA row; discarding them causes the entire file to be read as one giant concatenated sequence. Does not handle the `#{chain_lens}\t{cardinalities}` multimer metadata format (single-chain target A3Ms only).
- `build_paired_a3m(peptide_seq, peptide_id, target_a3m, out_dir, complex_name=None, n_target_copies=1)` — **implemented**: builds a ColabFold multimer A3M for a target:peptide complex from a pre-computed single-chain target MSA. Uses the metadata header format that `colabfold/batch.py:unserialize_msa` expects — first line is `#{target_len},{pep_len}\t{n_target_copies},1`; query sequence is concatenated (no `:` separator); MSA rows are written with their original `>` headers; peptide entry uses `{target_len}` gap characters followed by the peptide sequence so `_split_by_aln` assigns it to chain B. `n_target_copies` > 1 for homo-oligomeric targets (e.g. VEGF homodimer → 2); ColabFold replicates the target chain accordingly. Eliminates redundant `colabfold_search` calls when screening a peptide panel against one target.
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

Standalone Python script (stdlib only, `from __future__ import annotations` for Python 3.9 compatibility on DCC compute nodes) that builds a ColabFold multimer A3M from a cached target MSA and a peptide sequence. Uploaded to DCC by `predict_panel()` and called by each array task on the compute node — no container required. Logic mirrors `tools/msa._parse_a3m` + `build_paired_a3m`; keep the two in sync if either changes.

`parse_a3m(path) -> (query_seq, [(header, seq), ...])` preserves per-row headers. `build(target_a3m, peptide_seq, peptide_id, out_dir, complex_name=None, n_target_copies=1) -> Path` writes the paired A3M in the metadata-header format.

CLI: `python3 build_a3m.py <target_a3m> <peptide_seq> <peptide_id> <out_dir> [<complex_name>] [<n_target_copies>]`.

### [cluster/array_predict.sh](cluster/array_predict.sh)

SLURM array job template for parallel panel predictions. Submitted by `predict_panel()` with `--dependency=afterok:{primary_id} --array=0-{N-1}`. Each task reads its peptide from the manifest TSV using `$SLURM_ARRAY_TASK_ID`, calls `build_a3m.py` to construct the paired A3M on the DCC node (no local→DCC round-trip per peptide), then runs the `colabfold_batch` stage with the same Singularity + numpy workaround as `predict.sh`. Per-task logs at `/work/zq36/colabfold/logs/array_{array_job_id}_{task_id}.log`. Per-peptide outputs land at `JOBS_DIR/{peptide_id}/output/`. Required exports: `PRIMARY_MSA_DIR`, `MANIFEST`, `JOBS_DIR`, `BUILD_A3M_SCRIPT`. Optional: `N_TARGET_COPIES` (default 1 — set to 2 for homodimer targets), `NUM_RECYCLE`, `NUM_MODELS`, `CALC_EXTRA_PTM`.

### [cluster/install_localcolabfold.sh](cluster/install_localcolabfold.sh)

One-time setup script to run on DCC login node. Installs pixi to `/work/zq36/.pixi` and LocalColabFold to `/work/zq36/localcolabfold`. No sudo required — pixi and bundled CUDA runtime are fully user-space; cluster GPU nodes supply the driver.

### [tools/plot.py](tools/plot.py)

Matplotlib visualizations, all headless (Agg backend — no display required). Outputs saved as PNG to `outputs/{job_id}/`.

- `plot_plddt_profile(metrics, out_path)` — **implemented**: per-residue pLDDT line plot, filled by AlphaFold confidence tier (matches RCSB/UniProt colouring scheme).
- `plot_pae_heatmap(metrics, out_path)` — **implemented**: N×N PAE matrix as green-scale heatmap, 0–30 Å range.
- `plot_model_comparison(all_metrics, out_path, tmscore_vs_crystal=None)` — **implemented**: grouped bar chart of mean pLDDT and pTM across all 5 ranked models; optionally overlays TM-score vs crystal as a red dashed line.
- `generate_all(job_dir, out_dir, tmscore_vs_crystal=None)` — convenience wrapper; calls all three and returns saved paths.
- CLI: `python -m tools.plot --job-dir jobs/X --out-dir outputs/X [--tmscore 0.976]`.

### [tools/batch.py](tools/batch.py)

Task-agnostic orchestrator. Walks `Roadmap.jobs` from `preflight.reasoning.build_roadmap`, dispatches each `JobStep` through `resolve_wrapper`, isolates per-job failures, and supports done.flag-based resume. Owns no domain knowledge — which tools to call is decided upstream by the registry and the Roadmap branch.

- `run_batch(spec, roadmap, *, jobs_root=Path("jobs"), abort_on_failure=False, outputs_root=None) -> BatchResult` — **implemented**: if any jobs have kind="binding_panel", calls `_run_panel_predictions` first (blocking); then walks every non-pre-failed Job through the per-job loop. If `outputs_root` is set, calls `emit_reports` after the loop to write user-facing reports; default `None` keeps `run_batch` side-effect-free apart from `jobs_root` (tests rely on this).
- `_run_panel_predictions(spec, jobs, jobs_root)` — **implemented**: groups binding_panel jobs by target_idx; for each target writes a FASTA, calls `predict_panel` (blocking — waits for SLURM array, downloads results), then populates `data_colabfold_out_dir` / `data_pdb_complex` / `data_pdb_file` on each job. Resume: if `jobs_root/panel_{target_id}/done.flag` exists, skips submission and reconstructs dirs from the filesystem. Jobs with no DCC output are marked failed before the loop.
- `_run_one_job` — per-job loop: resume check → mkdir → ensure_fasta → for each step: resolve_wrapper, bind_inputs, call, propagate outputs → write done.flag → `_run_post_plan_analysis` → log + persist `job_meta.json`.
- `_run_post_plan_analysis(job)` — **implemented**: called inside the try block after `done.flag` is written; calls `analyze_structure` on the rank-1 PDB (if present in `job.inputs`) and `analyze_sequence` on each sequence in `data_aa_sequence`. Results stored in `job.results["analyze_structure"]` and `job.results["analyze_sequence"]`. Failures are caught and logged — analysis is supplemental, never load-bearing.
- `build_report_inputs(job, task_description, methods, flags, follow_up, weights) -> ReportInputs` — **implemented**: assembles a `ReportInputs` from a completed Job. Pulls `ExtractedMetrics` from `job.results["tool_extract_colabfold"]`, `StructureFeatures` from `job.results["analyze_structure"]`, and `SequenceFeatures` from `job.results["analyze_sequence"]`. For binding tasks (`data_aa_sequence` is a list), uses the peptide (index 1) as `sequence`; for monomer tasks uses index 0.
- `build_panel_report_inputs(target_jobs, target_id, task_description, *, methods, flags, follow_up, weights) -> PanelReportInputs` — **implemented**: aggregates a target's per-peptide Jobs into one ranked panel input. For each job picks the best model (max `composite_score`) from `job.results["tool_extract_colabfold"]` and attaches its structure/sequence analysis; jobs with no metrics are skipped.
- `emit_reports(batch_result, *, outputs_root, task_description, methods=None, weights=None) -> list[Path]` — **implemented**: groups done jobs by `metadata["target_id"]`. binding / binding_panel kinds → one aggregated `outputs/{target_id}/panel_report.md`; monomer kinds → per-job `outputs/{job_id}/report.md`. Returns the paths written. This is the closing hop of the prompt→report path; `run_batch(outputs_root=...)` and the CLI call it automatically.
- `_bind_inputs(step, job_inputs, job_dir)` — resolves `step.input_bindings` keys against `job.inputs`, with reserved value `_job_dir` resolving to the working directory.
- `_ensure_fasta(inputs, job_dir)` — derives `data_fasta_file` from `data_aa_sequence`. `str` → monomer FASTA, `list[str]` → ColabFold multimer FASTA (chains joined with `:`).
- `_propagate_outputs(job, result)` — exposes a step's `pdb_paths[0]` as `data_pdb_complex` / `data_pdb_file` so downstream steps can consume it. (Hardcoded to `pdb_paths`; B2 in the Part-B refactor makes this registry-driven.)
- Logging: per-job `batch.log`, `failure.log`, and `job_meta.json` written to each `jobs/{job_id}/` directory.
- CLI: `python -m tools.batch --targets SEQ1 [SEQ2 ...] [--peptides P1 P2 ...] [--objective "..."] [--jobs-root jobs/] [--outputs-root outputs/] [--abort-on-failure]`. `--outputs-root` defaults to `outputs/`; pass `''` to skip report emission. With defaults this is a one-call prompt→report path.

V1 scope: predict + extract chain. The `_binding_branch` emits one Job per (target, peptide) pair using `predict_structure` — this is correct for single-pair or small jobs but does not exploit MSA caching. For multi-peptide panels, the agent should call `predict_panel` directly (bypassing the per-pair orchestrator) and wire results back once jobs complete. Full panel-branch integration in `tools/batch.py` is a v2 item; see open design questions.

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

- `JobStep` — one tool invocation with `tool_id`, static `kwargs`, `input_bindings` (wrapper kwarg name → `Job.inputs` key), and advisory `consumes` / `produces` mirroring registry I/O. Reserved binding values: `_job_dir` resolves to the job's working directory at call time, `_msa_cache` resolves to a cached A3M path keyed by sequence sha1.
- `Job` — one unit of work end-to-end. `inputs` is a named bundle keyed by data_type ids; `results` is keyed by tool_id; `metadata` holds task-specific labels (target_id, kind, etc.) that have no role in execution. `status` ∈ `{pending, running, done, skipped, failed}`.
- `BatchResult` — the run result with `spec`, `roadmap`, `jobs`, `msa_cache_keys`, and `n_done` / `n_failed` / `n_skipped` properties.

Lives in its own module to avoid a circular import once `tools/batch.py` consumes these and also calls back into `preflight.reasoning.lookup_tool`.

### [preflight/queryables.md](preflight/queryables.md)

Human-readable catalog of machine-queryable tools per ontology subtask (folding, structural analysis, disorder, binding, mutation, PPI, MSA). Each entry tagged Tier 1 (clean REST/pip), Tier 2 (installable but non-trivial), or Tier 3 (web-only/too heavy). Companion to the structured `capabilities/` registry below.

### [preflight/capabilities/](preflight/capabilities/)

Three orthogonal YAML files forming the machine-queryable capability registry, design borrowed from EDAM:

- `subtasks.yaml` — high-level task categories (Topic axis): folding, binding, disorder_core, etc., with parent hierarchy.
- `data_types.yaml` — inputs and outputs that flow between tools (Data axis): aa_sequence, pdb_file, iptm_score, etc.
- `tools.yaml` — the registry itself; each tool entry carries `tier` (1 = clean REST/pip, 2 = non-trivial setup, 3 = unimplemented/web-only), subtask IDs, has_input/has_output data_type IDs, and a `wrapper` Python entrypoint (null = declared but not yet callable). `tool_anchor2` is deprecated (`deprecated: true`) — AIUPred covers the same use case locally. Four `tool_rosetta_*` entries (score_interface, per_residue_energies, alanine_scan, confidence_filter) are wired to `tools.rosetta.*`, Tier 2 (requires proteinviz_pyrosetta env).

Conventions: stable IDs, forward relations only, polyhierarchy permitted. See [preflight/capabilities/README.md](preflight/capabilities/README.md) for the full schema rules. Consumed by `preflight/reasoning.py` (loader and lookup helpers planned).

## Filesystem conventions

- `jobs/{job_id}/` — per-task working directory. All intermediate files. Never cross-contaminate tasks.
- `outputs/{job_id}/` — final deliverables (report.md, PDBs, PNGs). User-facing.
- `job_id` format: `{target_id}_{YYYYMMDD}_{short_hash}` (proposed — not yet enforced).
- Completion signal: a `done.flag` file in the job directory (proposed — not yet enforced).

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
| `tools/msa.py` | SSH/SCP/SLURM helpers implemented; `_parse_a3m` preserves per-row headers returning `(query_seq, [(header,seq)])` pairs; `build_paired_a3m` implemented with correct ColabFold metadata-header format and `n_target_copies` for homo-oligomers; `run_msa` deprecated stub (raises immediately) |
| `cluster/predict.sh` | Implemented (`CALC_EXTRA_PTM` default 1; `SEARCH_ONLY` flag for primary panel jobs) |
| `cluster/build_a3m.py` | Implemented (stdlib-only, Python 3.9 compatible; metadata-header format; `n_target_copies` arg; called by array tasks on DCC) |
| `cluster/array_predict.sh` | Implemented (SLURM array template; `N_TARGET_COPIES` export for homo-oligomeric targets) |
| `cluster/msa_search.sh` | **Deleted** — MSA search folded into `cluster/predict.sh` |
| `cluster/install_localcolabfold.sh` | Implemented (one-time DCC setup) |
| `tools/plot.py` | Implemented (pLDDT profile, PAE heatmap, model comparison bar chart) |
| `tools/analyze.py` | `analyze_sequence` expanded (11 new scalar metrics + 5 new flags); `screen_sequences` + `DEFAULT_SCREEN_THRESHOLDS` implemented (batch screening with 3-figure diagnostic output); `analyze_structure`, `detect_disulfides`, `query_aiupred` implemented; `analyze_cider` + `CIDERFeatures` implemented (localCIDER global descriptors); `query_iupred3` is a legacy alias |
| `tools/metapredict.py` | `predict_disorder` implemented and validated; `MetapredictResult` dataclass; stdout prefix stripping; 60s timeout + `CUDA_VISIBLE_DEVICES=""` |
| `tools/metapredict_worker.py` | `_predict_disorder` validated end-to-end (Python 3.11 / metapredict V3.0.1); sys.path guard prevents tools/metapredict.py shadow-import |
| `tools/rosetta.py` | `score_interface`, `per_residue_energies`, `alanine_scan` implemented (subprocess bridge to proteinviz_pyrosetta) |
| `tools/pyrosetta_worker.py` | `_score_interface`, `_per_residue_energies`, `_alanine_scan` implemented (runs under Python 3.14 / PyRosetta 2026.19) |
| `tools/visualize.py` | `render_py3dmol`, `render_complex_py3dmol`, `write_html`, `write_complex_html`, `write_html_from_metrics` implemented; `render_pymol` phase 2 stub; `render_interface` removed |
| `tools/report.py` | Implemented; per-complex `render_report` (7-section) + aggregated `render_panel_report` (`PanelEntry`/`PanelReportInputs`, one row per peptide, panel-wide actifpTM warning); shared `_binding_tier`/`_fold_tier`/`_actifptm_warning` helpers |
| `preflight/reasoning.py` | Registry loader + lookup helpers + validate_inputs + suggest_analyses + build_roadmap implemented; `_panel_branch` (extraction-only Jobs for N>1 panels) + `_single_pair_branch` + monomer branch; estimate_runtime placeholder constants |
| `tools/batch.py` | Implemented: panel pre-processing (`_run_panel_predictions` — blocking predict_panel + done.flag resume per target); per-job orchestrator loop, FASTA derivation, output propagation, failure isolation, per-job logging, CLI entrypoint; `emit_reports` + `build_panel_report_inputs` + `run_batch(outputs_root=...)` + CLI `--outputs-root` close the prompt→report path (aggregated panel report per target, per-job report for monomers) |
| `preflight/heuristics.md` | Complete |

## Open design questions

- Exact `composite_score` weighting needs calibration on a labeled binder/non-binder set.
- `job_id` and `done.flag` conventions are proposed but not implemented — lock down before first real run.
- Decide whether pre-flight conversation state lives in a file (for resumable sessions) or only in the agent's conversation context.
- **Heterodimeric targets**: `build_paired_a3m` and `build_a3m.py` only support single-chain or homo-oligomeric targets. Heterodimers (two different protein chains) would require two separate monomer MSAs, a 3-chain metadata header, and interleaved MSA rows. Out of scope until a concrete use case arises.
