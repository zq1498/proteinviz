"""Validate that build_paired_a3m produces equivalent ColabFold predictions
to a fresh full-complex MSA search.

Two test complexes:
  cam    — Calmodulin (148 aa) + MLCK peptide (26 aa)  [PDB: 2BBM]
  mdm2   — MDM2 binding domain (84 aa) + p53 peptide (15 aa)  [PDB: 1YCR]

For each complex:
  Step 1 (parallel): fresh complex prediction + target monomer prediction
  Step 2:            build paired A3M from monomer A3M + peptide
  Step 3 (parallel): cached prediction using pre_msa_dir
  Step 4:            compare top models per-chain; print metric delta table

Run:
  python tests/validate_msa_caching.py
"""

from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))

from tools.compare import compare, ComparisonResult
from tools.extract import extract_colabfold, ExtractedMetrics
from tools.msa import build_paired_a3m
from tools.predict import check_backend, predict_structure, write_fasta

JOBS_ROOT = ROOT / "jobs" / "validate_msa_caching"

# Sequences extracted from the reformatted complex FASTAs
COMPLEXES: dict[str, dict] = {
    "cam": {
        "complex_fasta": ROOT / "2BBM_2_CAM_complex.txt",
        "target_name": "calmodulin_2BBM",
        "target_seq": (
            "ADQLTEEQIAEFKEAFSLFDKDGDGTITTKELGTVMRSLGQNPTEAELQDMINEVDADGNGTID"
            "FPEFLTMMARKMKDTDSEEEIREAFRVFDKDGNGFISAAELRHVMTNLGEKLTDEEVDEMIREA"
            "DIDGDGQVNYEEFVTMMTSK"
        ),
        "peptide_id": "mlck_peptide",
        "peptide_seq": "KRRWKKNFIAVSAANRFKKISSSGAL",
    },
    "mdm2": {
        "complex_fasta": ROOT / "1YCR_MDM2_complex.txt",
        "target_name": "mdm2_1YCR",
        "target_seq": (
            "ETLVRPKPLLLKLLKSVGAQKDTYTMKEVLFYLGQYIMTKRLYDEKQQHIVYCSNDLLGDLFG"
            "VPSFSVKEHRKIYTMIYRNLVV"
        ),
        "peptide_id": "p53_peptide",
        "peptide_seq": "SQETFSDLWKLLPEN",
    },
}


# ---------------------------------------------------------------------------
# Step functions
# ---------------------------------------------------------------------------

def run_fresh(key: str, cfg: dict) -> Path:
    out = JOBS_ROOT / f"{key}_fresh"
    print(f"[{key}] submitting fresh complex prediction → {out.name}", flush=True)
    result = predict_structure(
        fasta=cfg["complex_fasta"],
        out_dir=out,
        job_name=f"val_{key}_fresh",
        calc_extra_ptm=True,
    )
    if not result.success:
        raise RuntimeError(f"fresh prediction failed: {result.error}")
    print(f"[{key}] fresh done — {len(result.pdb_paths)} PDB(s)", flush=True)
    return out


def run_monomer(key: str, cfg: dict) -> Path:
    """Run target monomer prediction to obtain its A3M. Returns local msas/ dir."""
    JOBS_ROOT.mkdir(parents=True, exist_ok=True)
    monomer_fasta = JOBS_ROOT / f"{key}_monomer.fasta"
    write_fasta(cfg["target_seq"], cfg["target_name"], monomer_fasta)

    out = JOBS_ROOT / f"{key}_monomer"
    print(f"[{key}] submitting monomer prediction (A3M capture) → {out.name}", flush=True)
    result = predict_structure(
        fasta=monomer_fasta,
        out_dir=out,
        job_name=f"val_{key}_monomer",
        calc_extra_ptm=False,  # meaningless for monomers
    )
    if not result.success:
        raise RuntimeError(f"monomer prediction failed: {result.error}")
    msa_dir = out / "msas"
    print(
        f"[{key}] monomer done — {len(list(msa_dir.glob('*.a3m')))} A3M(s) in msas/",
        flush=True,
    )
    return msa_dir


def build_and_run_cached(key: str, cfg: dict, monomer_msa_dir: Path) -> Path:
    a3ms = list(monomer_msa_dir.glob("*.a3m"))
    if not a3ms:
        raise FileNotFoundError(f"No A3M files in {monomer_msa_dir}")
    target_a3m = a3ms[0]

    paired_dir = JOBS_ROOT / f"{key}_paired_a3m"
    paired_dir.mkdir(parents=True, exist_ok=True)
    build_paired_a3m(
        peptide_seq=cfg["peptide_seq"],
        peptide_id=cfg["peptide_id"],
        target_a3m=target_a3m,
        out_dir=paired_dir,
        complex_name=f"{cfg['target_name']}_{cfg['peptide_id']}",
    )

    out = JOBS_ROOT / f"{key}_cached"
    print(f"[{key}] submitting cached prediction → {out.name}", flush=True)
    result = predict_structure(
        fasta=cfg["complex_fasta"],
        out_dir=out,
        job_name=f"val_{key}_cached",
        calc_extra_ptm=True,
        pre_msa_dir=paired_dir,
    )
    if not result.success:
        raise RuntimeError(f"cached prediction failed: {result.error}")
    print(f"[{key}] cached done — {len(result.pdb_paths)} PDB(s)", flush=True)
    return out


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

def _top_model(out_dir: Path) -> tuple[Path | None, ExtractedMetrics | None]:
    try:
        metrics = extract_colabfold(out_dir)
    except FileNotFoundError:
        return None, None
    if not metrics:
        return None, None
    top = metrics[0]
    return top.pdb_path, top


def compare_runs(key: str, fresh_dir: Path, cached_dir: Path) -> dict:
    fresh_pdb, fresh_m = _top_model(fresh_dir)
    cached_pdb, cached_m = _top_model(cached_dir)

    if not fresh_pdb or not cached_pdb:
        return {"error": "missing PDB or score JSON"}

    # Per-chain structural comparison
    chain_results: dict[str, ComparisonResult] = {}
    for chain_id in ("A", "B"):
        try:
            chain_results[chain_id] = compare(
                pred_pdb=fresh_pdb,
                ref_pdb=cached_pdb,
                pred_chain=chain_id,
                ref_chain=chain_id,
            )
        except Exception as e:
            chain_results[chain_id] = None  # type: ignore[assignment]
            print(f"  [{key}] chain {chain_id} comparison failed: {e}", flush=True)

    def _delta(attr: str) -> float | None:
        a = getattr(fresh_m, attr, None)
        b = getattr(cached_m, attr, None)
        return (b - a) if a is not None and b is not None else None

    return {
        "chain_cmp": chain_results,
        "fresh_iptm": fresh_m.iptm,
        "cached_iptm": cached_m.iptm,
        "delta_iptm": _delta("iptm"),
        "fresh_actifptm": fresh_m.actifptm,
        "cached_actifptm": cached_m.actifptm,
        "delta_actifptm": _delta("actifptm"),
        "fresh_mean_plddt": fresh_m.mean_plddt,
        "cached_mean_plddt": cached_m.mean_plddt,
        "delta_mean_plddt": _delta("mean_plddt"),
        "fresh_chain_plddt": fresh_m.per_chain_plddt,
        "cached_chain_plddt": cached_m.per_chain_plddt,
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def _fmt(v: float | None, fmt: str = ".4f") -> str:
    return f"{v:{fmt}}" if v is not None else "  n/a "


def print_summary(results: dict[str, dict]) -> None:
    print("\n" + "=" * 72)
    print("MSA CACHING VALIDATION — RESULTS")
    print("=" * 72)

    for key, r in results.items():
        print(f"\n  {key.upper()}")
        if "error" in r:
            print(f"    ERROR: {r['error']}")
            continue

        # Structural comparison per chain
        for chain_id, cmp in r["chain_cmp"].items():
            if cmp is None:
                print(f"    chain {chain_id}: comparison failed")
                continue
            label = "target protein" if chain_id == "A" else "peptide      "
            print(
                f"    chain {chain_id} ({label})  "
                f"TM={cmp.tmscore_pred:.4f}  "
                f"RMSD={cmp.rmsd_ca:.3f} Å  "
                f"n_fresh={cmp.n_pred}  n_cached={cmp.n_ref}"
            )

        # Metric deltas (cached − fresh)
        print(
            f"    ipTM        fresh={_fmt(r['fresh_iptm'])}  "
            f"cached={_fmt(r['cached_iptm'])}  "
            f"Δ={_fmt(r['delta_iptm'], '+.4f')}"
        )
        print(
            f"    actifpTM    fresh={_fmt(r['fresh_actifptm'])}  "
            f"cached={_fmt(r['cached_actifptm'])}  "
            f"Δ={_fmt(r['delta_actifptm'], '+.4f')}"
        )
        print(
            f"    mean pLDDT  fresh={_fmt(r['fresh_mean_plddt'], '.2f')}  "
            f"cached={_fmt(r['cached_mean_plddt'], '.2f')}  "
            f"Δ={_fmt(r['delta_mean_plddt'], '+.2f')}"
        )

        # Per-chain pLDDT (peptide confidence is the key check)
        fc = r["fresh_chain_plddt"]
        cc = r["cached_chain_plddt"]
        if fc or cc:
            f_str = "  ".join(f"{k}:{v:.1f}" for k, v in sorted(fc.items()))
            c_str = "  ".join(f"{k}:{v:.1f}" for k, v in sorted(cc.items()))
            print(f"    per-chain pLDDT  fresh: {f_str}")
            print(f"    per-chain pLDDT  cached:{c_str}")

    print("\n" + "=" * 72)
    print("Thresholds for 'caching is faithful':")
    print("  TM-score ≥ 0.95 on both chains")
    print("  Cα RMSD < 1.5 Å on target (chain A), < 2.0 Å on peptide (chain B)")
    print("  |ΔipTM| < 0.02,  |Δactifpn| < 0.02")
    print("=" * 72)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    ok, msg = check_backend("colabfold")
    if not ok:
        print(f"DCC connectivity check failed: {msg}", file=sys.stderr)
        sys.exit(1)
    print(f"DCC: {msg}\n")

    JOBS_ROOT.mkdir(parents=True, exist_ok=True)

    # Step 1 — fresh complex + target monomer predictions run in parallel
    print("Step 1: submitting 4 SLURM jobs in parallel (2 fresh complex + 2 monomers)...")
    monomer_msa_dirs: dict[str, Path] = {}
    fresh_dirs: dict[str, Path] = {}

    futures: dict = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for key, cfg in COMPLEXES.items():
            futures[pool.submit(run_fresh, key, cfg)] = ("fresh", key)
            futures[pool.submit(run_monomer, key, cfg)] = ("monomer", key)

        for fut in as_completed(futures):
            kind, key = futures[fut]
            try:
                path = fut.result()
                if kind == "fresh":
                    fresh_dirs[key] = path
                else:
                    monomer_msa_dirs[key] = path
            except Exception as e:
                print(f"FATAL [{key}] {kind}: {e}", file=sys.stderr)
                sys.exit(1)

    # Step 2 — build paired A3Ms + run cached predictions in parallel
    print("\nStep 2: building paired A3Ms and submitting 2 cached predictions...")
    cached_dirs: dict[str, Path] = {}

    cached_futures: dict = {}
    with ThreadPoolExecutor(max_workers=2) as pool:
        for key, cfg in COMPLEXES.items():
            cached_futures[pool.submit(
                build_and_run_cached, key, cfg, monomer_msa_dirs[key]
            )] = key

        for fut in as_completed(cached_futures):
            key = cached_futures[fut]
            try:
                cached_dirs[key] = fut.result()
            except Exception as e:
                print(f"FATAL [{key}] cached: {e}", file=sys.stderr)
                sys.exit(1)

    # Step 3 — compare
    print("\nStep 3: comparing predictions...")
    results = {key: compare_runs(key, fresh_dirs[key], cached_dirs[key]) for key in COMPLEXES}
    print_summary(results)


if __name__ == "__main__":
    main()
