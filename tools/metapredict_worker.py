"""metapredict disorder prediction worker — executed only under proteinviz_metapredict (Python 3.11).

metapredict V3 (Ryan et al.) is installed in the proteinviz_metapredict conda env and is not
available in the main proteinviz env. This worker bridges the gap via the same stdin/stdout
JSON protocol used by tools/pyrosetta_worker.py.

HOW THIS SCRIPT IS CALLED:
  tools/metapredict.py spawns this file under the metapredict interpreter:
    /home/zach/miniconda3/envs/proteinviz_metapredict/bin/python tools/metapredict_worker.py

  Input  (stdin):  JSON object  {"function": "<name>", "kwargs": {...}}
  Output (stdout): JSON object  {"result": <data>, "error": null}
                   on failure:  {"result": null, "error": "<message>"}

HOW TO ADD A NEW FUNCTION:
  1. Define _my_function(**kwargs) -> dict
  2. Add "my_function": _my_function to _FUNCTIONS below.
  3. Add a public wrapper in tools/metapredict.py that calls _call_worker("my_function", ...).
"""

from __future__ import annotations

import json
import sys

# Remove the script's own directory from sys.path so that
# "import metapredict" resolves to the installed package and not
# tools/metapredict.py (the caller bridge that lives alongside this file).
import os as _os
_script_dir = _os.path.dirname(_os.path.abspath(__file__))
sys.path = [p for p in sys.path if _os.path.abspath(p) != _script_dir]
del _os, _script_dir

import metapredict as mp


def _predict_disorder(sequence: str) -> dict:
    sequence = sequence.upper().strip()
    scores = mp.predict_disorder(sequence)
    domains = mp.predict_disorder_domains(sequence)
    return {
        "sequence": sequence,
        "disorder_scores": [float(s) for s in scores],
        "percent_disorder": float(mp.percent_disorder(sequence)),
        "disordered_domain_boundaries": [list(b) for b in domains.disordered_domain_boundaries],
        "folded_domain_boundaries": [list(b) for b in domains.folded_domain_boundaries],
    }


_FUNCTIONS: dict = {
    "predict_disorder": _predict_disorder,
}


if __name__ == "__main__":
    request = json.load(sys.stdin)
    fn_name = request.get("function")
    kwargs = request.get("kwargs", {})

    if fn_name not in _FUNCTIONS:
        json.dump({"result": None, "error": f"unknown function: {fn_name!r}"}, sys.stdout)
        sys.exit(1)

    try:
        result = _FUNCTIONS[fn_name](**kwargs)
        json.dump({"result": result, "error": None}, sys.stdout)
    except Exception as exc:
        json.dump({"result": None, "error": str(exc)}, sys.stdout)
        sys.exit(1)
