# Capabilities Registry

Three orthogonal YAML files form the machine-queryable capability layer that the pre-flight reasoning code consumes. Design borrowed from [EDAM](https://edamontology.org/page) — separate axes, stable IDs, minimal fields per entry.

| File | EDAM analog | Holds |
|---|---|---|
| `subtasks.yaml` | Topic | High-level task categories (folding, binding, disorder, etc.) and their parent hierarchy. |
| `data_types.yaml` | Data | Things that flow between tools — sequences, PDBs, score arrays, summary metrics. |
| `tools.yaml` | Tool registry (annotated with EDAM terms) | Each callable tool, with `subtask` (Topic), `has_input` / `has_output` (Data), `wrapper` (Python entrypoint), `invocation` style. |

## Conventions

- **IDs are stable.** Once assigned, an ID is permanent. Renames change the `label`, not the ID. Removed entries get `deprecated: true` and a `replaced_by` pointer rather than deletion.
- **IDs are namespaced.** `subtask_*`, `data_*`, `tool_*`. Makes cross-references unambiguous.
- **Forward relations only.** A tool lists its `subtask` and its inputs/outputs. Subtasks do *not* list their tools — that's derivable by querying. One direction = no inconsistencies.
- **Polyhierarchy allowed.** A tool can serve multiple subtasks (ColabFold serves folding, binding, and PPI). A subtask can have multiple parents.
- **One sentence definitions.** Long explanations belong in `queryables.md` or `heuristics.md`; the registry stays terse for fast scanning.

## Loading

Code in `preflight/reasoning.py` will load all three YAML files at import time and expose:

- `lookup_tool(tool_id) -> dict`
- `tools_for_subtask(subtask_id) -> list[dict]`
- `tools_providing(data_type_id) -> list[dict]`
- `tools_requiring(data_type_id) -> list[dict]`

The pre-flight `validate_inputs`, `suggest_analyses`, and `build_roadmap` stubs will use these helpers to compose plans from declared capabilities rather than hardcoded conditionals.

## Adding a new tool

1. If the tool produces a new kind of output, add a `data_types.yaml` entry first.
2. If the tool serves a new task category, add a `subtasks.yaml` entry first.
3. Add the `tools.yaml` entry, referencing the subtask and data type IDs.
4. Update `queryables.md` (the human-readable mirror) in the same change.
