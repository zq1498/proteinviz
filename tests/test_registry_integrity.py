"""Capability registry consistency.

The three YAML files (subtasks, data_types, tools) reference each other by
ID. A typo in any cross-reference silently breaks `tools_for_subtask` /
`tools_providing` / `tools_requiring`. These tests catch that.
"""

from __future__ import annotations

import importlib
import inspect
import re

import pytest

from preflight import reasoning
from preflight.reasoning import (
    _load_registry,
    _reload_registry,
    all_subtasks,
    all_tools,
    resolve_wrapper,
    tools_providing,
    tools_requiring,
)

# Reserved binding tokens the orchestrator resolves itself (see preflight/jobs.py).
_RESERVED_BINDING_TOKENS = {"_job_dir", "_msa_cache"}

# Output-map expressions support exactly three forms (see _resolve_output_expr in tools/batch.py):
#   "."            whole result
#   attr           a single attribute
#   attr[0]        first element of a list attribute
_OUTPUT_EXPR_RE = re.compile(r"^(?:\.|[A-Za-z_][A-Za-z0-9_]*(?:\[0\])?)$")


@pytest.fixture(autouse=True)
def _fresh_registry():
    _reload_registry()
    yield
    _reload_registry()


def test_subtask_references_resolve():
    reg = _load_registry()
    subtask_ids = set(reg["subtasks"].keys())
    for tool in reg["tools"].values():
        for sid in tool.get("subtask", []):
            assert sid in subtask_ids, (
                f"tool {tool['id']} references unknown subtask {sid!r}"
            )


def test_data_type_references_resolve():
    reg = _load_registry()
    data_ids = set(reg["data_types"].keys())
    for tool in reg["tools"].values():
        for did in tool.get("has_input", []):
            assert did in data_ids, (
                f"tool {tool['id']} has_input references unknown data type {did!r}"
            )
        for did in tool.get("has_output", []):
            assert did in data_ids, (
                f"tool {tool['id']} has_output references unknown data type {did!r}"
            )


def test_subtask_parents_resolve():
    reg = _load_registry()
    subtask_ids = set(reg["subtasks"].keys())
    for sub in reg["subtasks"].values():
        parent = sub.get("parent")
        if parent is None:
            continue
        parents = parent if isinstance(parent, list) else [parent]
        for p in parents:
            assert p in subtask_ids, (
                f"subtask {sub['id']} declares unknown parent {p!r}"
            )


def test_data_type_parents_resolve():
    reg = _load_registry()
    data_ids = set(reg["data_types"].keys())
    for dt in reg["data_types"].values():
        parent = dt.get("parent")
        if parent is None:
            continue
        assert parent in data_ids, (
            f"data type {dt['id']} declares unknown parent {parent!r}"
        )


def test_tool_ids_unique_and_prefixed():
    tools = all_tools()
    ids = [t["id"] for t in tools]
    assert len(ids) == len(set(ids)), "duplicate tool ids"
    for tid in ids:
        assert tid.startswith("tool_"), f"tool id {tid!r} should start with 'tool_'"


def test_subtask_ids_unique_and_prefixed():
    subs = all_subtasks()
    ids = [s["id"] for s in subs]
    assert len(ids) == len(set(ids)), "duplicate subtask ids"
    for sid in ids:
        assert sid.startswith("subtask_"), f"subtask id {sid!r} should start with 'subtask_'"


def test_wrappers_resolve_to_callable():
    """Every non-null wrapper must be an importable dotted callable."""
    for tool in all_tools():
        wrapper = tool.get("wrapper")
        if wrapper is None:
            continue
        module_path, _, attr = wrapper.rpartition(".")
        assert module_path and attr, f"wrapper {wrapper!r} for {tool['id']} not dotted"
        mod = importlib.import_module(module_path)
        target = getattr(mod, attr, None)
        assert target is not None, (
            f"wrapper {wrapper!r} for {tool['id']} not found in module"
        )
        assert callable(target), f"wrapper {wrapper!r} for {tool['id']} is not callable"


def test_tools_providing_round_trip():
    """For every output of every tool, that tool appears in tools_providing(output)."""
    for tool in all_tools():
        for output_id in tool.get("has_output", []):
            providers = {t["id"] for t in tools_providing(output_id)}
            assert tool["id"] in providers, (
                f"{tool['id']} declares output {output_id} but tools_providing missed it"
            )


def test_tools_requiring_round_trip():
    for tool in all_tools():
        for input_id in tool.get("has_input", []):
            requirers = {t["id"] for t in tools_requiring(input_id)}
            assert tool["id"] in requirers, (
                f"{tool['id']} declares input {input_id} but tools_requiring missed it"
            )


# ---------------------------------------------------------------------------
# Declarative call contract (B1): bindings, output_map, call_kwargs
# ---------------------------------------------------------------------------

def test_bindings_resolve_to_data_id_or_reserved_token():
    """Every `bindings` value is a known data_* id or a reserved orchestrator token."""
    reg = _load_registry()
    data_ids = set(reg["data_types"].keys())
    for tool in reg["tools"].values():
        for kwarg, source in tool.get("bindings", {}).items():
            assert source in data_ids or source in _RESERVED_BINDING_TOKENS, (
                f"{tool['id']} binds {kwarg!r} to unknown source {source!r}"
            )


def test_output_map_keys_in_has_output_and_well_formed():
    """Every `output_map` key is one of the tool's outputs; every expression is well-formed."""
    for tool in all_tools():
        outputs = set(tool.get("has_output", []))
        for data_id, expr in tool.get("output_map", {}).items():
            assert data_id in outputs, (
                f"{tool['id']} output_map key {data_id!r} not in has_output"
            )
            assert _OUTPUT_EXPR_RE.match(expr), (
                f"{tool['id']} output_map expr {expr!r} for {data_id!r} is malformed"
            )


def test_call_kwargs_accepted_by_wrapper_signature():
    """Static call_kwargs keys must be real parameters of the wrapped callable."""
    for tool in all_tools():
        call_kwargs = tool.get("call_kwargs")
        if not call_kwargs or tool.get("wrapper") is None:
            continue
        fn = resolve_wrapper(tool["id"])
        params = inspect.signature(fn).parameters
        accepts_var_kw = any(
            p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()
        )
        for key in call_kwargs:
            assert accepts_var_kw or key in params, (
                f"{tool['id']} call_kwargs {key!r} not accepted by {tool['wrapper']}"
            )


def test_binding_kwargs_accepted_by_wrapper_signature():
    """Every bound wrapper kwarg must be a real parameter of the wrapped callable."""
    for tool in all_tools():
        bindings = tool.get("bindings")
        if not bindings or tool.get("wrapper") is None:
            continue
        fn = resolve_wrapper(tool["id"])
        params = inspect.signature(fn).parameters
        accepts_var_kw = any(
            p.kind is inspect.Parameter.VAR_KEYWORD for p in params.values()
        )
        for kwarg in bindings:
            assert accepts_var_kw or kwarg in params, (
                f"{tool['id']} binds unknown kwarg {kwarg!r} not in {tool['wrapper']}"
            )


def test_reasoning_module_reexports_job_types():
    """Re-export contract documented in ARCHITECTURE.md must hold."""
    for name in ("Job", "JobStep", "BatchResult", "TaskSpec", "Roadmap"):
        assert hasattr(reasoning, name), f"reasoning is missing {name}"
