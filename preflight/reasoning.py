"""Pre-flight reasoning: parse task, query the capability registry, build a roadmap.

The agent (Claude) drives the conversational reasoning. This module exists so
deterministic operations — capability lookup, runtime estimation, input
validation — are reproducible and live in code rather than in prose.

Capability registry layout (loaded from preflight/capabilities/):
    subtasks.yaml    — high-level task categories (Topic axis)
    data_types.yaml  — inputs and outputs that flow between tools (Data axis)
    tools.yaml       — tool entries annotated with subtask + has_input + has_output

See preflight/capabilities/README.md for the schema rules.
"""

from __future__ import annotations

import importlib
import secrets
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Callable

import yaml

from preflight.jobs import Job, JobStep, BatchResult  # re-exported for callers

_CAPABILITIES_DIR = Path(__file__).parent / "capabilities"

__all__ = [
    "TaskSpec", "FeasibilityReport", "Roadmap",
    "Job", "JobStep", "BatchResult",
    "lookup_tool", "lookup_subtask", "lookup_data_type",
    "all_tools", "all_subtasks",
    "tools_for_subtask", "tools_providing", "tools_requiring",
    "resolve_wrapper",
    "estimate_runtime", "validate_inputs", "suggest_analyses", "build_roadmap",
]


class WrapperNotImplementedError(NotImplementedError):
    """Raised when a tool entry exists in the registry but its wrapper field is null."""


class WrapperResolutionError(ImportError):
    """Raised when a tool's wrapper path is set but cannot be imported or resolved."""


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class TaskSpec:
    targets: list[str]
    peptides: list[str] = field(default_factory=list)
    objective: str = ""
    user_requested_metrics: list[str] = field(default_factory=list)


@dataclass
class FeasibilityReport:
    estimated_runtime_s: float
    gpu_required: bool
    warnings: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)


@dataclass
class Roadmap:
    steps: list[str]                                       # human-readable summary shown to user
    decision_points: list[str] = field(default_factory=list)
    suggested_additions: list[str] = field(default_factory=list)
    jobs: list[Job] = field(default_factory=list)          # executable plan consumed by orchestrator


# ---------------------------------------------------------------------------
# Capability registry loader
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def _load_registry() -> dict[str, dict[str, dict]]:
    """Load all three YAML files, returning {kind: {id: entry}} dicts.

    Cached for the process lifetime — registry edits require a restart.
    Use _reload_registry() in tests if needed.
    """
    registry: dict[str, dict[str, dict]] = {}
    for kind, fname in [
        ("subtasks", "subtasks.yaml"),
        ("data_types", "data_types.yaml"),
        ("tools", "tools.yaml"),
    ]:
        path = _CAPABILITIES_DIR / fname
        entries = yaml.safe_load(path.read_text()) or []
        by_id = {e["id"]: e for e in entries}
        if len(by_id) != len(entries):
            raise ValueError(f"Duplicate id in {fname}")
        registry[kind] = by_id
    return registry


def _reload_registry() -> None:
    """Drop the cache. For tests / interactive development."""
    _load_registry.cache_clear()


# ---------------------------------------------------------------------------
# Lookup helpers
# ---------------------------------------------------------------------------

def lookup_tool(tool_id: str) -> dict:
    """Return the tool entry for tool_id, or raise KeyError."""
    return _load_registry()["tools"][tool_id]


def lookup_subtask(subtask_id: str) -> dict:
    return _load_registry()["subtasks"][subtask_id]


def lookup_data_type(data_id: str) -> dict:
    return _load_registry()["data_types"][data_id]


def all_tools() -> list[dict]:
    return list(_load_registry()["tools"].values())


def all_subtasks() -> list[dict]:
    return list(_load_registry()["subtasks"].values())


def tools_for_subtask(subtask_id: str, include_children: bool = True) -> list[dict]:
    """Tools annotated with subtask_id. If include_children, also matches descendants."""
    target_ids = {subtask_id}
    if include_children:
        target_ids |= _descendant_subtasks(subtask_id)
    return [t for t in all_tools() if set(t.get("subtask", [])) & target_ids]


def tools_providing(data_type_id: str) -> list[dict]:
    """Tools whose has_output includes data_type_id."""
    return [t for t in all_tools() if data_type_id in t.get("has_output", [])]


@lru_cache(maxsize=None)
def resolve_wrapper(tool_id: str) -> Callable:
    """Resolve a registry tool_id to its callable Python wrapper.

    Reads the tool's `wrapper` field (a dotted path like "tools.predict.run_colabfold"),
    imports the module, and returns the named attribute. Cached per tool_id so the
    orchestrator does not pay the import cost on every step.

    Raises:
        KeyError: tool_id is not in the registry.
        WrapperNotImplementedError: registry entry exists but wrapper is null
            (the tool is declared but not yet code-wrapped).
        WrapperResolutionError: wrapper path is set but the module or attribute
            cannot be loaded — usually a typo in tools.yaml or a not-yet-written wrapper.
    """
    entry = lookup_tool(tool_id)  # raises KeyError if missing
    dotted = entry.get("wrapper")
    if dotted is None:
        raise WrapperNotImplementedError(
            f"{tool_id} ({entry['label']}) has no wrapper registered in tools.yaml. "
            f"Add a dotted Python path to its `wrapper` field, or wrap it before invocation."
        )
    module_path, _, attr = dotted.rpartition(".")
    if not module_path:
        raise WrapperResolutionError(
            f"{tool_id} wrapper '{dotted}' is not a dotted path (need module.attr)."
        )
    try:
        module = importlib.import_module(module_path)
    except ImportError as e:
        raise WrapperResolutionError(
            f"{tool_id} wrapper '{dotted}': could not import module '{module_path}': {e}"
        ) from e
    try:
        fn = getattr(module, attr)
    except AttributeError as e:
        raise WrapperResolutionError(
            f"{tool_id} wrapper '{dotted}': module '{module_path}' has no attribute '{attr}'."
        ) from e
    if not callable(fn):
        raise WrapperResolutionError(
            f"{tool_id} wrapper '{dotted}' resolved to a non-callable ({type(fn).__name__})."
        )
    return fn


def tools_requiring(data_type_id: str) -> list[dict]:
    """Tools whose has_input includes data_type_id."""
    return [t for t in all_tools() if data_type_id in t.get("has_input", [])]


def _descendant_subtasks(parent_id: str) -> set[str]:
    """All subtask IDs whose `parent` chain includes parent_id."""
    subtasks = _load_registry()["subtasks"]
    descendants: set[str] = set()
    frontier = {parent_id}
    while frontier:
        next_frontier: set[str] = set()
        for sub in subtasks.values():
            parent = sub.get("parent")
            parents = parent if isinstance(parent, list) else [parent] if parent else []
            if frontier & set(parents):
                if sub["id"] not in descendants:
                    descendants.add(sub["id"])
                    next_frontier.add(sub["id"])
        frontier = next_frontier
    return descendants


# ---------------------------------------------------------------------------
# Pre-flight reasoning helpers
# ---------------------------------------------------------------------------

def estimate_runtime(n_targets: int, n_peptides: int, avg_length: int) -> float:
    """Rough runtime estimate in seconds for a full pairwise sweep on one GPU.

    Calibration TODO — replace constants after first real runs.
    """
    pairs = max(1, n_targets) * max(1, n_peptides or 1)
    per_pair_s = 60 + 0.5 * avg_length
    return pairs * per_pair_s


def validate_inputs(spec: TaskSpec) -> list[str]:
    """Return human-readable warnings derived from sequence-level heuristics.

    Mirrors the rules in preflight/heuristics.md § Sequence-level flags.
    """
    warnings: list[str] = []
    for kind, seqs in [("target", spec.targets), ("peptide", spec.peptides)]:
        for i, seq in enumerate(seqs):
            warnings.extend(_validate_one(kind, i, seq))
    return warnings


def _validate_one(kind: str, idx: int, seq: str) -> list[str]:
    out: list[str] = []
    n = len(seq)
    tag = f"{kind}[{idx}]"
    if kind == "peptide" and n < 5:
        out.append(f"{tag}: length {n} is too short to form a stable interface")
    if kind == "peptide" and n > 40:
        out.append(f"{tag}: length {n} > 40 inflates ipTM denominator; rely on actifpTM")
    if kind == "target" and n > 2000:
        out.append(f"{tag}: length {n} > 2000 may OOM ColabFold; consider domain-wise prediction")
    cys_positions = [i for i, aa in enumerate(seq) if aa == "C"]
    for a, b in zip(cys_positions, cys_positions[1:]):
        if b - a <= 8:
            out.append(f"{tag}: cysteine pair at {a + 1},{b + 1} (Δ={b - a}) — potential intramolecular disulfide")
            break
    return out


def suggest_analyses(spec: TaskSpec) -> list[str]:
    """Suggest analyses the user did not explicitly request but the inputs warrant.

    Each suggestion names the tool (by registry id) so the agent can resolve
    the call concretely.
    """
    suggestions: list[str] = []
    if len(spec.targets) > 1:
        suggestions.append(
            "Multiple targets — generate isoform-discrimination ΔipTM table across the panel."
        )
    long_targets = [i for i, s in enumerate(spec.targets) if len(s) > 200]
    if long_targets:
        tools = tools_for_subtask("subtask_disorder_core")
        names = ", ".join(t["label"] for t in tools) or "no disorder tool registered"
        suggestions.append(
            f"Targets {long_targets} are >200 residues — run disorder check first ({names})."
        )
    if any("C" in s for s in spec.peptides):
        suggestions.append(
            "Peptides contain cysteines — consider reduced vs. oxidized predictions."
        )
    return suggestions


def build_roadmap(spec: TaskSpec, feasibility: FeasibilityReport) -> Roadmap:
    """Build an ordered execution plan from the task spec and registry.

    Returns a Roadmap with both a human-readable `steps` summary and a
    machine-executable `jobs` list. The orchestrator consumes `jobs`; the
    agent shows `steps` to the user during pre-flight.

    Branches on task shape: binding (peptides given) vs. monomer folding
    (no peptides). Other task shapes (mutation, ppi, disorder-only) get
    their own branches as those tasks come up.
    """
    if spec.peptides:
        steps, jobs = _binding_branch(spec)
    else:
        steps, jobs = _monomer_branch(spec)

    decision_points: list[str] = []
    if any(len(s) > 200 for s in spec.targets):
        decision_points.append(
            "Run disorder check on targets before interpreting low-pLDDT regions?"
        )

    steps.append("Compute composite scores and rank")
    steps.append("Render markdown report with flagged observations")

    return Roadmap(
        steps=steps,
        decision_points=decision_points,
        suggested_additions=suggest_analyses(spec),
        jobs=jobs,
    )


def _binding_branch(spec: TaskSpec) -> tuple[list[str], list[Job]]:
    """Emit one Job per (target, peptide) pair with a predict + extract plan."""
    binding_tools = tools_for_subtask("subtask_binding")
    if not binding_tools:
        raise RuntimeError("No tools registered for subtask_binding")
    primary = binding_tools[0]   # tool_colabfold by current registry order

    n_pairs = len(spec.targets) * len(spec.peptides)
    steps = [
        f"Predict {n_pairs} target-peptide complexes via {primary['id']}",
        "Extract ipTM, actifpTM, pLDDT, PAE per complex",
    ]

    jobs: list[Job] = []
    for ti, target in enumerate(spec.targets):
        for pi, peptide in enumerate(spec.peptides):
            target_id = f"target_{ti}"
            peptide_id = f"peptide_{pi}"
            jobs.append(Job(
                job_id=f"{target_id}__{peptide_id}__{secrets.token_hex(4)}",
                inputs={
                    "data_aa_sequence": [target, peptide],
                    "data_fasta_file": None,
                },
                plan=[
                    JobStep(
                        tool_id=primary["id"],
                        kwargs={"fast": False},
                        input_bindings={
                            "fasta": "data_fasta_file",
                            "out_dir": "_job_dir",
                        },
                        consumes=["data_fasta_file"],
                        produces=primary.get("has_output", []),
                    ),
                    JobStep(
                        tool_id="tool_extract_colabfold",
                        input_bindings={"out_dir": "_job_dir"},
                        consumes=["data_pdb_complex"],
                        produces=[
                            "data_iptm_score", "data_actifptm_score",
                            "data_ptm_score", "data_plddt_per_residue", "data_pae_matrix",
                        ],
                    ),
                ],
                metadata={
                    "target_id": target_id,
                    "peptide_id": peptide_id,
                    "kind": "binding",
                },
            ))
    return steps, jobs


def _monomer_branch(spec: TaskSpec) -> tuple[list[str], list[Job]]:
    """Emit one Job per target with a predict + extract plan (single chain)."""
    folding_tools = tools_for_subtask("subtask_folding")
    if not folding_tools:
        raise RuntimeError("No tools registered for subtask_folding")
    primary = folding_tools[0]

    steps = [
        f"Predict {len(spec.targets)} monomer structures via {primary['id']}",
        "Extract per-residue pLDDT and pTM",
    ]

    jobs: list[Job] = []
    for ti, target in enumerate(spec.targets):
        target_id = f"target_{ti}"
        jobs.append(Job(
            job_id=f"{target_id}__monomer__{secrets.token_hex(4)}",
            inputs={
                "data_aa_sequence": target,
                "data_fasta_file": None,
            },
            plan=[
                JobStep(
                    tool_id=primary["id"],
                    kwargs={"fast": False},
                    input_bindings={
                        "fasta": "data_fasta_file",
                        "out_dir": "_job_dir",
                    },
                    consumes=["data_fasta_file"],
                    produces=primary.get("has_output", []),
                ),
                JobStep(
                    tool_id="tool_extract_colabfold",
                    input_bindings={"out_dir": "_job_dir"},
                    consumes=["data_pdb_file"],
                    produces=["data_ptm_score", "data_plddt_per_residue", "data_pae_matrix"],
                ),
            ],
            metadata={"target_id": target_id, "kind": "monomer"},
        ))
    return steps, jobs
