#!/bin/bash
# SLURM job: run colabfold_search on DCC to generate an MSA (.a3m) for one FASTA.
# Submitted programmatically by tools/msa.py — not meant to be sbatch'd manually.
#
# Variables injected by msa.py via sed before submission:
#   __JOB_NAME__   short identifier (e.g. 3v03_20260420)
#   __FASTA__      absolute path to input FASTA on DCC
#   __OUT_DIR__    absolute path to output directory on DCC (will contain .a3m)
#   __DB_DIR__     path to colabfold databases (default /work/zq36/colabfold_db)

#SBATCH --job-name=cf_msa___JOB_NAME__
#SBATCH --output=/work/zq36/msa_logs/cf_msa___JOB_NAME__.out
#SBATCH -p common
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --time=01:00:00
#SBATCH --mail-type=END,FAIL
#SBATCH --mail-user=zq36@duke.edu

set -euo pipefail

CF_BIN=/work/zq36/localcolabfold/.pixi/envs/default/bin/colabfold_search
DB_DIR=__DB_DIR__
FASTA=__FASTA__
OUT_DIR=__OUT_DIR__

mkdir -p "$OUT_DIR"
mkdir -p /work/zq36/msa_logs

echo "[$(date)] Starting MSA search for $FASTA"
echo "[$(date)] DB: $DB_DIR  OUT: $OUT_DIR"

"$CF_BIN" "$FASTA" "$DB_DIR" "$OUT_DIR" --threads 8

echo "[$(date)] MSA search complete. Output:"
ls -lh "$OUT_DIR"
