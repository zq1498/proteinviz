"""Structure rendering via py3Dmol (browser-based HTML).

Implemented:
    render_py3dmol       — single-structure viewer, pLDDT / chain / spectrum colouring
    render_complex_py3dmol — complex viewer that highlights a designated peptide chain
    write_html           — write render_py3dmol output to a file
    write_complex_html   — write render_complex_py3dmol output to a file
    write_html_from_metrics — pipeline integration point; writes HTML from an ExtractedMetrics

Phase 2 (PyMOL static PNG):
    render_pymol         — stub; requires PyMOL CLI
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from tools.extract import ExtractedMetrics

# Loaded from CDN; no local install needed for browser rendering.
_PY3DMOL_CDN = "https://3dmol.org/build/3Dmol-min.js"

# AlphaFold/RCSB pLDDT colour stops applied to the B-factor column.
# ColabFold stores pLDDT in the PDB B-factor field.
_PLDDT_COLORS = ["#FF7D45", "#FFDB13", "#65CBF3", "#0053D6"]  # <50 → 50-70 → 70-90 → >90

# Colour used to highlight the peptide chain in complex views.
_PEPTIDE_COLOR = "#FF2D9E"  # hot pink — visually distinct from pLDDT blues/yellows


def render_py3dmol(pdb_path: Path, color_by: str = "plddt") -> str:
    """Return a self-contained HTML string with an inline py3Dmol viewer.

    color_by:
        "plddt"    — RCSB-style B-factor gradient (orange/yellow/cyan/blue)
        "chain"    — each chain a distinct colour
        "spectrum" — N→C rainbow
    """
    pdb_data = _escape_pdb(pdb_path.read_text())

    if color_by == "plddt":
        colors_js = str(_PLDDT_COLORS).replace("'", '"')
        style_js = (
            f'{{"cartoon": {{"colorscheme": {{"prop":"b","gradient":"linear",'
            f'"min":0,"max":100,"colors":{colors_js}}}}}}}'
        )
    elif color_by == "chain":
        style_js = '{"cartoon": {"color": "chainHetatm"}}'
    else:
        style_js = '{"cartoon": {"color": "spectrum"}}'

    legend = _plddt_legend() if color_by == "plddt" else ""

    return _html_page(pdb_path.stem, pdb_data, style_js, legend)


def render_complex_py3dmol(pdb_path: Path, peptide_chain: str = "C") -> str:
    """Return self-contained HTML with a homodimer+peptide complex viewer.

    Target chains are coloured by pLDDT gradient. The peptide chain is
    rendered in a distinct colour (hot pink) with both cartoon and stick
    representation so it stands out against the target background.

    peptide_chain: PDB chain ID of the peptide (ColabFold panels use "C").
    """
    pdb_data = _escape_pdb(pdb_path.read_text())
    colors_js = str(_PLDDT_COLORS).replace("'", '"')
    target_style = (
        f'{{"cartoon": {{"colorscheme": {{"prop":"b","gradient":"linear",'
        f'"min":0,"max":100,"colors":{colors_js}}}}}}}'
    )
    peptide_style = (
        f'{{"cartoon": {{"color": "{_PEPTIDE_COLOR}"}},'
        f'"stick": {{"color": "{_PEPTIDE_COLOR}", "radius": 0.15}}}}'
    )

    script = (
        f'const viewer = $3Dmol.createViewer("viewer", {{backgroundColor: "#1a1a2e"}});\n'
        f'    viewer.addModel(`{pdb_data}`, "pdb");\n'
        f'    viewer.setStyle({{}}, {target_style});\n'
        f'    viewer.setStyle({{chain: "{peptide_chain}"}}, {peptide_style});\n'
        f'    viewer.zoomTo();\n'
        f'    viewer.render();'
    )

    legend = _plddt_legend() + (
        f'<br><span style="color:{_PEPTIDE_COLOR}">&#9632;</span> '
        f'Peptide (chain {peptide_chain})'
    )

    return _html_page_raw(pdb_path.stem, script, legend)


def write_html(pdb_path: Path, out_path: Path, color_by: str = "plddt") -> Path:
    """Write a self-contained HTML viewer to out_path. Returns the path."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_py3dmol(pdb_path, color_by=color_by))
    print(f"[visualize] wrote {out_path}", flush=True)
    return out_path


def write_complex_html(
    pdb_path: Path, out_path: Path, peptide_chain: str = "C"
) -> Path:
    """Write a complex viewer (peptide chain highlighted) to out_path."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(render_complex_py3dmol(pdb_path, peptide_chain=peptide_chain))
    print(f"[visualize] wrote {out_path}", flush=True)
    return out_path


def write_interface_surface_html(
    pdb_path: Path,
    out_path: Path,
    interface_residues: list[tuple[str, int]] | None = None,
    chains_to_show: list[str] | None = None,
    peptide_chain: str = "C",
) -> Path:
    """Write an interface surface viewer to out_path.

    Renders the protein surface with interface residues highlighted and per-residue pLDDT coloring.
    If interface_residues is None, they will be computed from the PDB structure.
    chains_to_show: optional list of chain IDs to display. Other chains will be hidden.
    peptide_chain: chain ID of the peptide/ligand for interface filtering.
    Returns the path to the written HTML file.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        render_interface_surface(
            pdb_path,
            interface_residues=interface_residues,
            chains_to_show=chains_to_show,
            peptide_chain=peptide_chain,
        )
    )
    print(f"[visualize] wrote {out_path}", flush=True)
    return out_path


def write_html_from_metrics(
    metrics: "ExtractedMetrics",
    out_dir: Path,
    peptide_chain: str | None = None,
) -> Path:
    """Write an HTML viewer for the PDB stored in an ExtractedMetrics record.

    If peptide_chain is given, uses the complex viewer (peptide highlighted).
    Otherwise falls back to the standard pLDDT viewer.
    Returns the path to the written HTML file.
    """
    out_path = out_dir / f"{metrics.model_id}.html"
    if peptide_chain is not None:
        return write_complex_html(metrics.pdb_path, out_path, peptide_chain=peptide_chain)
    return write_html(metrics.pdb_path, out_path)


def render_interface_surface(
    pdb_path: Path,
    interface_residues: list[tuple[str, int]] | None = None,
    chains_to_show: list[str] | None = None,
    peptide_chain: str = "C",
) -> str:
    """Return self-contained HTML with interface surface viewer.

    Renders: cartoon structure colored by pLDDT + gaussian surface + interface residues highlighted.
    Interface residues are displayed with increased cartoon radius and a distinct style to stand out
    from the bulk of the protein. Per-residue pLDDT is visible on all atoms.

    interface_residues: list of (chain_id, residue_number) tuples. If None, will be computed
    from the PDB structure. Interface residues are those within 5 Ångströms of any atom on
    another chain.

    chains_to_show: list of chain IDs to display. If None, all chains are shown. Useful for
    focusing on a subset (e.g., ["A", "C"] to hide the dimer partner).

    peptide_chain: chain ID of the peptide/ligand. When provided with chains_to_show,
    interface residues will be filtered to only those interacting with the peptide_chain.
    """
    from tools.analyze import analyze_structure
    import numpy as np
    from Bio.PDB import PDBParser

    pdb_data = _escape_pdb(pdb_path.read_text())
    colors_js = str(_PLDDT_COLORS).replace("'", '"')

    # Compute interface residues if not provided
    if interface_residues is None:
        # If chains_to_show is specified, only compute peptide-interaction residues
        if chains_to_show is not None and peptide_chain in chains_to_show:
            interface_residues = _get_peptide_interface_residues(pdb_path, peptide_chain)
        else:
            struct_features = analyze_structure(pdb_path)
            interface_residues = struct_features.interface_residues

    # Build selection objects for interface residues: {chain: "C", resi: [1, 2, 3, ...]}
    interface_by_chain: dict[str, list[int]] = {}
    for chain_id, res_num in interface_residues:
        interface_by_chain.setdefault(chain_id, []).append(res_num)

    # If chains_to_show is specified, filter interface residues to those chains
    if chains_to_show is not None:
        interface_by_chain = {
            chain_id: res_nums
            for chain_id, res_nums in interface_by_chain.items()
            if chain_id in chains_to_show
        }
        chains_filter = str(chains_to_show).strip("[]").replace("'", '"')
    else:
        chains_filter = None

    # Generate setStyle calls for each interface residue
    interface_styles = []
    for chain_id, res_nums in interface_by_chain.items():
        resi_json = str(res_nums)  # [38, 39, 40, ...] is valid JS array syntax
        interface_styles.append(
            f'    viewer.setStyle({{chain: "{chain_id}", resi: {resi_json}}}, '
            f'{{"cartoon": {{"thickness": 0.8, "color": "#FF6B9D"}}, '
            f'"stick": {{"colorscheme": {{"prop":"b","gradient":"linear",'
            f'"min":0,"max":100,"colors":{colors_js}}}, "radius": 0.25}}}});'
        )

    interface_calls = "\n".join(interface_styles) if interface_styles else ""

    # Target style: all residues colored by pLDDT
    target_style = (
        f'{{"cartoon": {{"colorscheme": {{"prop":"b","gradient":"linear",'
        f'"min":0,"max":100,"colors":{colors_js}}}}}}}'
    )

    # If chains_to_show is specified, hide all chains first, then show only the selected ones
    if chains_to_show is not None:
        # Build setStyle calls for only the chains we want to show
        chain_styles = [
            f'    viewer.setStyle({{chain: "{chain_id}"}}, {target_style});'
            for chain_id in chains_to_show
        ]
        chain_style_calls = "\n".join(chain_styles)
        init_hide = '    viewer.setStyle({}, {});\n'  # Hide all by default
        style_setup = f'{init_hide}{chain_style_calls}\n'

        # Get all chain IDs from the PDB to compute excluded chains
        from Bio.PDB import PDBParser
        parser = PDBParser(QUIET=True)
        bp_structure = parser.get_structure("complex", str(pdb_path))
        model = next(bp_structure.get_models())
        all_chains = [c.id for c in model.get_chains()]
        excluded_chains = [c for c in all_chains if c not in chains_to_show]

        # Add bright surface for visible chains
        visible_surface = "\n".join(
            f'    viewer.addSurface($3Dmol.SurfaceType.VDW, {{chain: "{chain_id}", opacity: 0.5, color: "#FFFFFF"}});'
            for chain_id in chains_to_show
        )

        # Add desaturated grey surface for excluded chains (context only)
        excluded_surface = (
            "\n".join(
                f'    viewer.addSurface($3Dmol.SurfaceType.VDW, {{chain: "{chain_id}", opacity: 0.15, color: "#888888"}});'
                for chain_id in excluded_chains
            )
            if excluded_chains
            else ""
        )

        surface_calls = f'{visible_surface}\n{excluded_surface}' if excluded_surface else visible_surface
    else:
        style_setup = f'    viewer.setStyle({{}}, {target_style});\n'
        surface_calls = '    viewer.addSurface($3Dmol.SurfaceType.VDW, {"opacity": 0.3, "color": "white"});'

    script = (
        f'const viewer = $3Dmol.createViewer("viewer", {{backgroundColor: "#1a1a2e"}});\n'
        f'    viewer.addModel(`{pdb_data}`, "pdb");\n'
        f'{style_setup}'
        f'{interface_calls}\n'
        f'{surface_calls}\n'
        f'    viewer.zoomTo();\n'
        f'    viewer.render();'
    )

    legend = (
        _plddt_legend()
        + f'<br><span style="color:#FF6B9D">▬▬▬</span> Interface residues<br>'
        + f'<span style="opacity:0.3; color:white">&#9679;</span> Protein surface (VDW)'
    )

    return _html_page_raw(pdb_path.stem + "_interface", script, legend)


def render_pymol(pdb_path: Path, out_png: Path, highlight_chains: list[str] | None = None) -> Path:
    """Invoke PyMOL CLI to render a publication-quality PNG. Phase 2 — not yet implemented."""
    raise NotImplementedError("render_pymol requires a PyMOL CLI install; planned for phase 2")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _escape_pdb(pdb_text: str) -> str:
    """Escape PDB text for safe embedding in a JS template literal."""
    return pdb_text.replace("\\", "\\\\").replace("`", "\\`").replace("$", "\\$")


def _get_peptide_interface_residues(
    pdb_path: Path, peptide_chain: str, interface_cutoff_a: float = 5.0
) -> list[tuple[str, int]]:
    """Return residues from all chains that interact with a specific peptide chain.

    This is used to filter interface highlighting to only peptide-protein interactions,
    excluding intra-protein (e.g., dimer) interfaces.
    """
    import numpy as np
    from Bio.PDB import PDBParser

    parser = PDBParser(QUIET=True)
    bp_structure = parser.get_structure("complex", str(pdb_path))
    model = next(bp_structure.get_models())
    chains = list(model.get_chains())

    # Get all atoms for the peptide chain
    peptide_atoms = []
    for chain in chains:
        if chain.id == peptide_chain:
            for res in chain.get_residues():
                if res.id[0] == " ":
                    for atom in res.get_atoms():
                        peptide_atoms.append(atom.get_vector().get_array())

    if not peptide_atoms:
        return []

    peptide_atoms = np.array(peptide_atoms, dtype=np.float32)

    # Find residues in other chains that are within cutoff of peptide
    interface_residues: list[tuple[str, int]] = []
    for chain in chains:
        if chain.id == peptide_chain:
            continue
        for res in chain.get_residues():
            if res.id[0] != " ":
                continue
            res_coords = np.array(
                [a.get_vector().get_array() for a in res.get_atoms()], dtype=np.float32
            )
            if len(res_coords) == 0:
                continue
            diffs = res_coords[:, None, :] - peptide_atoms[None, :, :]  # (M, N, 3)
            if np.sqrt((diffs**2).sum(axis=-1)).min() <= interface_cutoff_a:
                interface_residues.append((chain.id, res.id[1]))

    return interface_residues


def _plddt_legend() -> str:
    stops = [
        ("#0053D6", "Very high (&gt;90)"),
        ("#65CBF3", "Confident (70–90)"),
        ("#FFDB13", "Low (50–70)"),
        ("#FF7D45", "Very low (&lt;50)"),
    ]
    rows = "".join(
        f'<span style="color:{c}">&#9632;</span> {label}<br>' for c, label in stops
    )
    return f"pLDDT confidence:<br>{rows}"


def _html_page(title: str, pdb_data: str, style_js: str, legend: str) -> str:
    """Standard HTML page using setStyle on the full model."""
    script = (
        f'const viewer = $3Dmol.createViewer("viewer", {{backgroundColor: "#1a1a2e"}});\n'
        f'    viewer.addModel(`{pdb_data}`, "pdb");\n'
        f'    viewer.setStyle({{}}, {style_js});\n'
        f'    viewer.zoomTo();\n'
        f'    viewer.render();'
    )
    return _html_page_raw(title, script, legend)


def _html_page_raw(title: str, script: str, legend: str) -> str:
    """Shared HTML boilerplate; script is injected verbatim."""
    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <title>{title}</title>
  <script src="{_PY3DMOL_CDN}"></script>
  <style>
    body {{ margin: 0; background: #1a1a2e; }}
    #viewer {{ width: 100vw; height: 100vh; position: relative; }}
    #legend {{
      position: absolute; bottom: 16px; left: 16px;
      color: white; font-family: monospace; font-size: 13px; line-height: 1.8;
      background: rgba(0,0,0,0.45); padding: 8px 12px; border-radius: 6px;
    }}
  </style>
</head>
<body>
  <div id="viewer"></div>
  <div id="legend">{legend}</div>
  <script>
    {script}
  </script>
</body>
</html>"""
