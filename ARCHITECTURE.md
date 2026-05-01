# Architecture

Living description of how the codebase is laid out and how data flows through it. **This document must be updated in the same change that modifies code** (see CLAUDE.md § Living architecture doc). If you are reading this and it disagrees with the code, trust the code and fix this file.

Last verified against: 2026-05-01 session — `tools/batch.py` orchestrator implemented; `build_roadmap` extended with `_binding_branch` and `_monomer_branch` emitting Job lists; `tool_extract_colabfold` registry entry added; full pipeline smoke-tested with stubbed wrapper (FASTA derivation, dispatch, output propagation, done.flag resume, failure isolation all confirmed).

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

Subprocess wrappers for prediction backends. Backend selected via `PREDICT_BACKEND` env var or `--backend` flag.

ColabFold runs inside WSL (`wsl.exe -d Ubuntu --`). Windows paths are translated to `/mnt/c/...` form via `to_wsl_path()`. `MSYS_NO_PATHCONV=1` is injected into the subprocess env to prevent Git Bash from mangling Linux paths in the command arguments. The binary is at `/home/zach/localcolabfold/.pixi/envs/default/bin/colabfold_batch` (pixi-managed LocalColabFold install). GPU: RTX 3060 Ti, 8 GB VRAM.

- `PredictionResult` (dataclass): `success`, `backend`, `output_dir`, `runtime_s`, `pdb_paths`, `a3m_paths` (populated by msa_only runs), `error`.
- `to_wsl_path(windows_path)` — **implemented**: translates `C:\foo` → `/mnt/c/foo`.
- `check_backend(backend) -> (ok, msg)` — **implemented**: runs `colabfold_batch --help` via WSL to verify reachability.
- `run_colabfold(fasta, out_dir, fast=False, target_a3m=None)` — **implemented**: full WSL invocation with path translation and logging. `fast=True` appends `--msa-mode single_sequence --num-recycle 1` (~5 min, no MSA accuracy, dev/test only). `target_a3m`: when supplied, calls `build_paired_a3m()` and passes the resulting paired A3M as the colabfold_batch input, skipping the MSA search for the target.
- `run_colabfold_msa_only(fasta, out_dir)` — **implemented**: runs `colabfold_search` via WSL to produce a `.a3m` without structure prediction. Requires `COLABFOLD_DB` env var. On DCC, prefer `tools/msa.py` instead.
- `run_esmfold_api(fasta, out_dir)` — stub.
- `write_fasta(sequence, name, path)` — **implemented**: convenience helper for inline sequences.
- `predict(fasta, out_dir, backend, fast=False, target_a3m=None, msa_only=False)` — dispatcher.
- CLI: `python -m tools.predict --check`, `--fasta X --out Y`, or `--sequence MKTAYIAK... --out Y`. Flags: `--fast`, `--target-a3m PATH`, `--msa-only`.

Env vars: `COLABFOLD_BIN` (WSL path to binary), `COLABFOLD_SEARCH_BIN`, `COLABFOLD_DB` (WSL path to MMseqs2 DB, required for `--msa-only`), `WSL_DISTRO` (default: Ubuntu), `PREDICT_BACKEND`.

Notes from first run: subprocess uses `encoding="utf-8", errors="replace"` explicitly — Windows defaults to `cp1252` which chokes on ColabFold's progress characters. MSA search on first run can take hours; subsequent runs on the same sequence reuse the cached `.a3m` file in the job directory.

### [tools/extract.py](tools/extract.py)

Parses backend outputs into a uniform record regardless of backend.

- `ExtractedMetrics` (dataclass): `model_id`, `backend`, `pdb_path`, pLDDT stats, per-residue pLDDT, PAE, ipTM, actifpTM, ranking_score.
- `extract_colabfold(out_dir)` — **implemented**: globs `*_scores_rank_*_*.json`, parses pLDDT list, PAE matrix, ptm; matches each JSON to its unrelaxed PDB by filename substitution.
- `extract_esmfold` — stub.
- `DEFAULT_SCORE_WEIGHTS` — module-level dict `{"primary": 0.5, "mean_plddt": 0.3, "interface_plddt": 0.2}`. Single source of truth for score weights; pass a different dict to experiment without touching the function.
- `composite_score(m, interface_plddt=None, weights=DEFAULT_SCORE_WEIGHTS)` — **implemented as placeholder**: `w["primary"]·primary + w["mean_plddt"]·mean_plddt/100 + w["interface_plddt"]·interface_plddt/100` where primary = actifpTM if present else ipTM. Weights untuned; recalibrate on first real runs. For monomers, ptm is used as ranking_score; iptm/actifptm are None.
- `to_json(metrics, path)` — serializer for per-job metrics.json.

ColabFold score JSON schema (confirmed from smoke test): `plddt` (list[float], per-residue), `pae` (N×N list[list[float]]), `max_pae` (float), `ptm` (float). `iptm`/`actifptm` only present in multimer/complex runs.

### [tools/analyze.py](tools/analyze.py)

Sequence and structure feature extraction. Rules live in [preflight/heuristics.md](preflight/heuristics.md).

- `SequenceFeatures` (dataclass): length, MW, pI, aromatic fraction, cysteine positions, flags.
- `StructureFeatures` (dataclass): SASA (total and per-chain), interface residues, buried surface area, disulfide pairs.
- `analyze_sequence(seq)` — **implemented**: MW, pI, aromatic fraction, cysteine positions; window-based flags for charged IDR (>50% in 10-res), hydrophobic/TM (>70% in 10-res), PPII/proline-rich (≥25% Pro in 10-res or PPP stretch), disulfide-prone cysteine pairs (≤8 res apart); length flags (<5, <20, >40, >2000). Uses Biopython `ProteinAnalysis` (pixi ColabFold env).
- `analyze_structure(pdb, interface_cutoff_a=5.0)` — stub.
- `detect_disulfides(pdb, distance_cutoff_a=3.0)` — stub.
- `query_iupred3(seq)` — stub (web API).

### [tools/visualize.py](tools/visualize.py)

All stubs. Produces py3Dmol HTML for inline notebook rendering or PyMOL PNGs for static images.

### [tools/report.py](tools/report.py)

Markdown report generation. Structure matches the 6-section format in CLAUDE.md § Reporting format.

- `ReportInputs` (dataclass): `job_id`, `task_description`, `metrics: list[ExtractedMetrics]`, `methods`, `flags`, `follow_up`, `output_paths`, `weights` (optional — passed to `composite_score`; defaults to `DEFAULT_SCORE_WEIGHTS`).
- `render_report(inputs)` — **implemented**: 6-section markdown report (executive summary, methods, per-complex table, flags, follow-up, raw output paths). Rankings computed internally via `composite_score`. Weights used are echoed into the executive summary.
- `write_report(inputs, out_path)` — writes to disk, creates parent dirs.
- `executive_summary(ranked, flags, weights)` — **implemented**: one-paragraph interpretation with confidence tier (>0.70 strong / 0.50–0.70 moderate / <0.50 weak) and human-readable metric summary.

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
- `build_roadmap(spec, feasibility)` — composes the ordered execution plan; populates both `Roadmap.steps` (human-readable summary) and `Roadmap.jobs` (executable `Job` list consumed by the orchestrator). Branches: `_binding_branch(spec)` emits one Job per (target, peptide) pair with a `[tool_colabfold, tool_extract_colabfold]` plan; `_monomer_branch(spec)` emits one Job per target with the same plan, single chain. Other task shapes (mutation, ppi, disorder-only) get their own branches as those tasks come up.

### [tools/msa.py](tools/msa.py)

Orchestrates MSA search on the Duke Compute Cluster (DCC) via SLURM, then returns a local `.a3m` file for use with `colabfold_batch --msa-path`.

Workflow: upload FASTA via SCP → render + submit `cluster/msa_search.sh` via sbatch → poll `squeue` every 30s → SCP `.a3m` back → done.

- `build_paired_a3m(peptide_seq, peptide_id, target_a3m, out_dir)` — **implemented**: writes a ColabFold paired A3M (peptide single-seq + `*` chain-break + target A3M verbatim). Caller passes the resulting file as the input to `colabfold_batch`, skipping the target MSA search. Used by `run_colabfold(target_a3m=...)`.
- `check_connection()` — SSH health check, used by `--check` flag.
- `run_msa(fasta, out_dir, job_name)` — **implemented**: full upload/submit/wait/download cycle. Returns `MSAResult`.
- `MSAResult` (dataclass): `success`, `job_id`, `local_a3m`, `final_state`, `error`.
- CLI: `python -m tools.msa --check` or `--fasta X --out Y`.

Env vars: `DCC_HOST`, `DCC_KEY` (SSH key path), `DCC_WORK`, `DCC_CF_BIN`, `DCC_DB_DIR`.

SSH uses `BatchMode=yes` (key auth only, no password prompts). Key should be dedicated to this application: `~/.ssh/id_dcc`.

### [cluster/msa_search.sh](cluster/msa_search.sh)

SLURM job template. Submitted by `tools/msa.py` after substituting `__JOB_NAME__`, `__FASTA__`, `__OUT_DIR__`, `__DB_DIR__` via string replacement. Runs `colabfold_search` on `common` partition with 8 CPUs / 32 GB RAM. Email on END or FAIL to `zq36@duke.edu`.

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

- `run_batch(spec, roadmap, *, jobs_root=Path("jobs"), abort_on_failure=False) -> BatchResult` — **implemented**: walks every Job, marks status, returns the BatchResult.
- `_run_one_job` — per-job loop: resume check → mkdir → ensure_fasta → for each step: resolve_wrapper, bind_inputs, call, propagate outputs → write done.flag → log + persist `job_meta.json`.
- `_bind_inputs(step, job_inputs, job_dir)` — resolves `step.input_bindings` keys against `job.inputs`, with reserved value `_job_dir` resolving to the working directory.
- `_ensure_fasta(inputs, job_dir)` — derives `data_fasta_file` from `data_aa_sequence`. `str` → monomer FASTA, `list[str]` → ColabFold multimer FASTA (chains joined with `:`).
- `_propagate_outputs(job, result)` — exposes a step's `pdb_paths[0]` as `data_pdb_complex` / `data_pdb_file` so downstream steps can consume it.
- Logging: per-job `batch.log`, `failure.log`, and `job_meta.json` written to each `jobs/{job_id}/` directory.
- CLI: `python -m tools.batch --targets SEQ1 [SEQ2 ...] [--peptides P1 P2 ...] [--objective "..."] [--jobs-root jobs/] [--abort-on-failure]`.

V1 scope: predict + extract chain. MSA caching deferred to v2 (use `fast=True` on the prediction step for testing).

### [tools/compare.py](tools/compare.py)

Windows-side wrapper for structural comparison. Invokes `_compare_inner.py` via WSL.

- `ComparisonResult` (dataclass): `pred_pdb`, `ref_pdb`, `tmscore_pred`, `tmscore_ref`, `rmsd_ca`, `n_pred`, `n_ref`, optional pLDDT fields.
- `compare(pred_pdb, ref_pdb, score_json=None, pred_chain=None, ref_chain=None)` — **implemented**: translates paths to WSL, runs inner script, optionally attaches pLDDT from score JSON.
- `print_report(r)` — **implemented**: prints metrics + plain-language interpretation of TM-score.
- CLI: `python -m tools.compare --pred X --ref Y [--scores Z]`.

### [tools/_compare_inner.py](tools/_compare_inner.py)

Runs inside WSL pixi Python env (`tmtools` is installed there, not on Windows due to MSVC requirement). Parses Cα coordinates from both PDBs, runs `tmtools.tm_align()`, outputs JSON to stdout. Not imported directly — only called by `compare.py` via subprocess.

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
- `tools.yaml` — the registry itself; each tool entry references subtask IDs, has_input data_type IDs, has_output data_type IDs, and a `wrapper` Python entrypoint.

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
| `tools/predict.py` | ColabFold via WSL fully wired; ESMFold API stub |
| `tools/extract.py` | `extract_colabfold` implemented; ESMFold stub |
| `tools/compare.py` + `tools/_compare_inner.py` | Implemented (TM-score + Ca RMSD via tmtools in WSL) |
| `tools/msa.py` | Implemented (SSH/SCP/SLURM orchestration — untested, pending DCC setup) |
| `cluster/msa_search.sh` | Implemented (SLURM template) |
| `cluster/install_localcolabfold.sh` | Implemented (one-time DCC setup) |
| `tools/plot.py` | Implemented (pLDDT profile, PAE heatmap, model comparison bar chart) |
| `tools/analyze.py` | `analyze_sequence` implemented; structure stubs remain |
| `tools/visualize.py` | All stubs |
| `tools/report.py` | Implemented (`render_report`, `executive_summary`, `write_report`) |
| `preflight/reasoning.py` | Registry loader + lookup helpers + validate_inputs + suggest_analyses + build_roadmap (binding + monomer branches) implemented; estimate_runtime placeholder constants |
| `tools/batch.py` | Implemented: orchestrator loop, FASTA derivation, output propagation, done.flag resume, failure isolation, per-job logging, CLI entrypoint. V1 — no MSA cache yet. |
| `preflight/heuristics.md` | Complete |

## Open design questions

- Exact `composite_score` weighting needs calibration on a labeled binder/non-binder set.
- `job_id` and `done.flag` conventions are proposed but not implemented — lock down before first real run.
- Decide whether pre-flight conversation state lives in a file (for resumable sessions) or only in the agent's conversation context.
