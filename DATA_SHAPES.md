# Data Shapes Reference

IO contracts for the pipeline's main data types. Read this when writing tests or
extraction logic — not a substitute for reading the code, but a fast orientation to
what real payloads look like and where the edge cases are.

---

## ColabFold score JSON (`*_scores_rank_NNN_*.json`)

Produced by `colabfold_batch`. Parsed in `tools/extract.py:extract_colabfold`.

### Standard multimer output (no `--calc-extra-ptm`)

The real VEGF example outputs (`dcc_colabfold_example_output/`) look like this.
`actifptm` and `per_chain_ptm` are absent — this is the default run mode.

```json
{
  "plddt":   [37.47, 46.38, 53.81, 51.91, 54.12, "...390 floats total..."],
  "max_pae": 31.5,
  "pae":     [["...390x390 float matrix..."]],
  "ptm":     0.56,
  "iptm":    0.55
}
```

Key notes:
- `plddt` length = total residues across all chains concatenated (target + peptide)
- `pae` is always square: `len(pae) == len(pae[0]) == len(plddt)`
- `iptm` is present for multimer runs; absent for monomer (`alphafold2_ptm` model)
- `ptm` is always present; used as `ranking_score` and as fallback primary score for monomers

### With `calc_extra_ptm=True` (binding jobs)

When the ColabFold wrapper passes `--calc-extra-ptm`, two additional keys appear:

```json
{
  "plddt":              [...],
  "pae":                [...],
  "ptm":                0.72,
  "iptm":               0.68,
  "actifptm":           0.91,
  "pairwise_actifptm":  {"A-B": 0.91},
  "per_chain_ptm":      {"A": 0.88, "B": 0.03}
}
```

Key notes:
- `actifptm` — scalar; the interface-focused metric. Used as `primary` in composite score
  for 2-chain complexes only (see `composite_score` for the 3-chain demotion rule)
- `pairwise_actifptm` — dict keyed by chain pair string; not currently read by extract.py
  (only the scalar `actifptm` is used), but available for future per-interface breakdowns
- `per_chain_ptm` — per-chain confidence; chain B (peptide) value near 0 is normal and
  expected for short disordered peptides, not a failure

### Filename pattern

```
{prefix}_scores_rank_{NNN}_{model_spec}.json
{prefix}_unrelaxed_rank_{NNN}_{model_spec}.pdb
```

Example: `vegf_binder_complex_scores_rank_001_alphafold2_multimer_v3_model_1_seed_000.json`

`extract_colabfold` derives `model_id` as the substring from `_rank_` onward
(`rank_001_alphafold2_multimer_v3_model_1_seed_000`). The matching PDB is found
by replacing `_scores_` with `_unrelaxed_` in the stem.

---

## `ExtractedMetrics` (populated instance)

Produced by `tools/extract.py:extract_colabfold`. One per ranked model.

### Strong binder (2-chain, `calc_extra_ptm=True`)

```python
ExtractedMetrics(
    model_id      = "rank_001_alphafold2_multimer_v3_model_1_seed_000",
    backend       = "colabfold",
    pdb_path      = Path("jobs/vegf_p3/rank_001...pdb"),
    mean_plddt    = 74.3,
    min_plddt     = 22.1,   # peptide terminus — low is normal
    max_plddt     = 95.8,
    per_chain_plddt = {"A": 84.2, "B": 38.1},  # B is peptide; low expected
    iptm          = 0.68,
    actifptm      = 0.91,   # present: calc_extra_ptm was used
    ranking_score = 0.72,   # ptm
)
# composite_score(m) → 0.5*0.91 + 0.3*0.743 = 0.455 + 0.223 = 0.678
# (no interface_plddt supplied; add 0.2*(interface_plddt/100) if available)
```

### Suspicious result (high iptm, low interface pLDDT)

```python
ExtractedMetrics(
    mean_plddt      = 71.0,
    per_chain_plddt = {"A": 83.0, "B": 18.0},  # peptide chain nearly unresolved
    iptm            = 0.83,   # looks good
    actifptm        = 0.61,   # but actifpTM is much lower — inflate from iptm denominator
    ranking_score   = 0.79,
)
# Large iptm/actifptm gap → flag for review; flanking region likely inflating iptm
```

### Monomer (no peptide, no iptm/actifptm)

```python
ExtractedMetrics(
    per_chain_plddt = {"A": 71.4},
    iptm            = None,
    actifptm        = None,
    ranking_score   = 0.74,  # ptm only
)
# composite_score(m) → 0.5*0.74 + 0.3*(mean_plddt/100)  (ptm fallback)
```

---

## `composite_score` inputs and precedence

```
n_chains = len(m.per_chain_plddt)

if actifptm is not None and n_chains in {0, 2}:
    primary = actifptm          # 2-chain binding: focused interface metric
elif iptm is not None:
    primary = iptm              # 2-chain no extra_ptm, or 3-chain (demotion)
else:
    primary = ranking_score     # monomer: ptm

score = 0.5 * primary
      + 0.3 * (mean_plddt / 100)
      + 0.2 * (interface_plddt / 100)   # only if supplied
```

**3-chain demotion:** homodimer + peptide runs produce 3 chains (A, B = dimer, C = peptide).
`actifptm` in that case reflects the confident dimer interface, not the peptide, so it is
excluded and `iptm` is used instead. This is the single most counterintuitive rule in the
scoring layer — tests in `test_extract.py` (`test_composite_score_demotes_actifptm_for_three_chain_complex`)
lock it down.

---

## `Roadmap` (pre-flight output)

Produced by `preflight/reasoning.py:build_roadmap`. Consumed by the orchestrator
and shown to the user during pre-flight.

### Binding task (1 target × 1 peptide)

```python
Roadmap(
    steps = [
        "Predict 1 target-peptide complex(es) via tool_colabfold",
        "Extract ipTM, actifpTM, pLDDT, PAE per complex",
        "Compute composite scores and rank",
        "Render markdown report with flagged observations",
    ],
    decision_points = [],
    suggested_additions = [],
    jobs = [
        Job(
            job_id  = "target_0__peptide_0__a3f9c1b2",
            inputs  = {
                "data_aa_sequence":    ["MSSGSSG...", "ACDEFGH"],
                "data_peptide_sequence": "ACDEFGH",
                "data_fasta_file":     None,
            },
            plan = [
                JobStep(tool_id="tool_colabfold",         kwargs={"calc_extra_ptm": True}),
                JobStep(tool_id="tool_extract_colabfold", kwargs={}),
                JobStep(tool_id="tool_freesasa",          optional=True),
                JobStep(tool_id="tool_analyze_sequence",  optional=True,
                        input_bindings={"seq": "data_peptide_sequence"}),
            ],
            metadata = {"target_id": "target_0", "peptide_id": "peptide_0", "kind": "binding"},
        )
    ],
)
```

### Panel task (1 target × many peptides)

Panel jobs skip the prediction step in their plan — `predict_panel` runs upstream
via `run_batch`, populating `data_colabfold_out_dir` before the orchestrator walks
the job. The plan starts at extraction.

```python
Job(
    inputs = {
        "data_aa_sequence":        ["MSSGSSG...", "ACDEFGH"],
        "data_peptide_sequence":   "ACDEFGH",
        "data_colabfold_out_dir":  None,   # filled by run_batch after predict_panel
        "data_pdb_complex":        None,
        "data_pdb_file":           None,
    },
    plan = [
        JobStep(tool_id="tool_extract_colabfold",
                input_bindings={"out_dir": "data_colabfold_out_dir"}),
        JobStep(tool_id="tool_freesasa",         optional=True),
        JobStep(tool_id="tool_analyze_sequence", optional=True,
                input_bindings={"seq": "data_peptide_sequence"}),
    ],
    metadata = {"kind": "binding_panel", "target_idx": 0, "peptide_idx": 3},
)
```

---

## Smoke test fixture

The conftest `smoke_test_dir` fixture points to `jobs/smoke_test/` — a real
ColabFold output directory checked into the repo. It contains 5 ranked models from
a VEGF binder run **without** `--calc-extra-ptm`, so:

- `iptm` is present (~0.52–0.55)
- `actifptm` is **None** across all 5 models
- `per_chain_ptm` is **None**
- `plddt` length is 390 (target + peptide residues)

Tests that need `actifptm` populated use the `_make_metric()` factory in
`test_extract.py` rather than the smoke fixture.

If `jobs/smoke_test/` is absent the tests skip (not fail) — see `conftest.py:26`.
