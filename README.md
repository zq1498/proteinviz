# Protein Structure Visualization Suite

An agentic pipeline for structural analysis of peptide–protein interactions.

You describe a screen in natural language — typically a target protein and a panel of candidate peptides. The agent parses the request, reviews it for feasibility and experimental-design problems, waits for your approval, then runs structure prediction on Duke's compute cluster, extracts confidence and interface metrics, and writes a ranked markdown report that interprets the numbers rather than just listing them.

Built for personal and lab research use, not production deployment. It prioritizes correctness, interpretability, and honest calibration over polish. The intended reader of its reports is computationally competent but not necessarily a structural biologist, so the reporting layer is deliberately explanatory.

The structure-prediction engines are treated as commodities. The value here is the reasoning layer — pre-flight review, capability routing, metric integration, and contextualized reporting.

## Status

**Phase 1 — complete.** Prompt → report runs end-to-end: pre-flight roadmap, DCC SLURM submission (single pair and MSA-cached panel arrays), metric extraction, sequence and structure analysis, plotting, structural comparison, and markdown report rendering.

**Phase 2 — analysis layer built, two priorities open.** Wired into the reports: disorder (local AIUPred package + metapredict V3), CIDER charge-patterning, FreeSASA interface burial, PyRosetta interface scoring and alanine scanning with pLDDT-confidence filtering, contact fingerprinting with epitope clustering, and composition-matched scrambled controls for per-panel null calibration.

**Phase 3 — not started.** Either a multi-agent split or RAG over a structural-biology corpus (`papers_index.yaml` and `literature/` are scaffolding for the latter).

Next up, in order:

1. **`composite_score` calibration and ablation.** The 0.5/0.3/0.2 weight split is an uncalibrated placeholder. Treat the composite ranking as a sort order, not a calibrated probability, until it has been fit to labeled binder/non-binder data.
2. **`render_pymol()`** for publication-quality static images. The py3Dmol HTML viewers work; the PyMOL path raises `NotImplementedError`.

Test suite: 239 passed, 2 skipped, no xfails.

## What has actually been run

Screens and folds completed on DCC:

| Job | Description | Notes |
|---|---|---|
| `smoke_test` | Short test peptide | First end-to-end run; ~8 h MSA wait on remote MMseqs2 |
| `3v03` | HSA monomer (583 aa) | 5 ranked models, plots written |
| `vegf_monomer` | VEGF165 monomer | A3M captured for panel reuse |
| `vegf165_dimer` | VEGF165 homodimer | Validated panel pipeline input |
| `vegf165_dimer_pep00` | VEGF165 dimer + peptide | First panel array task end-to-end |
| `vegf_selectivity_screen` | VEGF selectivity panel | Aggregated panel report generated |

Separately, two known complexes serve as validation benchmarks rather than screens — calmodulin + MLCK peptide (2BBM) and the MDM2 binding domain + p53 peptide (1YCR). The `validate_msa_caching` job folds each one both ways, fresh search vs. cached target A3M, and backs `tests/validate_msa_caching.py`; `1ycr_mdm2_actifptm_test` confirmed actifpTM extraction and the ColabFold 1.6.0 score-JSON keys.

The panel path has been exercised on VEGF165 and on the two benchmarks above; it has not yet been run end-to-end against a new target.

That ~8 h MSA wait on the first run is the single most useful number here: the CPU-bound MMseqs2 search, not the GPU fold, is usually what you are waiting on. Panel runs exist to amortize it — one primary search per target, reused across every peptide in the array.

## Setup

Python work runs in the `proteinviz` conda environment. Calling bare `python` will miss the project's dependencies.

```bash
conda env create -f environment.yml
conda run -n proteinviz python -m pytest
```

Two analysis tools live in separate environments because their Python requirements conflict with the main env. They are called through JSON-over-stdin subprocess bridges, so you never activate them by hand:

| Environment | Python | Holds |
|---|---|---|
| `proteinviz` | 3.12 | everything in `tools/` and `preflight/` |
| `proteinviz_metapredict` | 3.11 | metapredict V3 disorder prediction |
| `proteinviz_pyrosetta` | 3.14 | PyRosetta 2026.19 interface scoring |

Configuration is environment variables. Copy `.env.example` to `.env` and fill in your DCC host, SSH key, and work directory; the defaults for ColabFold paths and prediction parameters are usually fine. `.env` is gitignored — do not commit it.

ColabFold runs on the Duke Compute Cluster, not locally. The one-time LocalColabFold install (pixi + bundled CUDA, entirely user-space, no sudo) is maintained on DCC itself under `$DCC_WORK/colabfold/install_scripts/`, not in this repo. Verify connectivity before a real run:

```bash
conda run -n proteinviz python -m tools.predict --check
```

Jobs go to the `scavenger-gpu` partition, where queue wait and hardware assignment are unpredictable. The pipeline deliberately does not quote wall-clock estimates — `estimate_runtime()` was removed rather than left uncalibrated. It flags runtime *risk factors* instead: pairwise count, sequence length, MSA search depth, and queue contention.

## Running it

The normal path is conversational: describe the task to the agent and let the pre-flight pass run first. The CLIs below are the same machinery, for when you want to drive one stage directly.

Full orchestration, prompt through report:

```bash
conda run -n proteinviz python -m tools.batch \
  --targets "MKTAYIAK..." \
  --peptides "ACDEFGH" "KLMNPQR" \
  --objective "rank peptides by predicted binding" \
  --outputs-root outputs/
```

Single prediction, or a panel from a manifest TSV:

```bash
conda run -n proteinviz python -m tools.predict --fasta target.fasta --out jobs/my_job
conda run -n proteinviz python -m tools.predict --panel --fasta target.fasta --manifest peptides.tsv
```

Plots from a finished job, and structural comparison against a reference:

```bash
conda run -n proteinviz python -m tools.plot --job-dir jobs/my_job --out-dir outputs/my_job
conda run -n proteinviz python -m tools.compare --pred model.pdb --ref 1ycr.pdb --scores scores.json
```

Panel runs resume. A `done.flag` in the job directory means that stage is skipped on a rerun rather than resubmitted, so an interrupted array does not cost you the cached MSA.

## How it works

The pre-flight subsystem is three orthogonal layers, mirroring the [EDAM ontology](https://edamontology.org/page) split between topic, operation, and data:

- **Ontology layer** — `preflight/heuristics.md`, mirrored in CLAUDE.md § Domain heuristics. Conceptual rules: thresholds, sequence flags, metric interpretation, target-specific priors. Read by the agent in conversation, not imported by code.
- **Registry layer** — `preflight/capabilities/*.yaml`, with `preflight/queryables.md` as the human-readable mirror. `subtasks.yaml` is the topic axis (folding, binding, ppi, structural_analysis, mutation, msa_generation, disorder, negative_controls; hierarchical, polyhierarchy allowed). `data_types.yaml` is the data axis (`data_aa_sequence`, `data_iptm_score`, `data_actifptm_score`, and so on). `tools.yaml` carries each tool's subtask and data references plus its declarative call contract — `wrapper` entrypoint, `call_kwargs`, `bindings`, and `output_map`. That contract is what makes the registry the source of truth for *how* to call and chain a tool, not merely what data flows.
- **Execution layer** — `tools/*.py`, `preflight/reasoning.py`, and `tools/batch.py`. `reasoning.py` loads the registry once at import (`lru_cache`) and exposes lookup helpers; `resolve_wrapper()` imports the callable from its dotted path; `tools/batch.py` is the task-agnostic orchestrator that walks `Roadmap.jobs`.

A task moves through intake (natural language → `TaskSpec`), capability lookup against the registry, pre-flight reasoning (`validate_inputs`, `suggest_analyses`, `build_roadmap`), a **hard user-confirmation gate**, orchestration per pair, and report rendering.

That confirmation gate is the point of the project. Nothing executes before you approve the roadmap. The pre-flight layer is also written to resist agreeable answers: it names the assumptions behind every feasibility claim, flags inputs that will produce uninterpretable results (peptides too short to form a stable interface, flanking regions that deflate ipTM, disordered targets whose low pLDDT propagates into complex confidence), and raises experimental-design problems instead of confirming your framing.

Because dispatch is registry-driven, adding a tool is a YAML entry plus a wrapper — not a new branch in the planner. Supplemental analysis steps are marked optional and fail in isolation, so one broken analysis does not sink a job.

## Layout

```
tools/           prediction wrappers, metric extraction, analysis, visualization, reporting, orchestration
preflight/       pre-flight reasoning, domain heuristics, capability registry, behavior evals
cluster/         SLURM job templates and the on-node paired-MSA builder
tests/           pytest suite (239 passing)
test_libraries/  peptide libraries and task scaffolds for upcoming screens
jobs/            per-task working directories (gitignored)
outputs/         final deliverables — reports, PDBs, images (gitignored)
literature/      RAG corpus for phase 3 (gitignored)
```

Working files stay under `jobs/`; user-facing deliverables go to `outputs/`. Tasks never share a directory. Both are gitignored, so a fresh clone has no example output in it — the VEGF selectivity report lives at `outputs/vegf_selectivity_report.md` locally.

## Documentation

- **[CLAUDE.md](CLAUDE.md)** — the agent's operating spec: pre-flight behavior, anti-hyperoptimism rules, domain heuristics, required report format. This is what the agent reads at session start.
- **[ARCHITECTURE.md](ARCHITECTURE.md)** — the living code map: per-module function reference, data flow, dataclass contracts, implementation status, open design questions. Any change under `tools/` or `preflight/` must update it in the same commit.
- **[project_state.md](project_state.md)** — hand-maintained phase status, jobs run, and next priorities.
- **[DATA_SHAPES.md](DATA_SHAPES.md)** — annotated payload examples (ColabFold JSON schema, `ExtractedMetrics`, `Roadmap`/`Job` shapes).
- **[preflight/queryables.md](preflight/queryables.md)** — catalog of external tools per subtask, tiered by how cleanly they integrate.
- **[preflight/capabilities/README.md](preflight/capabilities/README.md)** — registry schema rules and ID conventions.

## Known limitations

- `composite_score` weights are uncalibrated placeholders. See the scramble-null design in [composite_score_minimum_implementation.md](composite_score_minimum_implementation.md) for the planned fix; only the mean/SD z-score slice is implemented, not robust z-scores, empirical p-values, or multiple-testing correction.
- Only single-chain and homo-oligomeric targets are supported. Heterodimeric targets would need two monomer MSAs, a three-chain metadata header, and interleaved paired-MSA rows; binders against dimeric proteins would require such a strategy, alternatively, all MSAs would have to be recalculated as is standard
- Contact fingerprinting does not yet gate interface residues by per-residue pLDDT, so low-confidence contacts count toward epitope clustering.
- `done.flag` resume works, but the `job_id` format (`{target_id}_{YYYYMMDD}_{short_hash}`) is only proposed, not enforced in code.
