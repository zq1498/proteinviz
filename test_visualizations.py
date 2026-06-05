#!/usr/bin/env python3
"""Test and validate py3Dmol rendering output."""

from pathlib import Path
from tools.visualize import (
    render_py3dmol,
    render_complex_py3dmol,
    render_interface_surface,
    write_html,
    write_complex_html,
    write_interface_surface_html,
)

# Select test PDB files
test_dir = Path("dcc_colabfold_example_output")
complex_pdb = test_dir / "vegf_binder_complex_unrelaxed_rank_001_alphafold2_multimer_v3_model_1_seed_000.pdb"

monomer_dir = Path("jobs/vegf_monomer_out")
monomer_pdb = monomer_dir / "vegf_monomer_unrelaxed_rank_001_alphafold2_ptm_model_5_seed_000.pdb"

output_dir = Path("test_visualizations_output")
output_dir.mkdir(exist_ok=True)

if not complex_pdb.exists():
    print(f"Error: {complex_pdb} not found")
    exit(1)

if not monomer_pdb.exists():
    print(f"Error: {monomer_pdb} not found")
    exit(1)

# Test 1: Complex with different color schemes
print("Generating complex viewer (pLDDT coloring)...")
write_html(complex_pdb, output_dir / "complex_plddt.html", color_by="plddt")

print("Generating complex viewer (chain coloring)...")
write_html(complex_pdb, output_dir / "complex_chain.html", color_by="chain")

print("Generating complex viewer (spectrum coloring)...")
write_html(complex_pdb, output_dir / "complex_spectrum.html", color_by="spectrum")

# Test 2: Complex with peptide highlighting
print("Generating complex with peptide highlight (chain C)...")
write_complex_html(complex_pdb, output_dir / "complex_peptide_highlight_C.html", peptide_chain="C")

# Test 3: Monomer with different color schemes
print("Generating monomer viewer (pLDDT coloring)...")
write_html(monomer_pdb, output_dir / "monomer_plddt.html", color_by="plddt")

print("Generating monomer viewer (chain coloring)...")
write_html(monomer_pdb, output_dir / "monomer_chain.html", color_by="chain")

print("Generating monomer viewer (spectrum coloring)...")
write_html(monomer_pdb, output_dir / "monomer_spectrum.html", color_by="spectrum")

# Test 4: Interface surface viewers with pLDDT + interface highlighting
print("Generating complex with interface surface (auto-computed interface residues)...")
write_interface_surface_html(complex_pdb, output_dir / "complex_interface_surface.html")

print("Generating complex with interface surface (single dimer chain + peptide)...")
write_interface_surface_html(
    complex_pdb,
    output_dir / "complex_interface_surface_single_chain.html",
    chains_to_show=["A", "C"],
)

print("Generating monomer with interface surface...")
write_interface_surface_html(monomer_pdb, output_dir / "monomer_interface_surface.html")

print("\n✓ All test visualizations generated!")
print(f"\nTest files written to: {output_dir.absolute()}")
print("\nGenerated files:")
for html_file in sorted(output_dir.glob("*.html")):
    file_size = html_file.stat().st_size / 1024  # KB
    print(f"  - {html_file.name:<40} ({file_size:.1f} KB)")

print("\nYou can now open these HTML files in your browser to test the visualizations.")
