#!/bin/bash
# Reference copy of the ColabFold prediction script deployed on DCC at:
#   /work/zq36/colabfold/install_scripts/04_run_prediction.sh
#
# tools/predict.py submits this via:
#   sbatch --export=ALL,FASTA=...,OUTDIR=...,NUM_RECYCLE=...,NUM_MODELS=... <DCC_PREDICT_SCRIPT>
#
# To push updates to the live DCC copy:
#   scp cluster/predict.sh zq36@dcc-login.oit.duke.edu:/work/zq36/colabfold/install_scripts/04_run_prediction.sh

#SBATCH --job-name=cf_predict
#SBATCH --partition=scavenger-gpu
#SBATCH --gres=gpu:1
#SBATCH --mem=64G
#SBATCH --cpus-per-task=8
#SBATCH --time=12:00:00
#SBATCH --output=/work/zq36/colabfold/logs/predict_%j.log

# =============================================================================
# Stage 5: Run ColabFold prediction on a FASTA input
# =============================================================================
# Performs MSA generation (using local UniRef30 + ColabFoldDB) followed by
# AlphaFold2 structure prediction on GPU. Supports both monomer and multimer
# modes via the FASTA format itself (single sequence = monomer, sequences
# joined with ':' separator = multimer).
#
# USAGE:
#   sbatch --export=ALL,FASTA=path/to/input.fasta,OUTDIR=path/to/output \
#       04_run_prediction.sh
#
# OPTIONAL OVERRIDES (export before sbatch or pass via --export):
#   NUM_RECYCLE     (default: 3)         AF2 recycling iterations
#   NUM_MODELS      (default: 5)         How many of 5 AF2 models to run
#   NUM_SEEDS       (default: 1)         Random seeds per model (multiplies output)
#   USE_AMBER       (default: 0)         Set to 1 for amber relaxation (slow)
#   MSA_MODE        (default: mmseqs2_uniref_env)  See colabfold_batch --help
#   MODEL_TYPE      (default: auto)      auto/alphafold2_multimer_v3/alphafold2_ptm
#   CALC_EXTRA_PTM  (default: 1)         Set to 1 to compute actifpTM and other
#                                        extended pTM metrics (ipTM alone is not
#                                        reliable for short peptide-protein cases)
#
# EXAMPLES:
#   # Single chain, defaults:
#   sbatch --export=ALL,FASTA=jobs/myprotein.fasta,OUTDIR=jobs/myprotein_out \
#       04_run_prediction.sh
#
#   # Multimer with extra recycling:
#   sbatch --export=ALL,FASTA=jobs/binder_vegf.fasta,OUTDIR=jobs/binder_vegf_out,NUM_RECYCLE=6 \
#       04_run_prediction.sh
#
# FASTA FORMAT:
#   Monomer:   >seq_name
#              MKLLVLGLPGAGKGTQAQ...
#
#   Multimer:  >complex_name
#              MKLLVLGLPGAGKGTQAQ...:ACDEFGHIKLMNPQRSTVWY
#              (chains separated by ':' on a single line)
# =============================================================================

set -e

CF_VERSION="1.6.0-cuda12"
SIF_FILE="/work/zq36/colabfold/sif/colabfold_${CF_VERSION}.sif"
WEIGHTS_DIR="/work/zq36/colabfold/weights"
DB_DIR="/work/zq36/colabfold/databases"

# --- Validate inputs --------------------------------------------------------
if [ -z "${FASTA:-}" ] || [ -z "${OUTDIR:-}" ]; then
  echo "ERROR: Must provide FASTA and OUTDIR via --export"
  echo "Example: sbatch --export=ALL,FASTA=jobs/x.fasta,OUTDIR=jobs/x_out 04_run_prediction.sh"
  exit 1
fi

if [ ! -f "$FASTA" ]; then
  echo "ERROR: FASTA file not found: $FASTA"
  exit 1
fi

# Make paths absolute (singularity bind mounts need absolute paths)
FASTA=$(readlink -f "$FASTA")
mkdir -p "$OUTDIR"
OUTDIR=$(readlink -f "$OUTDIR")

# --- Parameter defaults -----------------------------------------------------
NUM_RECYCLE="${NUM_RECYCLE:-3}"
NUM_MODELS="${NUM_MODELS:-5}"
NUM_SEEDS="${NUM_SEEDS:-1}"
USE_AMBER="${USE_AMBER:-0}"
MSA_MODE="${MSA_MODE:-mmseqs2_uniref_env}"
MODEL_TYPE="${MODEL_TYPE:-auto}"
CALC_EXTRA_PTM="${CALC_EXTRA_PTM:-1}"
SEARCH_ONLY="${SEARCH_ONLY:-0}"

# --- Banner -----------------------------------------------------------------
echo "===================================================="
echo "ColabFold prediction"
echo "Job ID:        ${SLURM_JOB_ID:-N/A}"
echo "Started:       $(date)"
echo "Hostname:      $(hostname)"
echo "GPU:           $(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null || echo 'no GPU detected')"
echo ""
echo "FASTA:         $FASTA"
echo "Output dir:    $OUTDIR"
echo ""
echo "Parameters:"
echo "  NUM_RECYCLE:    $NUM_RECYCLE"
echo "  NUM_MODELS:     $NUM_MODELS"
echo "  NUM_SEEDS:      $NUM_SEEDS"
echo "  USE_AMBER:      $USE_AMBER"
echo "  MSA_MODE:       $MSA_MODE"
echo "  MODEL_TYPE:     $MODEL_TYPE"
echo "  CALC_EXTRA_PTM: $CALC_EXTRA_PTM"
echo "===================================================="

# --- Build colabfold_batch flags --------------------------------------------
# colabfold_batch flags:
#   --model-type     auto picks multimer if FASTA has ':', else ptm
#   --num-recycle    More recycling = better for hard targets, slower
#   --num-models     1-5; reduce for quick screens
#   --num-seeds      Different random init per model; multiplies output structures
#   --amber          OpenMM relaxation; usually unnecessary for screening
#   --calc-extra-ptm Compute actifpTM and extended pTM metrics; essential for
#                    peptide-protein binding triage (ipTM alone is unreliable)
#   --data           Path to model weights (inside container)
# Note: --msa-mode is omitted here because the input is a pre-built MSA
# directory from colabfold_search, not a raw FASTA.

BATCH_FLAGS=(
  --data /weights
  --model-type "$MODEL_TYPE"
  --num-recycle "$NUM_RECYCLE"
  --num-models "$NUM_MODELS"
  --num-seeds "$NUM_SEEDS"
)

if [ "$USE_AMBER" = "1" ]; then
  BATCH_FLAGS+=(--amber --num-relax 1)
fi

if [ "$CALC_EXTRA_PTM" = "1" ]; then
  BATCH_FLAGS+=(--calc-extra-ptm)
fi

# --- Configure container env -----------------------------------------------
# Use APPTAINERENV_ prefix (not SINGULARITYENV_) for Apptainer 1.x
export APPTAINERENV_CUDA_VISIBLE_DEVICES=0
# JAX needs to know about the GPU
export APPTAINERENV_TF_FORCE_UNIFIED_MEMORY=1
export APPTAINERENV_XLA_PYTHON_CLIENT_MEM_FRACTION=4.0
# Silence matplotlib warning about /cache not being writable inside the container
export APPTAINERENV_MPLCONFIGDIR=/tmp/mpl_$$

# --- Run prediction ---------------------------------------------------------
# Two-step process inside the container:
#   1. colabfold_search runs MSA against local databases (CPU-bound)
#   2. colabfold_batch runs structure prediction (GPU-bound)

echo "Running colabfold_search to build MSAs from local databases..."
MSA_DIR="${OUTDIR}/msas"
mkdir -p "$MSA_DIR"

# Skip search if .a3m files already exist — safe to reuse since MSA output is
# deterministic given the same input + database. Saves ~1-2 h when resuming
# after a prediction-stage failure.
if ls "${MSA_DIR}"/*.a3m >/dev/null 2>&1; then
  echo "Found existing .a3m files in ${MSA_DIR} — skipping MSA search."
  ls -lh "${MSA_DIR}"/*.a3m
else
  singularity exec --nv \
    -B "${DB_DIR}:/db" \
    -B "${FASTA}:/input.fasta:ro" \
    -B "${MSA_DIR}:/msas" \
    "$SIF_FILE" \
    colabfold_search \
      --threads "$SLURM_CPUS_PER_TASK" \
      /input.fasta \
      /db \
      /msas
fi

echo ""
echo "MSA search finished: $(date)"
echo "MSAs produced:"
ls -lh "$MSA_DIR"

# Primary-job exit point for panel screens — secondary array jobs handle prediction
if [ "$SEARCH_ONLY" = "1" ]; then
  echo ""
  echo "SEARCH_ONLY=1 — exiting after MSA search. colabfold_batch skipped."
  exit 0
fi

echo ""
echo "Running colabfold_batch for structure prediction..."

# WORKAROUND: ColabFold 1.6.0 bundles NumPy 2.x, which breaks multimer
# predictions via a hard TypeError in msa_pairing.py (np.sum on a generator).
# Fix: pip-install numpy<2 into a throwaway directory and prepend it to
# PYTHONPATH so the container picks it up ahead of its own site-packages.
# Monomers are unaffected but we apply the fix unconditionally for consistency.
NUMPY_FIX_DIR="${OUTDIR}/numpy_fix"
mkdir -p "$NUMPY_FIX_DIR"

echo "Pinning numpy<2 in ${NUMPY_FIX_DIR} (NumPy 2.x multimer workaround)..."
singularity exec \
  -B "${NUMPY_FIX_DIR}:/numpy_fix" \
  "$SIF_FILE" \
  pip install --target=/numpy_fix --no-deps --quiet "numpy<2"

export APPTAINERENV_PYTHONPATH=/numpy_fix

singularity exec --nv \
  -B "${WEIGHTS_DIR}:/weights" \
  -B "${MSA_DIR}:/input" \
  -B "${OUTDIR}:/output" \
  -B "${NUMPY_FIX_DIR}:/numpy_fix" \
  "$SIF_FILE" \
  colabfold_batch \
    /input \
    /output \
    "${BATCH_FLAGS[@]}"

echo "===================================================="
echo "Prediction finished: $(date)"
echo ""
echo "Output structures:"
ls -lh "${OUTDIR}"/*.pdb 2>/dev/null | head -20
echo ""
echo "Output directory size:"
du -sh "$OUTDIR"
echo "===================================================="
