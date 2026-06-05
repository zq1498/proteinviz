# metapredict integration — status & testing notes

## Status: RESOLVED (2026-06-05)

Both metapredict and localCIDER integrations are fully functional.

---

## Root cause of the original hang

When Python runs `tools/metapredict_worker.py` as a script, it inserts the
script's own directory (`tools/`) as `sys.path[0]`. This caused `import metapredict as mp`
to resolve to `tools/metapredict.py` (the caller bridge) instead of the installed
`metapredict` package. The bridge immediately spawned another worker subprocess,
which did the same — infinite subprocess recursion. Each level timed out silently,
giving the appearance of a 30-60s hang.

The CUDA/GPU theory from the original notes was a red herring. CPU mode
(`CUDA_VISIBLE_DEVICES=""`) and `json.load(sys.stdin)` vs `sys.stdin.read()` are both
kept as defensive improvements, but neither was the root cause.

---

## Fixes applied

**`tools/metapredict_worker.py`**
- Strip `tools/` from `sys.path` before `import metapredict as mp` to prevent
  the shadow-import loop.
- Use `json.load(sys.stdin)` (mirrors pyrosetta_worker pattern).
- `import metapredict` at module level (not deferred inside function).

**`tools/metapredict.py`**
- `_call_worker` passes `CUDA_VISIBLE_DEVICES=""` and a 60s `timeout` to `subprocess.run`.
- Strips stdout prefix before JSON parse (metapredict writes warnings to stdout on
  short sequences — same handling as pyrosetta's license banner).

---

## Validated behavior

```
predict_disorder('ACDEFGHIKLMNPQRSTVWY')  →  ~3.9s, 60% disorder
predict_disorder(MDM2_146aa)              →  18.5% disorder, disordered=[0,27], folded=[27,146]
```

MDM2 result is biologically correct: disordered N-terminus, folded p53-binding domain.
