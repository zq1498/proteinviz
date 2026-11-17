# Part B5 — deferred follow-ups (handoff plan)

**Status:** Part B is complete through **B5 v1**. Two follow-ups remain, both scoped out of v1 on
purpose. This file is the self-contained handoff for a fresh Claude Code session.
**Repo:** `/home/zach/proteinviz/proteinviz` · run Python as `conda run -n proteinviz python` ·
tests: `conda run -n proteinviz python -m pytest -q` (config `pytest.ini`, `testpaths = tests`).
**Baseline at handoff:** 157 passed, 2 skipped, 2 xfailed. Keep it green per phase; commit per task;
**do not `git push`** without Zach's say-so. Update `ARCHITECTURE.md` + `project_state.md` in the
**same commit** as the code (CLAUDE.md § Living architecture doc — binding, not optional).

---

## 0. What already shipped (context — read first)

Part B made the capability registry the source of truth for *how to call and chain* tools. The
relevant pieces you'll build on:

- **Call contract in `tools.yaml`** — each wrapped tool may carry `call_kwargs`, `bindings`
  (wrapper kwarg → `data_*` id or reserved `_job_dir`/`_msa_cache`), and `output_map`
  (`data_*` id → `"."` / `attr` / `attr[0]`).
- **`make_step(tool_id, *, kwarg_overrides=None, binding_overrides=None, optional=False)`**
  (`preflight/reasoning.py`) — builds a `JobStep` from the registry contract.
- **`JobStep.optional`** (`preflight/jobs.py`) — a failing optional step is logged and skipped in
  `tools/batch.py::_run_one_job`, not fatal. All analysis steps use this.
- **Registry-driven `_propagate_outputs(job, tool_id, result)`** (`tools/batch.py`) — drives off
  `output_map` via `_resolve_output_expr`; skips `None`/empty. This is what makes
  `alanine_scan`'s output reachable by `confidence_filter` (see Task 2).
- **B5 v1 analysis-in-plans** — `_analysis_steps(*, peptide_sequence)` appends always-on optional
  `tool_freesasa` + `tool_analyze_sequence` to every binding/monomer plan; `_append_requested_analyses`
  appends opt-in **sequence** analyses from `TaskSpec.requested_analyses`
  (`_OPT_IN_SEQ_ANALYSES = {tool_iupred3, tool_metapredict, tool_localcider}`). Results land under
  their **tool-id keys** in `job.results`; `build_report_inputs` / `build_panel_report_inputs`
  (`tools/batch.py`) read structure from `job.results["tool_freesasa"]` and sequence from
  `["tool_analyze_sequence"]`.

The two follow-ups below are independent — do them in either order, each its own commit(s).

---

## Task 1 — Render opt-in disorder / CIDER results in the report (§3.4 of the original plan)

**Problem.** When the agent sets `spec.requested_analyses=["tool_metapredict", ...]`, those steps now
**run** and their results are stored in `job.results["tool_metapredict"]` / `["tool_iupred3"]` /
`["tool_localcider"]` — but **nothing renders them.** The data flows into the job and dies there.

### Current report state — exact code to know (`tools/report.py`)
- **`ReportInputs`** (per-complex, line ~24) already has `disorder: list[float] | None = None`
  (commented "from query_aiupred()"). `_render_structural_section` (line ~139) **already renders it**
  in § 4: mean disorder score + fraction of residues > 0.5. So for the **per-complex** path, disorder
  rendering exists — it's only unpopulated. There is **no** field for CIDER.
- **`PanelReportInputs`** / **`PanelEntry`** (line ~270/285) have `structure` and `sequence` but **no
  `disorder` and no `cider` field**, and `render_panel_report` § 4 "Per-Complex Notes" (line ~400)
  renders neither.

### Result shapes to read (already verified)
- `tool_iupred3` → `query_aiupred(seq)` → `list[float]` (per-residue disorder, 0–1).
- `tool_metapredict` → `predict_disorder(seq)` → `MetapredictResult` with fields
  `sequence, disorder_scores (list[float]), percent_disorder (float), disordered_domain_boundaries,
  folded_domain_boundaries`.
- `tool_localcider` → `analyze_cider(seq)` → `CIDERFeatures` with fields
  `sequence, fcr, ncpr, kappa, omega, scd, delta, mean_hydropathy, ppii_propensity,
  isoelectric_point, phase_region`.

### Steps
1. **Decide the disorder source precedence** with Zach (both predictors can be requested): suggested
   `tool_metapredict.disorder_scores` else `tool_iupred3` (the raw list). Normalise to a `list[float]`
   for the existing `ReportInputs.disorder` rendering.
2. **Per-complex (`build_report_inputs`)**: populate `ReportInputs.disorder` from those keys. This
   lights up the existing § 4 block with zero report-code change.
3. **CIDER**: add a `cider: CIDERFeatures | None = None` field to `ReportInputs` (and `PanelEntry`),
   and render a line in `_render_structural_section` — suggested: `kappa`, `fcr`, `ncpr`,
   `phase_region` (the interpretable ones). Populate it from `job.results["tool_localcider"]`.
4. **Panel (`PanelReportInputs`/`PanelEntry` + `render_panel_report`)**: add `disorder` and `cider`
   to `PanelEntry`; populate in `build_panel_report_inputs`; render a per-peptide line under § 4
   "Per-Complex Notes" (e.g. "_peptide_3 — % disordered 41%, κ 0.28, region: weak polyampholyte_").
5. **Heuristic interpretation** (CLAUDE.md: every report interprets, never dumps numbers): add a short
   plain-language gloss — e.g. high mean disorder on the **peptide** → "binder is largely disordered;
   the complex pLDDT on the peptide chain is the metric to trust." Pull the phrasing pattern from the
   existing tier helpers (`_binding_tier`, `_actifptm_warning`).

### Tests (`tests/test_report.py`, `tests/test_batch.py`)
- `ReportInputs(disorder=[...])` renders the disorder block (may already be covered — check).
- New: `ReportInputs(cider=CIDERFeatures(...))` renders the CIDER line.
- `build_report_inputs` populates `disorder`/`cider` from `job.results["tool_metapredict"]` /
  `["tool_localcider"]` (construct a fake `MetapredictResult`/`CIDERFeatures`).
- Panel equivalents.

### Docs
`tools/report.py` section in `ARCHITECTURE.md` (ReportInputs/PanelEntry field lists, § 4 description)
and the report bullet in `project_state.md`.

---

## Task 2 — Wire the Rosetta hot-spot chain (`alanine_scan → confidence_filter`) (§2 B5.4–5)

**Goal.** Make the two-step structure-based opt-in runnable through the plan, so the agent can request
per-residue hot-spot ΔΔG cross-referenced with pLDDT confidence.

### What's already in place
- `tool_rosetta_alanine_scan` — `bindings {pdb_path: data_pdb_complex}`,
  `output_map {".": data_ala_scan_result}`. Wrapper: `alanine_scan(pdb_path, interface="A_B",
  rigorous=False) -> list[AlaScanEntry]`.
- `tool_rosetta_confidence_filter` — `bindings {scan: data_ala_scan_result, plddt_pdb: data_pdb_file}`.
  Wrapper: `confidence_filter_scan(scan, plddt_pdb, plddt_threshold=70.0) -> list[ConfidentHotSpot]`.
- Because of registry-driven `_propagate_outputs` (B2), once `alanine_scan` runs, `"." →
  data_ala_scan_result` lands in `job.inputs`, and `confidence_filter`'s `scan` binding picks it up
  **automatically**. The plumbing is done; what's missing is *appending the two steps to the plan with
  the right `interface` string and ordering.*

### The two real constraints the hybrid keeps in Python (don't lose these)
1. **`interface` chain string is per task shape.** ColabFold chain assignment: single-pair binding =
   target chain **A**, peptide chain **B** → `interface="A_B"`. Panel homodimer target = chains **A,B**
   + peptide **C** → `interface="AB_C"`. `alanine_scan` defaults to `"A_B"`, so the **panel path needs
   a `kwarg_overrides={"interface": "AB_C"}`** (and any other multi-chain target likewise). Derive this
   from job kind / target chain count — do **not** hardcode one value for both branches.
   - Verify the live convention before trusting the above: inspect a real panel complex PDB's chain ids
     (`grep '^ATOM' …pdb | cut -c22 | sort -u`) for a homodimer+peptide job under `jobs/`. The peptide
     is the **last** chain (`tools/report.py::_peptide_chain_plddt` already relies on "last chain id").
2. **`plddt_pdb` must be the UNRELAXED PDB.** Amber relaxation zeroes B-factors → pLDDT. At the default
   `USE_AMBER=0` (`cluster/predict.sh`), ColabFold emits **only** unrelaxed PDBs and `extract_colabfold`
   already prefers `*_unrelaxed_rank_*.pdb`, so `data_pdb_file`/`data_pdb_complex` **is** the unrelaxed
   structure and the current `plddt_pdb: data_pdb_file` binding is correct as-is. **Only if Amber is
   ever enabled** do you need the `data_pdb_unrelaxed` data type + dual `output_map` exposure from the
   predict/extract steps (original plan B5.5). Add a guard/comment; don't build the dual path
   speculatively.

### Steps
1. **Extend opt-in handling** in `preflight/reasoning.py`. `_append_requested_analyses` currently only
   handles `_OPT_IN_SEQ_ANALYSES`. Add a structure-based path: if `requested_analyses` contains a
   hot-spot marker (suggest a single id like `"tool_rosetta_confidence_filter"`, or a synthetic
   `"rosetta_hotspots"` token — decide with Zach), append **two** optional steps in order:
   `make_step("tool_rosetta_alanine_scan", kwarg_overrides={"interface": <per-kind>}, optional=True)`
   then `make_step("tool_rosetta_confidence_filter", optional=True)`. Only for jobs that have a complex
   (`kind in {binding, binding_panel}`) — skip for monomers (no interface).
2. **Per-kind `interface`**: single-pair → `"A_B"`; panel → `"AB_C"` for a homodimer (generalise from
   the target chain count if you can derive it; otherwise `"AB_C"` is correct for the current VEGF
   homodimer panels and you should leave a comment).
3. **Report surface (depends on Task 1 patterns)**: add a § for hot-spots — top N `ConfidentHotSpot`
   entries (chain, PDB resnum, resname, ΔΔG, confident/uncertain). `ConfidentHotSpot` comes sorted
   confident-first then ΔΔG-desc. Likely a new `hotspots` field on `ReportInputs`/`PanelEntry`.
4. **Failure isolation already handled**: both steps are `optional=True`, so a missing
   `proteinviz_pyrosetta` env or a PyRosetta error logs-and-skips without failing the prediction job.

### Tests (`tests/test_reasoning.py`, `tests/test_batch.py`)
- `requested_analyses` with the hot-spot marker appends `[alanine_scan, confidence_filter]` (optional,
  correct order) for binding/panel jobs and **nothing** for monomers.
- Panel job gets `interface="AB_C"`; single-pair gets `"A_B"`.
- **Do not run PyRosetta in tests** — monkeypatch `resolve_wrapper` (see the B5 optional-step tests in
  `tests/test_batch.py` for the pattern) to assert the chain runs and `confidence_filter` receives the
  `alanine_scan` result via `data_ala_scan_result` propagation.

### Docs
`ARCHITECTURE.md` (`_append_requested_analyses` description, reasoning status row, report section) and
`project_state.md` (registry section + reasoning helpers). If you add `data_pdb_unrelaxed`, document it
in `data_types.yaml` + the capabilities bullet.

---

## 3. Working agreements

- Env: `conda run -n proteinviz python ...`. Never bare `python`. Don't `pip install` without permission.
- PyRosetta runs in a **separate** env (`proteinviz_pyrosetta`, Python 3.14) via a subprocess bridge
  (`tools/rosetta.py` → `tools/pyrosetta_worker.py`). Tests must not depend on it being present.
- Test after every change; commit per task; keep `ARCHITECTURE.md` + `project_state.md` truthful in the
  same commit. Don't `git push` without explicit say-so.
- Both follow-ups are **opt-in** and **optional-step** — they must never regress the always-green
  predict→extract→report path or fail a prediction job when an analysis tool is unavailable.
