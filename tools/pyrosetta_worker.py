"""PyRosetta analysis worker — executed only under proteinviz_pyrosetta (Python 3.14).

WHY THIS IS A SEPARATE SCRIPT, NOT A MODULE:
PyRosetta is distributed via conda.graylab.jhu.edu as a version-specific wheel.
The current release (2026.19) is built only for Python 3.14 (py314_0). The main
proteinviz env runs Python 3.12 and cannot install it — there is no py312 build
at this release. Rather than upgrade the entire stack, PyRosetta is isolated in
its own environment and called via subprocess from tools/rosetta.py.

HOW THIS SCRIPT IS CALLED:
  tools/rosetta.py spawns this file under the pyrosetta interpreter:
    /home/zach/miniconda3/envs/proteinviz_pyrosetta/bin/python tools/pyrosetta_worker.py

  Input  (stdin):  JSON object  {"function": "<name>", "kwargs": {...}}
  Output (stdout): JSON object  {"result": <data>, "error": null}
                   on failure:  {"result": null, "error": "<message>"}
  stderr is forwarded as-is and surfaced by the caller on error.

HOW TO ADD A NEW FUNCTION:
  1. Define _my_function(arg1, arg2=default, ...) -> dict | list[dict]
     All values must be JSON-serialisable (str, int, float, bool, None, list, dict).
  2. Add "my_function": _my_function to _FUNCTIONS below.
  3. Add a public wrapper in tools/rosetta.py that calls _call_worker("my_function", ...).

PYROSETTA 2026.19 API NOTES (verify on upgrade):
  - InterfaceAnalyzerMover exposes get_interface_set() -> std::set<unsigned long> of
    Rosetta residue numbers. Older docs/examples show get_interface_residues(), which
    does not exist in this release.
  - mutate_residue() (pyrosetta.toolbox.mutants) requires a single-letter amino acid
    code ("A"), not three-letter ("ALA"). Three-letter codes raise "Expected a
    character, but multi-character string found" at the C++ boundary.
  - The license banner is written to C-level fd 1 at import time and cannot be
    suppressed via sys.stdout redirect. The caller (rosetta._call_worker) handles
    this by scanning stdout for the first '{' before JSON parsing.
"""

from __future__ import annotations

import json
import sys

import pyrosetta  # available only under proteinviz_pyrosetta — never import from proteinviz


def _score_interface(pdb_path: str, interface: str = "A_B") -> dict:
    """REF2015 interface analysis via InterfaceAnalyzerMover.

    Returns dG_separated (kcal/mol REU), dSASA (Å²), and interface residue count.
    pack_separated=True repacks the separated chains before scoring, which gives a
    more physically meaningful binding ΔG than raw complex minus separated scores.
    """
    from pyrosetta.rosetta.protocols.analysis import InterfaceAnalyzerMover

    pose = pyrosetta.pose_from_pdb(pdb_path)
    sfxn = pyrosetta.get_fa_scorefxn()
    total_score = sfxn(pose)

    iam = InterfaceAnalyzerMover(interface)
    iam.set_scorefunction(sfxn)
    iam.set_pack_separated(True)
    iam.apply(pose)

    return {
        "interface": interface,
        "total_score_reu": float(total_score),
        "dG_separated": float(iam.get_interface_dG()),
        "dSASA": float(iam.get_interface_delta_sasa()),
        "n_interface_residues": int(iam.get_num_interface_residues()),
    }


def _per_residue_energies(pdb_path: str) -> list[dict]:
    """Per-residue total energy contributions (REF2015, kcal/mol REU).

    Returns both Rosetta numbering (sequential 1-indexed) and PDB numbering.
    Use PDB numbering when cross-referencing with ColabFold outputs or viewers;
    the two can diverge if the PDB has insertion codes or non-standard residues.
    """
    pose = pyrosetta.pose_from_pdb(pdb_path)
    sfxn = pyrosetta.get_fa_scorefxn()
    sfxn(pose)
    energies = pose.energies()
    pdb_info = pose.pdb_info()

    result = []
    for i in range(1, pose.total_residue() + 1):
        res = pose.residue(i)
        result.append({
            "rosetta_resnum": i,
            "pdb_resnum": int(pdb_info.number(i)) if pdb_info else i,
            "chain": str(pdb_info.chain(i)) if pdb_info else "?",
            "resname": res.name3().strip(),
            "total_energy": float(energies.residue_total_energy(i)),
        })
    return result


def _alanine_scan(pdb_path: str, interface: str = "A_B", rigorous: bool = False) -> list[dict]:
    """ΔΔG for each non-Ala/Gly interface residue via single-point alanine mutation.

    Two modes controlled by `rigorous`:

    Fast (rigorous=False, default):
        ΔΔG = sfxn(complex_mut) − sfxn(complex_wt). Cheap but conflates folding
        stability with binding affinity — a residue that merely destabilises the
        peptide fold will score as a hot-spot even if it doesn't touch the interface.
        Good for rapid relative ranking within a single peptide.

    Rigorous (rigorous=True):
        ΔΔG = ΔG_separated(mut) − ΔG_separated(wt), where each ΔG_separated is
        computed by InterfaceAnalyzerMover (pack_separated=True). FastRelax is
        applied to the mutant pose before scoring to remove clash geometry introduced
        by the side-chain swap. This is the correct binding ddG and is suitable for
        post-screening hit characterisation. ~2–5× slower per residue.

    Positive ΔΔG in either mode means the wildtype residue contributes favourably
    to the interface — hot-spot candidate. Results sorted descending by ΔΔG.
    """
    from pyrosetta.rosetta.protocols.analysis import InterfaceAnalyzerMover
    from pyrosetta.toolbox.mutants import mutate_residue

    pose = pyrosetta.pose_from_pdb(pdb_path)
    sfxn = pyrosetta.get_fa_scorefxn()

    iam = InterfaceAnalyzerMover(interface)
    iam.set_scorefunction(sfxn)
    # pack_separated only needed in rigorous mode (required for get_interface_dG to be
    # meaningful); skipping it in fast mode avoids the extra repack at no accuracy cost
    # since fast mode uses the total-complex score, not dG_separated.
    iam.set_pack_separated(rigorous)
    iam.apply(pose)
    # get_interface_set() is the 2026.19 name; get_interface_residues() does not exist.
    interface_resnums = list(iam.get_interface_set())

    if rigorous:
        from pyrosetta.rosetta.protocols.relax import FastRelax
        dG_wt = float(iam.get_interface_dG())
        fr = FastRelax()
        fr.set_scorefxn(sfxn)
    else:
        baseline = sfxn(pose)

    pdb_info = pose.pdb_info()
    results = []
    for i in interface_resnums:
        res = pose.residue(i)
        resname = res.name3().strip()
        if resname in ("ALA", "GLY"):
            continue
        mut_pose = pose.clone()
        mutate_residue(mut_pose, i, "A", 9.0, sfxn)  # single-letter required; "ALA" raises C++ error

        if rigorous:
            fr.apply(mut_pose)
            iam_mut = InterfaceAnalyzerMover(interface)
            iam_mut.set_scorefunction(sfxn)
            iam_mut.set_pack_separated(True)
            iam_mut.apply(mut_pose)
            ddG = float(iam_mut.get_interface_dG()) - dG_wt
        else:
            ddG = float(sfxn(mut_pose) - baseline)

        results.append({
            "rosetta_resnum": i,
            "pdb_resnum": int(pdb_info.number(i)) if pdb_info else i,
            "chain": str(pdb_info.chain(i)) if pdb_info else "?",
            "resname": resname,
            "ddG": ddG,
        })
    return sorted(results, key=lambda r: r["ddG"], reverse=True)


_FUNCTIONS: dict = {
    "score_interface": _score_interface,
    "per_residue_energies": _per_residue_energies,
    "alanine_scan": _alanine_scan,
}


if __name__ == "__main__":
    request = json.load(sys.stdin)
    fn_name = request.get("function")
    kwargs = request.get("kwargs", {})

    if fn_name not in _FUNCTIONS:
        json.dump({"result": None, "error": f"unknown function: {fn_name!r}"}, sys.stdout)
        sys.exit(1)

    try:
        pyrosetta.init("-mute all")
        result = _FUNCTIONS[fn_name](**kwargs)
        json.dump({"result": result, "error": None}, sys.stdout)
    except Exception as exc:
        json.dump({"result": None, "error": str(exc)}, sys.stdout)
        sys.exit(1)
