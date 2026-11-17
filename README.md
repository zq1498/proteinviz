# Protein Structure Visualization Suite

An agentic pipeline for structural analysis of peptide–protein interactions.

You describe a screen in natural language — typically a target protein and a panel of candidate peptides. The agent parses the request, reviews it for feasibility and experimental-design problems, waits for your approval, then runs structure prediction on Duke's compute cluster, extracts confidence and interface metrics, and writes a ranked markdown report that interprets the numbers rather than just listing them.

Built for personal and lab research use, not production deployment. It prioritizes correctness, interpretability, and honest calibration over polish. The intended reader of its reports is computationally competent but not necessarily a structural biologist, so the reporting layer is deliberately explanatory.

The structure-prediction engines are treated as commodities. The value here is the reasoning layer — pre-flight review, capability routing, metric integration, and contextualized reporting.

## Status

**Phase 1 — complete.** Prompt → report runs end-to-end: pre-flight roadmap, DCC SLURM submission (single pair and MSA-cached panel arrays), metric extraction, sequence/structure analysis, plotting, and markdown report rendering.

**Phase 2 — substantially done.** Sequence and structure analysis are wired into the reports: disorder (local AIUPred package + metapredict V3), CIDER/charge-patterning, FreeSASA interface burial, PyRosetta interface scoring and alanine scanning with pLDDT-confidence filtering, contact fingerprinting with epitope clustering, and composition-matched scrambled controls for per-panel null calibration. PyMOL publication rendering is still a stub.

**Phase 3 — not started.** Either a multi-agent split or RAG over a structural-biology corpus (`papers_index.yaml` and `literature/` are scaffolding for the latter).

Test suite: 239 passed, 2 skipped.

The honest caveat: the pipeline is exercised on real DCC runs (VEGF165, MDM2, calmodulin), but `composite_score`'s ranking weights are uncalibrated placeholders. Treat the composite ranking as a sort order, not a calibrated probability, until it has been fit to labeled binder/non-binder data.

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

Jobs are submitted to the `scavenger-gpu` partition, where queue wait and hardware assignment are unpredictable. The pipeline deliberately does not quote wall-clock estimates; it flags runtime *risk factors* instead (pairwise count, sequence length, MSA search depth, queue contention).

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

Panel runs resume. A `done.flag` in the job directory means the stage is skipped on a rerun rather than resubmitted, so an interrupted array does not cost you the cached MSA.

## How it works

Three layers, kept deliberately separate:

- **Ontology** (`preflight/heuristics.md`) — conceptual domain knowledge. What metrics mean, what thresholds imply, which sequence features deserve a flag. Read by the agent, not imported by code.
- **Registry** (`preflight/capabilities/*.yaml`) — an operational catalog of tools, the subtasks they serve, the data types they consume and produce, and their call contracts. Queried by `preflight/reasoning.py`.
- **Execution** (`tools/*.py`, `cluster/*`) — the actual subprocess and Python calls.

A task moves through intake (natural language → `TaskSpec`), capability lookup against the registry, pre-flight reasoning (`validate_inputs`, `suggest_analyses`, `build_roadmap`), a **hard user-confirmation gate**, orchestration per pair in `tools/batch.py`, and report rendering.

That confirmation gate is the point of the project. Nothing executes before you approve the roadmap. The pre-flight layer is also written to resist agreeable answers: it names the assumptions behind every feasibility claim, flags inputs that will produce uninterpretable results (peptides too short to form a stable interface, flanking regions that will deflate ipTM, disordered targets whose low pLDDT will propagate into complex confidence), and raises experimental-design problems instead of confirming your framing.

Because the orchestrator is registry-driven, adding a tool is a YAML entry plus a wrapper — not a new branch in the planner.

## Layout

```
tools/        prediction wrappers, metric extraction, analysis, visualization, reporting, orchestration
preflight/    pre-flight reasoning, domain heuristics, capability registry, behavior evals
cluster/      SLURM job templates and the on-node paired-MSA builder
tests/        pytest suite (239 passing)
test_libraries/  peptide libraries and task scaffolds for upcoming screens
jobs/         per-task working directories (gitignored)
outputs/      final deliverables — reports, PDBs, images (gitignored)
literature/   RAG corpus for phase 3 (gitignored)
```

Working files stay under `jobs/`; user-facing deliverables go to `outputs/`. Tasks never share a directory.

## Documentation

- **[CLAUDE.md](CLAUDE.md)** — the agent's operating spec: pre-flight behavior, anti-hyperoptimism rules, domain heuristics, required report format. This is what the agent reads at session start.
- **[ARCHITECTURE.md](ARCHITECTURE.md)** — the living code map: per-module function reference, data flow, dataclass contracts, implementation status, open design questions. Any change under `tools/` or `preflight/` must update it in the same commit.
- **[DATA_SHAPES.md](DATA_SHAPES.md)** — annotated payload examples (ColabFold JSON schema, `ExtractedMetrics`, `Roadmap`/`Job` shapes).
- **[project_state.md](project_state.md)** — hand-maintained phase status and milestones.
- **[preflight/queryables.md](preflight/queryables.md)** — catalog of external tools per subtask, tiered by how cleanly they integrate.

## Known limitations

- `composite_score` weights are uncalibrated placeholders. See the scramble-null design in [composite_score_minimum_implementation.md](composite_score_minimum_implementation.md) for the planned fix.
- Only single-chain and homo-oligomeric targets are supported. Heterodimeric targets would need two monomer MSAs and interleaved paired-MSA rows; out of scope until a concrete use case.
- Contact fingerprinting does not yet gate interface residues by per-residue pLDDT, so low-confidence contacts count toward epitope clustering.
- `done.flag` resume works, but the `job_id` naming format is only proposed in ARCHITECTURE.md, not enforced in code. Worth locking down before the next real screen.
- ESMFold and PyMOL rendering are stubs.
