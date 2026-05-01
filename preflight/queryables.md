# Machine-Queryable Tools by Subtask

Crude inventory of tools per ontology subtask, classified by how they can be called from the pipeline. Tier 1 = clean programmatic access (REST or pip), Tier 2 = installable but non-trivial setup, Tier 3 = web-server-only or too heavy to integrate.

This file is the source of truth for "what methods does the agent have available?" — the pre-flight reasoning layer reads it when composing a plan.

---

## Structure prediction (Folding)

| Tool | Tier | Access | Install | Notes |
|---|---|---|---|---|
| ColabFold | 1 | Local CLI via WSL | Already installed (`/home/zach/localcolabfold/.pixi/envs/default/bin/colabfold_batch`) | Primary backend; full pLDDT/PAE/pTM. Wrapped in `tools/predict.py`. |
| Boltz-1 | 1 | Local CLI + Python | `pip install boltz` | Open-source, AF3-class accuracy. CLI: `boltz predict input.yaml`. Returns PDB + confidence JSON. Drop-in second backend. |
| ESMFold (Atlas API) | 2 | REST `POST api.esmatlas.com/foldSequence` | None — HTTP only | Meta archived the repo Aug 2024; service has had outages. Treat as unreliable fallback for monomer sanity checks. |
| ESMFold (local) | 3 | Local install | Heavy (PyTorch + ~3GB weights) | Not worth the setup cost given ColabFold + Boltz-1 cover the use case. |

---

## Structural analysis

| Tool | Tier | Access | Install | Notes |
|---|---|---|---|---|
| FreeSASA | 1 | Python API | `pip install freesasa` | SASA, buried surface area, per-residue/per-atom. Takes Bio.PDB structure directly. Fills SASA stub in `tools/analyze.py`. |
| Biotite | 1 | Python API | `pip install biotite` | Comprehensive structural library: parsing, SASA, DSSP interface, atom selection. NumPy-native, fast. Good primary structure manipulation layer. |
| Biopython DSSP | 1 | Python API + binary | `pip install biopython` + `mkdssp` (apt/conda in WSL) | Secondary structure assignment via `Bio.PDB.DSSP`. Needs `mkdssp` binary one-time setup. |
| tmtools | 1 | Python via WSL subprocess | Already in WSL pixi env | TM-score + Cα RMSD. Wrapped in `tools/compare.py`. |
| NetSurfP-3 | 2 | Local CLI | `pip install -e .` from github.com/Eryk96/NetSurfP-3.0; needs ESM-1b weights (~700MB) | Sequence-only secondary structure, RSA, disorder. Useful when no PDB exists yet. Non-trivial setup. |
| PDBePISA | 1 | REST | None — HTTP only | `www.ebi.ac.uk/pdbe/pisa/` for assembly-level interface analysis, BSA, stability. Cleaner than manual SASA differences for PPI interfaces. |

---

## Disorder characterization

| Tool | Tier | Access | Install | Notes |
|---|---|---|---|---|
| IUPred3 | 1 | REST | None — HTTP only | `iupred3.elte.hu/iupred3/{type}/{accession_or_seq}[.json]`. Types: `long`, `short`, `anchor`. Authoritative per-residue disorder + ANCHOR2 binding prediction. |
| ANCHOR2 | 1 | Same REST endpoint | — | Same endpoint as IUPred3 with `anchor` type. Disorder-mediated binding sites (MoRF-like). |
| metapredict V3 | 1 | Python API | `pip install metapredict` | Holehouse/idptools. Per-residue disorder, V3 (Nov 2024) is SOTA-accurate. Offline, fast for batch. Use as fast pre-screen alongside IUPred3. |
| localCIDER | 1 | Python API | `pip install localcider` | Pappu lab. Charge patterning (κ, NCPR, FCR, SCD), composition. Covers ontology Ensemble subsection from sequence. |
| ALBATROSS (sparrow) | 2 | Python API | `pip install idptools-sparrow` | Holehouse lab. Predicted ensemble dimensions (Rg, end-to-end). Heavier — research-grade, not pre-flight. |
| flDPnn2 | 3 | Web only | — | biomine.cs.vcu.edu/servers/flDPnn2/. No REST, no standalone. Revisit: check if GitHub release exists. |
| DisoFLAG | 3 | Web only | — | No public API found. Revisit. |
| FuzPred | 3 | Web only | — | fuzpred.bio.unipd.it. No REST. Disorder-to-order vs disorder-to-disorder binding mode prediction. Worth scraping or asking authors for code. |
| FuzDrop | 3 | Web only | — | fuzdrop.bio.unipd.it. No REST. LLPS propensity. Same situation as FuzPred. |
| catGRANULE 2.0 | 3 | Web only | — | LLPS predictor. No REST. |
| PScore | 2 | Python script | GitHub: vnrusso/PScore | LLPS predictor, runnable locally. Lightweight. |
| PLAAC | 2 | R package / standalone | github.com/whitehead/plaac | Prion-like domain detection. R-based; CLI exists. |
| PONDR | 3 | Web only | — | Legacy. Superseded by metapredict for practical use. |

---

## Peptide–protein binding

| Tool | Tier | Access | Install | Notes |
|---|---|---|---|---|
| ColabFold (multimer) | 1 | Local CLI via WSL | Done | Primary binding backend. ipTM + actifpTM in score JSON. |
| Boltz-1 | 1 | Local CLI + Python | `pip install boltz` | Handles peptide-protein complexes natively. |
| IUPred3 / ANCHOR2 | 1 | REST | — | Pre-flight disorder check on peptide and target binding region. |
| NetSurfP-3 | 2 | Local CLI | See above | Pre-fold peptide secondary structure / RSA from sequence. |
| PeptiDerive | 3 | Rosetta protocol | Full Rosetta build (hours) | Per-residue energy contribution from a complex PDB. Powerful but heavy install. Phase 2/3 candidate. |

---

## Mutational effect

| Tool | Tier | Access | Install | Notes |
|---|---|---|---|---|
| ColabFold (WT + mutant) | 1 | Local CLI via WSL | Done | ΔpLDDT comparison. Already doable with current tooling. |
| FoldX | 2 | Local CLI | Binary download (free academic, foldxsuite.crg.eu, registration required) | ΔΔG predictor. Native Windows/Linux binary, no WSL needed. Best ROI for mutation analysis. |
| Rosetta ddg_monomer | 3 | Local CLI | Rosetta build | Authoritative ΔΔG but heavy. Skip unless FoldX is insufficient. |
| DynaMut2, mCSM, PremPS | 3 | Web only | — | No REST APIs. Form-based scrapers possible but fragile. |

---

## Protein–protein interaction

| Tool | Tier | Access | Install | Notes |
|---|---|---|---|---|
| ColabFold (multimer) | 1 | Local CLI via WSL | Done | Primary PPI backend. |
| Boltz-1 | 1 | Local CLI + Python | `pip install boltz` | Handles multimers natively. |
| IUPred3 | 1 | REST | — | Disorder check on both partners — ipTM unreliable when partners are heavily disordered. |
| PDBePISA | 1 | REST | — | Post-fold assembly-level interface analysis, BSA, oligomeric state plausibility. |
| FreeSASA | 1 | Python API | `pip install freesasa` | Buried surface area on complex formation. |

---

## MSA generation (supporting)

| Tool | Tier | Access | Install | Notes |
|---|---|---|---|---|
| ColabFold (mmseqs2 server) | 1 | Built into colabfold_batch | Done | Default MSA path. Slow on first run, cached after. |
| DCC (SLURM) | 1 | SSH/SCP/sbatch | Wired in `tools/msa.py` | Cluster-based MSA generation. Untested. |
| `build_paired_a3m` | 1 | Python | In `tools/msa.py` | Reuse pre-computed target MSA across a peptide panel. |

---

## Where this gets aggregated

The pre-flight reasoning layer is the consumer. Concrete plan:

1. **This file is the catalog.** Human-readable, easy to extend.
2. **Add a structured mirror at `preflight/capabilities.yaml`.** Each tool gets an entry with: `name`, `subtask`, `tier`, `provides` (outputs it produces), `requires` (inputs it needs), `install` (command or "done"), `wrapper` (Python module path if wrapped, else `null`). YAML so it diffs cleanly and the agent can parse it deterministically without LLM-style ambiguity.
3. **`preflight/reasoning.py` reads `capabilities.yaml` at import time** and exposes a `lookup(subtask, requires=..., provides=...)` helper. The stubs `validate_inputs`, `suggest_analyses`, and `build_roadmap` use this to compose a plan from declared capabilities rather than hardcoded logic.
4. **Tool wrappers register themselves implicitly** by their `wrapper` path in the YAML. If `tools/disorder.py::query_iupred3` exists, the catalog entry points to it; if it's a stub, the catalog says `tier: planned` and the agent knows the capability is declared but not yet callable.

Two-layer design: this `.md` for humans (and for me to reason about in conversation), `capabilities.yaml` for the pre-flight code to query systematically. Keep them in sync the same way `ARCHITECTURE.md` is kept in sync with the code — same change, not a follow-up.
