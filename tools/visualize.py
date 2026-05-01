"""Structure rendering (py3Dmol inline, PyMOL CLI for static images)."""

from __future__ import annotations

from pathlib import Path


def render_py3dmol(pdb_path: Path, color_by: str = "plddt") -> str:
    """Return an HTML string with an inline py3Dmol viewer.

    color_by: "plddt" (B-factor gradient), "chain", or "spectrum".
    """
    raise NotImplementedError


def render_pymol(pdb_path: Path, out_png: Path, highlight_chains: list[str] | None = None) -> Path:
    """Invoke PyMOL CLI to render a publication-quality PNG."""
    raise NotImplementedError


def render_interface(pdb_path: Path, chain_a: str, chain_b: str, out_png: Path) -> Path:
    """Render a view highlighting the interface residues between two chains."""
    raise NotImplementedError
