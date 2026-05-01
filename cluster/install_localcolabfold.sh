#!/bin/bash
# Run this ONCE on a DCC login node to install LocalColabFold.
# No sudo required — pixi envs are fully user-space.
# CUDA drivers are already on cluster GPU nodes; pixi bundles the CUDA runtime.
#
# Assumes pixi binary is already installed at /hpc/home/zq36/.pixi/bin/pixi.
# If not: curl -fsSL https://pixi.sh/install.sh | bash
#
# Usage:
#   ssh zq36@dcc-login.oit.duke.edu
#   bash install_localcolabfold.sh

set -euo pipefail

# Pixi is already installed; add it to PATH for this script session.
# This does NOT modify ~/.bashrc — see the note at the bottom.
export PATH="/hpc/home/zq36/.pixi/bin:$PATH"

# Redirect pixi's package cache to /work so it's on the same filesystem
# as the install target. Without this, pixi tries to hard-link from
# ~/.pixi/cache (/hpc/home) to /work — which fails because they're
# different mount points and hard links don't cross filesystem boundaries.
export PIXI_CACHE_DIR=/work/zq36/.pixi_cache

INSTALL_DIR=/work/zq36/localcolabfold

echo "[$(date)] pixi version: $(pixi --version)"
echo "[$(date)] Installing LocalColabFold to $INSTALL_DIR..."

mkdir -p "$INSTALL_DIR"
cd "$INSTALL_DIR"

# Pull the LocalColabFold pixi config and install the environment.
# The pixi env (~15-20 GB) lands in $INSTALL_DIR/.pixi/envs/default/
# — this is why we install to /work, not /hpc/home (25 GB quota).
pixi init --import https://raw.githubusercontent.com/YoshitakaMo/localcolabfold/main/pixi.toml
pixi install

echo "[$(date)] Verifying colabfold_search..."
.pixi/envs/default/bin/colabfold_search --help 2>&1 | head -3

echo ""
echo "Installation complete."
echo ""
echo "colabfold_search binary path (used by SLURM jobs and tools/msa.py):"
echo "  $INSTALL_DIR/.pixi/envs/default/bin/colabfold_search"
echo ""
echo "To use pixi interactively on DCC, add to ~/.bashrc:"
echo "  export PATH=\"/hpc/home/zq36/.pixi/bin:\$PATH\""
echo "  export PIXI_CACHE_DIR=/work/zq36/.pixi_cache"
echo "(SLURM jobs do NOT need this — they use hardcoded absolute paths.)"
