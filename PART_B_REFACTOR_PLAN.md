# Part B — Orchestrator + registry hybrid refactor (handoff plan)

**Status:** B1–B4 + B6 complete and committed; B5 **v1** complete (always-on structure+sequence
analysis routed through the registry + opt-in sequence analyses via `TaskSpec.requested_analyses` +
`JobStep.optional` non-fatal steps; `_run_post_plan_analysis` retired). **Remaining B5 follow-ups**
(not yet done): the `alanine_scan → confidence_filter` Rosetta hotspot chain (§2 B5.4–5), and report
rendering of opt-in disorder/CIDER results (§3.4 — data now flows into `job.results` but `tools/report.py`
does not surface it yet). Note: relaxed/unrelaxed PDB plumbing (§3.3) is a no-op at the default
`USE_AMBER=0` (ColabFold emits only unrelaxed PDBs, which is what `confidence_filter` needs).
**Audience:** a fresh Claude Code session with no prior context. Everything needed is below.
**Repo:** `/home/zach/proteinviz/proteinviz` · run Python as `conda run -n proteinviz python` ·
tests: `conda run -n proteinviz python -m pytest -q` (config in `pytest.ini`, `testpaths = tests`).

---

## 0. Why this refactor exists (read first)

ProteinViz is an agentic pipeline: a natural-language task → pre-flight roadmap → executed jobs →
report. A three-layer "capability registry" (borrowed from EDAM) is supposed to decouple *what tools
exist* from *how the orchestrator runs them*:

- **Ontology layer** — `preflight/heuristics.md` (conceptual rules).
- **Registry layer** — `preflight/capabilities/*.yaml` (`subtasks.yaml`, `data_types.yaml`,
  `tools.yaml`). Machine-queryable catalog of tools, their I/O data types, and a `wrapper` dotted path.
- **Execution layer** — `tools/*.py`, `preflight/reasoning.py` (builds the roadmap), `tools/batch.py`
  (the orchestrator that walks the roadmap's jobs).

**The problem this refactor solves:** the registry today only declares *what data flows* (`has_input` /
`has_output` data-type ids), not *how to call a tool*. So the actual call wiring is hand-written in the
roadmap branches, and the result is:

1. **The orchestrator only ever dispatches two tools** — `tool_colabfold`/`tool_predict_panel` and
   `tool_extract_colabfold`. The entire analysis layer that's already implemented and registered —
   PyRosetta (`score_interface`, `alanine_scan`, …), FreeSASA, metapredict, localCIDER, AIUPred,
   tmtools — **is never put into any plan, so it never runs through the pipeline.** `analyze_structure`
   and `analyze_sequence` only run because they're hardcoded in `_run_post_plan_analysis`, bypassing
   the registry entirely.
2. **Per-tool call bindings are hand-written** in each `reasoning.py` branch
   (`input_bindings={"fasta": "data_fasta_file", "out_dir": "_job_dir"}`). Adding a tool to a plan
   means editing Python, not just YAML.
3. **`consumes`/`produces` on `JobStep` are advisory and duplicated** — the branches literally copy
   `has_output` into `produces`, and the orchestrator never uses them to chain steps.
4. **`_propagate_outputs` is hardcoded** to `result.pdb_paths[0]`. Any new output shape (an A3M, a
   disorder array, an interface score) silently fails to propagate to downstream steps.
5. **Tool selection is positional** — `_single_pair_branch` does `binding_tools[0]` "by current
   registry order." Reorder `tools.yaml` or wire Boltz-1 and the branch silently changes backend; no
   code consults `tier` or wrapper-presence.

All of this gets worse as the tool/task list grows. **Goal:** make the registry the single source of
truth for *how to call and chain* tools, so adding a tool is a YAML edit and the analysis layer becomes
reachable through the orchestrator and folded into the report.

**Chosen depth: HYBRID** (decided with the user). The registry gains the per-tool *call contract*
(bindings, static kwargs, output mapping); the orchestrator chains and propagates from it; but the
roadmap branches keep deciding *which* tools run in *what* order, in readable Python. We deliberately do
**not** build a fully-declarative auto-planner that derives plans by topological `has_output→has_input`
matching, because the registry's data types aren't unique (multiple tools emit `data_pdb_complex`,
`data_interface_residues`) and several real constraints aren't expressible as data types (PyRosetta
`confidence_filter` needs the *unrelaxed* PDB; the panel path needs `predict_panel` run upstream as a
blocking SLURM step). Hybrid is also a strict prerequisite for fully-declarative, so nothing here is
throwaway — an auto-planner can be layered on the same registry data later if task growth justifies it.

---

## 1. Current state — exact code to know

### `preflight/jobs.py` (the execution dataclasses — "jobs")
- `JobStep(tool_id, kwargs={}, input_bindings={}, consumes=[], produces=[])`. `input_bindings` maps a
  wrapper kwarg name → a key in `Job.inputs`. Reserved binding values: `_job_dir` (→ `Job.job_dir`),
  `_msa_cache` (→ a cached A3M path; **reserved but not yet implemented**).
- `Job(job_id, inputs, plan: list[JobStep], status, results, error, runtime_s, job_dir, metadata)`.
  `inputs` keyed by `data_*` ids; `results` keyed by `tool_id`; `metadata` holds non-execution labels
  (`target_id`, `peptide_id`, `kind`, `target_idx`, `peptide_idx`).
- `BatchResult(spec, roadmap, jobs, msa_cache_keys, started_at, finished_at)` with `n_done/n_failed/n_skipped`.

### `preflight/reasoning.py` (builds the roadmap)
- Registry loader `_load_registry()` (lru_cache) → `{kind: {id: entry}}`. Lookups: `lookup_tool`,
  `tools_for_subtask`, `tools_providing`, `tools_requiring`, `all_tools`. `resolve_wrapper(tool_id)`
  imports the dotted `wrapper` path (cached); raises `WrapperNotImplementedError` if `wrapper` is null.
- `build_roadmap(spec, feasibility)` → `Roadmap(steps, decision_points, suggested_additions, jobs)`.
  Branches: `_binding_branch` → `_panel_branch` (N>1 peptides, `kind="binding_panel"`, extraction-only
  jobs) or `_single_pair_branch` (N==1, `kind="binding"`, predict+extract); `_monomer_branch`
  (`kind="monomer"`). **Each branch hand-builds `JobStep`s** with literal `input_bindings`, `kwargs`,
  and `produces=primary.get("has_output", [])`. Tool choice is `tools_for_subtask("subtask_binding")[0]`.

### `tools/batch.py` (the orchestrator)
- `run_batch(spec, roadmap, *, jobs_root, abort_on_failure, outputs_root=None)`: panel pre-processing
  (`_run_panel_predictions`, blocking) then `_run_one_job` per job; emits reports if `outputs_root` set.
- `_run_one_job`: resume via `done.flag` → mkdir → `_ensure_fasta` → for each step:
  `resolve_wrapper(step.tool_id)` → `_bind_inputs(step, job.inputs, job_dir)` → `wrapper(**kwargs)` →
  `job.results[step.tool_id] = result` → `_propagate_outputs(job, result)` → write `done.flag` →
  `_run_post_plan_analysis(job)` → persist `job_meta.json`.
- `_bind_inputs(step, job_inputs, job_dir)`: merges `step.kwargs` with resolved `step.input_bindings`
  (reserved `_job_dir`).
- `_propagate_outputs(job, result)`: **hardcoded** — sets `data_pdb_complex`/`data_pdb_file` from
  `result.pdb_paths[0]`.
- `_run_post_plan_analysis(job)`: **hardcoded** — calls `analyze_structure` on the rank-1 PDB and
  `analyze_sequence` on each sequence; stores under `job.results["analyze_structure"]` /
  `["analyze_sequence"]`. (Part A's `build_report_inputs` / `build_panel_report_inputs` read those keys.)

### `preflight/capabilities/tools.yaml` (the registry)
Each entry today: `id`, `label`, `tier` (1 clean / 2 non-trivial / 3 unimplemented-or-web-only),
`definition`, `subtask: [...]`, `has_input: [data_*]`, `has_output: [data_*]`, `wrapper` (dotted path or
null), `invocation`. **No call-contract fields yet.** Wrapped + Tier ≤ 2 tools (these are what B5 will
plan): `tool_colabfold`, `tool_predict_panel`, `tool_extract_colabfold`, `tool_iupred3`
(`tools.analyze.query_aiupred`), `tool_metapredict`, `tool_localcider`, `tool_freesasa`
(`tools.analyze.analyze_structure`), `tool_tmtools`, `tool_rosetta_score_interface`,
`tool_rosetta_per_residue_energies`, `tool_rosetta_alanine_scan`, `tool_rosetta_confidence_filter`.
Note: `analyze_sequence` has **no registry entry** — add `tool_analyze_sequence` in B5.

---

## 2. The phased plan

Ship in order; each phase leaves the suite green. **Behavior is unchanged through B4** (same tools run),
so the existing tests guard the refactor. B5 is where new behavior (analysis-in-plans) lands — do it
granularly with the user (see §3).

### B1 — Declarative call contract in `tools.yaml`
Add three optional fields to each **wrapped** tool entry:

```yaml
- id: tool_colabfold
  # ...existing fields...
  wrapper: tools.predict.predict_structure
  call_kwargs:            # static defaults; a branch may override per-step
    fast: false
  bindings:               # wrapper kwarg -> data_* id OR reserved token (_job_dir, _msa_cache)
    fasta: data_fasta_file
    out_dir: _job_dir
  output_map:             # data_* id -> attribute expression on the wrapper's return value
    data_pdb_complex: pdb_paths[0]
    data_pdb_file: pdb_paths[0]
    data_a3m_msa: a3m_paths
```

- `tool_extract_colabfold`: `bindings: {out_dir: _job_dir}` (single-pair) — note the panel path binds
  `out_dir: data_colabfold_out_dir` instead, so this is a **per-use override**, not a fixed registry
  binding. Keep the registry binding as the single-pair default; `_panel_branch` passes a
  `binding_overrides`.
- `tool_rosetta_alanine_scan`: `bindings: {pdb_path: data_pdb_complex}`,
  `output_map: {data_ala_scan_result: "."}` (`"."` = the whole return value).
- The **resolver** for `output_map` expressions must support exactly three forms (keep it tiny — no eval):
  `"."` (whole result), `attr` (a single attribute), `attr[0]` (first element of a list attribute).
  Current outputs need nothing more.

Acceptance: `tools.yaml` parses; every `bindings` value is a known `data_*` id or reserved token; every
`output_map` key is in that tool's `has_output`. (Enforced by B6 tests.)

### B2 — Registry-driven `_propagate_outputs` (`tools/batch.py`)
Change the signature to know which tool just ran, and drive propagation from `output_map`:

```python
def _propagate_outputs(job: Job, tool_id: str, result: Any) -> None:
    entry = lookup_tool(tool_id)
    for data_id, expr in entry.get("output_map", {}).items():
        val = _resolve_output_expr(result, expr)
        if val is not None:
            job.inputs[data_id] = val
```

Update the call site in `_run_one_job` to `_propagate_outputs(job, step.tool_id, result)`. Add
`_resolve_output_expr(result, expr)` implementing the three forms from B1. This makes `consumes`/`produces`
load-bearing: a later step's `bindings` reference `data_*` ids that earlier steps populated.
**Behavior parity:** `tool_colabfold`'s `output_map` reproduces today's `pdb_paths[0]` →
`data_pdb_complex`/`data_pdb_file`, so nothing changes yet.

### B3 — `make_step` + rewrite the branches (`preflight/reasoning.py`)
Add a registry-driven step builder and use it in all branches:

```python
def make_step(tool_id, *, kwarg_overrides=None, binding_overrides=None) -> JobStep:
    entry = lookup_tool(tool_id)
    return JobStep(
        tool_id=tool_id,
        kwargs={**entry.get("call_kwargs", {}), **(kwarg_overrides or {})},
        input_bindings={**entry.get("bindings", {}), **(binding_overrides or {})},
        consumes=entry.get("has_input", []),
        produces=entry.get("has_output", []),
    )
```

Rewrite `_single_pair_branch`, `_panel_branch`, `_monomer_branch` to call `make_step(...)` instead of
hand-writing `JobStep`s. Examples:
- single pair: `make_step(primary_id, kwarg_overrides={"calc_extra_ptm": True})`,
  `make_step("tool_extract_colabfold")`.
- monomer: `make_step(primary_id, kwarg_overrides={"calc_extra_ptm": False})`, `make_step("tool_extract_colabfold")`.
- panel: `make_step("tool_extract_colabfold", binding_overrides={"out_dir": "data_colabfold_out_dir"})`.

**No behavior change** — `test_reasoning.py` (job metadata, plan tool ids, panel routing) and the
existing run path verify it. Branches shrink to "list of tool ids + minimal overrides."

### B4 — Tier-aware explicit selection (`preflight/reasoning.py`)
Replace `tools_for_subtask(...)[0]` with:

```python
def select_tool(subtask_id, *, require_wrapper=True, max_tier=2):
    cands = [t for t in tools_for_subtask(subtask_id)
             if not t.get("deprecated")
             and (t.get("wrapper") or not require_wrapper)
             and t.get("tier", 99) <= max_tier]
    if not cands:
        raise RuntimeError(f"No selectable tool for {subtask_id}")
    return sorted(cands, key=lambda t: (t.get("tier", 99), -t.get("priority", 0), t["id"]))[0]
```

Add an optional `priority: int` (higher wins) to the registry to mark the preferred backend
(e.g. `tool_colabfold` over a future `tool_boltz1`). Have `build_roadmap` surface Tier-3 / null-wrapper
tools for the relevant subtask as `decision_points` ("Boltz-1 is registered but not yet callable") so the
capability map stays visible without silently routing to an unimplemented step. This resolves the standing
"registry vs callable coverage" open question in `project_state.md`.

### B5 — Bring the analysis layer into plans (NEW BEHAVIOR — do granularly, see §3)
The payoff. Make the registered analysis tools run through the orchestrator and feed the report.

1. **Register `analyze_sequence`** as `tool_analyze_sequence` → `tools.analyze.analyze_sequence` in
   `tools.yaml` (FreeSASA/`analyze_structure` is already `tool_freesasa`). Give both `bindings` and, for
   analysis tools that feed nothing downstream, an empty/terminal `output_map`.
2. **Always-on analysis steps:** binding/monomer branches append `make_step("tool_freesasa")` and
   `make_step("tool_analyze_sequence")` so today's behavior is preserved but routed through the registry.
3. **Retire `_run_post_plan_analysis`**; update Part A's `build_report_inputs` /
   `build_panel_report_inputs` to read analysis results from their **tool-id keys**
   (`job.results["tool_freesasa"]`, `job.results["tool_analyze_sequence"]`, etc.) instead of
   `"analyze_structure"`/`"analyze_sequence"`. (This is the one place Part A code must change.)
4. **Opt-in analyses** the agent selects in pre-flight (driven by `suggest_analyses` and a new
   `spec.requested_analyses: list[str]` field): disorder (`tool_iupred3` or `tool_metapredict`),
   `tool_localcider`, and the Rosetta hotspot chain (`tool_rosetta_alanine_scan` →
   `tool_rosetta_confidence_filter`). Because outputs now flow by data type (B2), `confidence_filter`
   binds `data_ala_scan_result` (from the scan step) + the PDB automatically.
5. **Honor the semantic constraints** the hybrid keeps visible: `confidence_filter` needs the
   *unrelaxed* PDB (Amber relaxation zeroes B-factors → pLDDT); `alanine_scan` prefers the *relaxed* PDB.
   Add `data_pdb_unrelaxed` (and keep `data_pdb_file`/`data_pdb_complex` for the relaxed/canonical) to
   `data_types.yaml`, and have the predict/extract `output_map` expose both, so the Rosetta bindings point
   at the right one.

**Scope v1 of B5 narrowly** (see §3): always-on structure+sequence + opt-in disorder. Land the Rosetta
hotspot chain as a second B5 sub-step once the relaxed/unrelaxed plumbing exists.

### B6 — Registry integrity tests + docs
- Extend `tests/test_registry_integrity.py`: every `bindings` value resolves to a known `data_*` id or a
  reserved token; every `output_map` key ∈ that tool's `has_output` and its expression is well-formed;
  `call_kwargs` keys are accepted by the wrapper signature (optional, via `inspect`).
- Add `tests/test_reasoning.py`: `make_step` produces the expected `JobStep` from a registry entry;
  `select_tool` skips unwrapped/Tier-3/deprecated and respects `priority`.
- Add `tests/test_batch.py`: registry-driven `_propagate_outputs` sets the mapped `data_*` keys from a
  fake result; `_resolve_output_expr` handles `"."`, `attr`, `attr[0]`.
- **Update `ARCHITECTURE.md` and `project_state.md` in the SAME commit as each code change** (binding
  rule in CLAUDE.md § Living architecture doc). Specifically: the new registry schema fields, `make_step`,
  `select_tool`, registry-driven propagation, analysis-tools-in-plans, retirement of
  `_run_post_plan_analysis`. Also fix the known doc drift: `ARCHITECTURE.md` is missing the
  interface-surface viewer that already exists in `tools/visualize.py`.

---

## 3. B5 decision points to resolve WITH THE USER before coding

The user explicitly asked to go granular here. Surface these as concrete choices:

1. **Always-on vs opt-in split.** Proposed: structure (`tool_freesasa`) + sequence
   (`tool_analyze_sequence`) always-on (matches today); disorder, CIDER, Rosetta hotspots opt-in. Confirm.
2. **How the agent signals opt-in.** Proposed: a `spec.requested_analyses: list[str]` field on `TaskSpec`
   that `build_roadmap` reads to append the corresponding analysis steps. Alternative: the agent edits the
   roadmap post-hoc. Decide the interface.
3. **Relaxed vs unrelaxed PDB plumbing.** Does ColabFold currently download both relaxed (Amber) and
   unrelaxed PDBs, or only one? This determines whether `data_pdb_unrelaxed` is available for the Rosetta
   confidence filter, and whether `alanine_scan` can run on a relaxed structure. Verify against
   `tools/predict.py` + `cluster/predict.sh` before wiring the hotspot chain.
4. **Report integration of new analyses.** Where do disorder summaries, CIDER descriptors, and Rosetta
   hotspots appear in the panel report (`tools/report.py`)? Likely new columns/§4 notes — design with the
   user once the data is flowing.
5. **Per-step failure policy for analysis.** Today analysis failures are swallowed
   (`_run_post_plan_analysis` is supplemental). As real plan steps they'd fail the job by default. Decide:
   mark analysis `JobStep`s as non-fatal (a new `optional: bool` on `JobStep`, orchestrator catches and
   logs) vs. let them fail the job. Recommended: add `JobStep.optional` and have `_run_one_job` catch
   failures of optional steps — preserves the "analysis is supplemental" guarantee while routing through
   the registry.

---

## 4. Working agreements

- Env: `conda run -n proteinviz python ...`. Never bare `python`. Don't `pip install` without permission.
- Test after every phase: `conda run -n proteinviz python -m pytest -q` (expect green; baseline at
  handoff = 126 passed, 2 skipped, 2 xfailed).
- Commit locally per phase; **do not `git push`** without the user's explicit say-so.
- Keep `ARCHITECTURE.md` truthful in the same change as the code — it is binding, not optional.
- Don't reimplement prediction/metric engines; wrap existing tools.
