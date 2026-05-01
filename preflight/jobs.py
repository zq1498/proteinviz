"""Task-agnostic execution dataclasses consumed by the orchestrator.

These types describe *what* the orchestrator should do (Job, JobStep) and
*what came back* (BatchResult). They contain no domain knowledge — a Job
can equally describe a peptide-protein binding prediction, a monomer fold,
a disorder check, or a structural comparison. The shape is decided by
whatever Roadmap branch produced the Job in `preflight.reasoning`.

Why these live in their own module:
- `reasoning.py` owns registry lookup + pre-flight reasoning (declarative).
- `jobs.py` owns the executable types (consumed by the orchestrator).
- Splitting avoids a circular dependency once `tools/batch.py` imports
  these and also wants to call back into `reasoning.lookup_tool` without
  pulling in the whole reasoning surface.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal


JobStatus = Literal["pending", "running", "done", "skipped", "failed"]


@dataclass
class JobStep:
    """One tool invocation within a Job's plan.

    `kwargs` holds static configuration for the call (e.g. `{"fast": False}`).
    `input_bindings` maps the wrapper's kwarg name to a key in `Job.inputs`,
    so the orchestrator can rebind data at call time without the plan having
    to know runtime paths. Two reserved binding values:
      - "_job_dir"   → resolves to Job.job_dir at call time
      - "_msa_cache" → resolves to a Path from the orchestrator's MSA cache,
                       keyed by the sha1 of the corresponding sequence input.

    `consumes` and `produces` mirror the registry's `has_input` / `has_output`
    for the named tool. They are advisory — used to sanity-check the chain
    (step N produces what step N+1 consumes) and to drive the MSA cache
    pre-computation. The orchestrator does not enforce them at call time.
    """
    tool_id: str
    kwargs: dict[str, Any] = field(default_factory=dict)
    input_bindings: dict[str, str] = field(default_factory=dict)
    consumes: list[str] = field(default_factory=list)
    produces: list[str] = field(default_factory=list)


@dataclass
class Job:
    """One unit of work the orchestrator walks end-to-end.

    `inputs` is a named bundle whose keys are data_type ids from the
    capability registry (e.g. `"data_aa_sequence"`, `"data_pdb_complex"`).
    Values can be raw sequences, file paths, or anything the bound wrapper
    accepts — the orchestrator does not type-check them.

    `results` is keyed by `tool_id`, so the report adapter can ask
    "what did `tool_colabfold` return for this Job" without knowing the
    Job's task shape.

    `metadata` is the escape hatch for task-specific labels that have no
    role in execution but matter for downstream presentation
    (e.g. `{"target_id": "VEGF165", "peptide_id": "p3", "kind": "binding"}`).
    Keep load-bearing fields out of here.
    """
    job_id: str
    inputs: dict[str, Any]
    plan: list[JobStep]
    status: JobStatus = "pending"
    results: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    runtime_s: float = 0.0
    job_dir: Path | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class BatchResult:
    """The full result of one orchestrator run.

    The orchestrator returns this; the report adapter consumes it. Holds
    the original spec and roadmap for traceability and the executed Jobs.
    `msa_cache_keys` records the sequence sha1s the MSA cache resolved
    during this run, so reports can show where MSA reuse paid off.
    """
    spec: Any                # preflight.reasoning.TaskSpec — typed loosely to avoid import cycle
    roadmap: Any             # preflight.reasoning.Roadmap
    jobs: list[Job]
    msa_cache_keys: list[str] = field(default_factory=list)
    started_at: str | None = None
    finished_at: str | None = None

    @property
    def n_done(self) -> int:
        return sum(1 for j in self.jobs if j.status == "done")

    @property
    def n_failed(self) -> int:
        return sum(1 for j in self.jobs if j.status == "failed")

    @property
    def n_skipped(self) -> int:
        return sum(1 for j in self.jobs if j.status == "skipped")
