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
    "resolve_wrapper", "make_step", "select_tool",
    "validate_inputs", "suggest_analyses", "build_roadmap",
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
    # Opt-in analyses (registry tool ids) the agent confirmed during pre-flight.
    # build_roadmap appends the matching analysis steps to every job's plan.
    # Sequence-based: _OPT_IN_SEQ_ANALYSES (iupred3, metapredict, localcider).
    # Structure-based: "tool_rosetta_confidence_filter" triggers the alanine-scan
    # → confidence-filter hotspot chain for binding/panel jobs.
    requested_analyses: list[str] = field(default_factory=list)


@dataclass
class FeasibilityReport:
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


def select_tool(subtask_id: str, *, require_wrapper: bool = True, max_tier: int = 2) -> dict:
    """Pick the preferred callable tool for a subtask, explicitly (not positionally).

    Filters out deprecated tools, tools above `max_tier`, and (when
    `require_wrapper`) tools with no `wrapper`. Ranks the survivors by
    `(tier, -priority, id)` — lower tier wins, then higher `priority`, then a
    stable id tiebreak. This replaces the old `tools_for_subtask(...)[0]`, which
    silently changed backend if `tools.yaml` was reordered.

    Raises RuntimeError if no tool qualifies.
    """
    cands = [
        t for t in tools_for_subtask(subtask_id)
        if not t.get("deprecated")
        and (t.get("wrapper") or not require_wrapper)
        and t.get("tier", 99) <= max_tier
    ]
    if not cands:
        raise RuntimeError(f"No selectable tool for {subtask_id}")
    return sorted(cands, key=lambda t: (t.get("tier", 99), -t.get("priority", 0), t["id"]))[0]


def _unimplemented_tool_notes(subtask_id: str, *, max_tier: int = 2) -> list[str]:
    """Decision-point notes for tools registered under a subtask but not yet callable.

    Keeps the capability map visible during pre-flight: a tool that is declared
    (so the agent knows it exists) but has a null wrapper or is above max_tier is
    surfaced as a note rather than silently ignored or silently routed to.
    """
    notes: list[str] = []
    for t in tools_for_subtask(subtask_id):
        if t.get("deprecated"):
            continue
        no_wrapper = not t.get("wrapper")
        too_high = t.get("tier", 99) > max_tier
        if no_wrapper or too_high:
            reason = "no wrapper" if no_wrapper else f"tier {t.get('tier')}"
            notes.append(
                f"{t['label']} ({t['id']}) is registered for {subtask_id} but not yet "
                f"callable ({reason}) — not routed to."
            )
    return notes


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
# Registry-driven step builder
# ---------------------------------------------------------------------------

def make_step(
    tool_id: str,
    *,
    kwarg_overrides: dict | None = None,
    binding_overrides: dict | None = None,
    optional: bool = False,
) -> JobStep:
    """Build a JobStep from a tool's registry call contract.

    Pulls `call_kwargs`, `bindings`, `has_input` (→ consumes) and `has_output`
    (→ produces) from the registry entry so a roadmap branch only has to name
    the tool plus any per-use overrides, instead of hand-writing the wiring.

    kwarg_overrides win over the registry's `call_kwargs`; binding_overrides win
    over the registry's `bindings` (e.g. the panel path rebinds `out_dir` to
    `data_colabfold_out_dir`). `optional=True` marks supplemental steps (analysis)
    so the orchestrator logs-and-continues on failure rather than failing the Job.
    """
    entry = lookup_tool(tool_id)
    return JobStep(
        tool_id=tool_id,
        kwargs={**entry.get("call_kwargs", {}), **(kwarg_overrides or {})},
        input_bindings={**entry.get("bindings", {}), **(binding_overrides or {})},
        consumes=list(entry.get("has_input", [])),
        produces=list(entry.get("has_output", [])),
        optional=optional,
    )


# Always-on analysis steps appended to every binding/monomer plan (supplemental,
# optional). Routed through the registry so today's behaviour (analyze_structure +
# analyze_sequence after the plan) is preserved without the hardcoded post-plan hook.
# For binding/panel jobs the sequence analyzer runs on the peptide (data_peptide_sequence);
# for monomers it runs on the single target (data_aa_sequence default binding).
_ALWAYS_ON_STRUCTURE = "tool_freesasa"
_ALWAYS_ON_SEQUENCE = "tool_analyze_sequence"

# Opt-in, sequence-based analyses the agent can request via TaskSpec.requested_analyses.
_OPT_IN_SEQ_ANALYSES = frozenset({"tool_iupred3", "tool_metapredict", "tool_localcider"})

# Opt-in structure-based hotspot chain. The agent writes "tool_rosetta_confidence_filter"
# in requested_analyses; _append_requested_analyses appends both steps in order.
_HOTSPOT_MARKER = "tool_rosetta_confidence_filter"

# ColabFold chain assignment: single-pair binding = target chain A, peptide chain B.
# Panel homodimer target = chains A, B + peptide chain C.
# The peptide is always the last chain (_peptide_chain_plddt in report.py relies on this).
_INTERFACE_SINGLE_PAIR = "A_B"
_INTERFACE_PANEL_HOMODIMER = "AB_C"  # correct for current VEGF homodimer panels


def _analysis_steps(*, peptide_sequence: bool) -> list[JobStep]:
    """Always-on structure + sequence analysis steps for one job's plan.

    `peptide_sequence=True` (binding/panel) binds the sequence analyzer to the
    peptide; False (monomer) uses the default data_aa_sequence binding.
    """
    seq_override = {"seq": "data_peptide_sequence"} if peptide_sequence else None
    return [
        make_step(_ALWAYS_ON_STRUCTURE, optional=True),
        make_step(_ALWAYS_ON_SEQUENCE, binding_overrides=seq_override, optional=True),
    ]


# ---------------------------------------------------------------------------
# Pre-flight reasoning helpers
# ---------------------------------------------------------------------------

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

    if spec.requested_analyses:
        added = _append_requested_analyses(jobs, spec.requested_analyses)
        if added:
            steps.append(f"Run opt-in analyses: {', '.join(added)}")

    decision_points: list[str] = []
    if any(len(s) > 200 for s in spec.targets):
        decision_points.append(
            "Run disorder check on targets before interpreting low-pLDDT regions?"
        )
    # Keep the capability map visible: note tools declared for the active subtask
    # that aren't yet callable, rather than silently ignoring or routing to them.
    primary_subtask = "subtask_binding" if spec.peptides else "subtask_folding"
    decision_points.extend(_unimplemented_tool_notes(primary_subtask))

    steps.append("Compute composite scores and rank")
    steps.append("Render markdown report with flagged observations")

    return Roadmap(
        steps=steps,
        decision_points=decision_points,
        suggested_additions=suggest_analyses(spec),
        jobs=jobs,
    )


def _append_requested_analyses(jobs: list[Job], requested: list[str]) -> list[str]:
    """Append opt-in analyses to every job's plan, in-place.

    Handles two opt-in categories:
    - Sequence-based (_OPT_IN_SEQ_ANALYSES): iupred3, metapredict, localcider.
      Added for all job kinds; bound to peptide sequence for complex jobs.
    - Structure-based hotspot chain (_HOTSPOT_MARKER): alanine_scan →
      confidence_filter. Added only for binding / binding_panel jobs (complexes
      have an interface; monomers do not). Both steps are optional.

    Unknown or not-yet-supported ids are skipped. Returns the sorted list of
    tool ids actually added (for the human-readable roadmap).
    """
    added: set[str] = set()
    want_hotspots = _HOTSPOT_MARKER in requested

    for job in jobs:
        kind = job.metadata.get("kind")
        is_complex = kind in {"binding", "binding_panel"}
        seq_override = {"seq": "data_peptide_sequence"} if is_complex else None

        for tool_id in requested:
            if tool_id not in _OPT_IN_SEQ_ANALYSES:
                continue
            job.plan.append(
                make_step(tool_id, binding_overrides=seq_override, optional=True)
            )
            added.add(tool_id)

        if want_hotspots and is_complex:
            interface = (
                _INTERFACE_PANEL_HOMODIMER if kind == "binding_panel"
                else _INTERFACE_SINGLE_PAIR
            )
            job.plan.append(
                make_step(
                    "tool_rosetta_alanine_scan",
                    kwarg_overrides={"interface": interface},
                    optional=True,
                )
            )
            job.plan.append(make_step("tool_rosetta_confidence_filter", optional=True))
            added.add(_HOTSPOT_MARKER)

    return sorted(added)


def _binding_branch(spec: TaskSpec) -> tuple[list[str], list[Job]]:
    """Route to per-pair or panel path depending on peptide count."""
    if len(spec.peptides) > 1:
        return _panel_branch(spec)
    return _single_pair_branch(spec)


def _single_pair_branch(spec: TaskSpec) -> tuple[list[str], list[Job]]:
    """Emit one Job per (target, peptide) pair with a predict + extract plan."""
    primary_id = select_tool("subtask_binding")["id"]   # tier-aware, priority-ranked

    n_pairs = len(spec.targets) * len(spec.peptides)
    steps = [
        f"Predict {n_pairs} target-peptide complex(es) via {primary_id}",
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
                    "data_peptide_sequence": peptide,
                    "data_fasta_file": None,
                },
                plan=[
                    make_step(primary_id, kwarg_overrides={"calc_extra_ptm": True}),
                    make_step("tool_extract_colabfold"),
                    *_analysis_steps(peptide_sequence=True),
                ],
                metadata={
                    "target_id": target_id,
                    "peptide_id": peptide_id,
                    "kind": "binding",
                },
            ))
    return steps, jobs


def _panel_branch(spec: TaskSpec) -> tuple[list[str], list[Job]]:
    """Emit extraction-only Jobs for a multi-peptide panel.

    Prediction is handled upstream by run_batch calling predict_panel per target
    (one primary MSA job + SLURM array). By the time _run_one_job runs, each job's
    data_colabfold_out_dir is already populated with the downloaded ColabFold outputs.
    """
    n_pairs = len(spec.targets) * len(spec.peptides)
    steps = [
        f"Submit {len(spec.targets)} panel job(s) via predict_panel "
        f"({len(spec.peptides)} peptides × MSA-cached SLURM array, "
        f"{len(spec.peptides) - 1} fewer colabfold_search run(s) per target)",
        f"Wait for {n_pairs} prediction(s) and download results",
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
                    "data_peptide_sequence": peptide,
                    "data_colabfold_out_dir": None,  # filled by run_batch after predict_panel
                    "data_pdb_complex": None,         # filled by run_batch after predict_panel
                    "data_pdb_file": None,            # filled by run_batch after predict_panel
                },
                plan=[
                    make_step(
                        "tool_extract_colabfold",
                        binding_overrides={"out_dir": "data_colabfold_out_dir"},
                    ),
                    *_analysis_steps(peptide_sequence=True),
                ],
                metadata={
                    "target_id": target_id,
                    "target_idx": ti,
                    "peptide_id": peptide_id,
                    "peptide_idx": pi,
                    "kind": "binding_panel",
                },
            ))
    return steps, jobs


def _monomer_branch(spec: TaskSpec) -> tuple[list[str], list[Job]]:
    """Emit one Job per target with a predict + extract plan (single chain)."""
    primary_id = select_tool("subtask_folding")["id"]   # tier-aware, priority-ranked

    steps = [
        f"Predict {len(spec.targets)} monomer structures via {primary_id}",
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
                make_step(primary_id, kwarg_overrides={"calc_extra_ptm": False}),
                make_step("tool_extract_colabfold"),
                *_analysis_steps(peptide_sequence=False),
            ],
            metadata={"target_id": target_id, "kind": "monomer"},
        ))
    return steps, jobs
