 # Protein Structure Visualization Suite

Agentic pipeline for structural analysis of peptide-protein interactions. See [CLAUDE.md](CLAUDE.md) for the full operating spec.

## Status

Phase 1 scaffolding. Stubs in place, no working end-to-end pipeline yet. See [roadmap_1_mvp.md](roadmap_1_mvp.md) for milestones.

## Quick start

```bash
# 1. Create environment
python -m venv .venv
source .venv/bin/activate    # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 2. Verify prediction backend
python -m tools.predict --check

# 3. Run a monomer fold (once implemented)
python -m tools.predict --fasta path/to/seq.fasta --out jobs/my_job
```

## Layout

- `tools/` — prediction wrappers, metric extraction, analysis, reporting
- `preflight/` — pre-flight reasoning layer and domain heuristics
- `jobs/` — per-task working directories (gitignored)
- `outputs/` — final deliverables (gitignored)
- `literature/` — optional RAG corpus (phase 3)

## Backends

ColabFold (primary) or ESMFold API (fallback if local GPU unavailable). Configure via environment variables in `.env` (see `.env.example`).
