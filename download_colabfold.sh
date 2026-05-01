#!/bin/bash
#SBATCH --job-name=colabfold_db_download
#SBATCH --output=/work/zq36/colabfold_download.out
#SBATCH -p common
#SBATCH --mem=4G
#SBATCH --time=24:00:00
#SBATCH --mail-type=END,FAIL
#SBATCH --mail-user=zq36@duke.edu

set -euo pipefail

DB_DIR=/work/zq36/colabfold_db
mkdir -p "$DB_DIR" && cd "$DB_DIR"

echo "[$(date)] Downloading UniRef30..."
wget https://wwwuser.gwdg.de/~compbiol/colabfold/uniref30_2302_db.tar.gz
echo "[$(date)] Extracting UniRef30..."
tar xzf uniref30_2302_db.tar.gz && rm uniref30_2302_db.tar.gz

echo "[$(date)] Downloading ColabFold envDB..."
wget https://wwwuser.gwdg.de/~compbiol/colabfold/colabfold_envdb_202108_db.tar.gz
echo "[$(date)] Extracting envDB..."
tar xzf colabfold_envdb_202108_db.tar.gz && rm colabfold_envdb_202108_db.tar.gz

echo "[$(date)] Done. Contents of $DB_DIR:"
ls -lh "$DB_DIR"
