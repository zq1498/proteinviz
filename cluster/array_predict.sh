#!/bin/bash
# SLURM array job template for parallel ColabFold predictions across a peptide panel.
#
# Submitted by tools/predict.predict_panel() after the primary MSA search job
# completes. Each array task reads its peptide from MANIFEST, builds its paired
# A3M on the DCC node from the primary job's cached target MSA, then runs
# colabfold_batch independently.
#
# Submitted as:
#   sbatch --dependency=afterok:{primary_job_id} \
#          --array=0-{n_peptides-1} \
#          --export=ALL,PRIMARY_MSA_DIR=...,MANIFEST=...,JOBS_DIR=...,
#                   BUILD_A3M_SCRIPT=...,NUM_RECYCLE=...,NUM_MODELS=...,
#                   CALC_EXTRA_PTM=... \
#          array_predict.sh
#
# REQUIRED exports (set by predict_panel()):
#   PRIMARY_MSA_DIR    Path to primary job's msas/ dir (contains target .a3m)
#   MANIFEST           TSV on DCC: peptide_id<TAB>peptide_seq, one row per task, 0-indexed
#   JOBS_DIR           Base dir; each task writes to JOBS_DIR/{peptide_id}/output/
#   BUILD_A3M_SCRIPT   Path to build_a3m.py on DCC (uploaded by predict_panel)
#
# OPTIONAL (same defaults as predict.sh):
#   NUM_RECYCLE (3), NUM_MODELS (5), NUM_SEEDS (1), USE_AMBER (0),
#   MODEL_TYPE (auto), CALC_EXTRA_PTM (1)

#SBATCH --job-name=cf_array
#SBATCH --partition=scavenger-gpu
#SBATCH --gres=gpu:1
#SBATCH --mem=64G
#SBATCH --cpus-per-task=8
#SBATCH --time=12:00:00
#SBATCH --output=/work/zq36/colabfold/logs/array_%A_%a.log

set -e

# =============================================================================
# 1. Read this task's peptide from the manifest (0-indexed by SLURM_ARRAY_TASK_ID)
# =============================================================================
TASK_LINE=$(( SLURM_ARRAY_TASK_ID + 1 ))
PEPTIDE_ID=$(awk -v n="$TASK_LINE" 'NR==n{print $1}' "$MANIFEST")
PEPTIDE_SEQ=$(awk -v n="$TASK_LINE" 'NR==n{print $2}' "$MANIFEST")

if [ -z "$PEPTIDE_ID" ] || [ -z "$PEPTIDE_SEQ" ]; then
  echo "ERROR: Could not read peptide for task ${SLURM_ARRAY_TASK_ID} from ${MANIFEST}"
  exit 1
fi

echo "===================================================="
echo "ColabFold array prediction"
echo "Array job:     ${SLURM_ARRAY_JOB_ID}_${SLURM_ARRAY_TASK_ID}"
echo "Started:       $(date)"
echo "Hostname:      $(hostname)"
echo "GPU:           $(nvidia-smi --query-gpu=name,memory.total --format=csv,noheader 2>/dev/null || echo 'no GPU detected')"
echo ""
echo "Peptide ID:      $PEPTIDE_ID"
echo "Peptide seq:     $PEPTIDE_SEQ"
echo "Primary MSAs:    $PRIMARY_MSA_DIR"
echo "N target copies: ${N_TARGET_COPIES:-1}"
echo "===================================================="

# =============================================================================
# 2. Locate the target A3M produced by the primary job
# =============================================================================
TARGET_A3M=$(ls "${PRIMARY_MSA_DIR}"/*.a3m 2>/dev/null | head -1)
if [ -z "$TARGET_A3M" ]; then
  echo "ERROR: No .a3m found in PRIMARY_MSA_DIR=${PRIMARY_MSA_DIR}"
  exit 1
fi
echo "Target A3M:    $TARGET_A3M"

# =============================================================================
# 3. Build paired A3M for this peptide on the DCC node (no container needed)
# =============================================================================
OUTDIR="${JOBS_DIR}/${PEPTIDE_ID}/output"
MSA_DIR="${OUTDIR}/msas"
mkdir -p "$MSA_DIR"

echo ""
echo "Building paired A3M for ${PEPTIDE_ID}..."
python3 "$BUILD_A3M_SCRIPT" \
  "$TARGET_A3M" "$PEPTIDE_SEQ" "$PEPTIDE_ID" "$MSA_DIR" \
  "" "${N_TARGET_COPIES:-1}"

# =============================================================================
# 4. Prediction parameters (mirror predict.sh defaults)
# =============================================================================
CF_VERSION="1.6.0-cuda12"
SIF_FILE="/work/zq36/colabfold/sif/colabfold_${CF_VERSION}.sif"
WEIGHTS_DIR="/work/zq36/colabfold/weights"

NUM_RECYCLE="${NUM_RECYCLE:-3}"
NUM_MODELS="${NUM_MODELS:-5}"
NUM_SEEDS="${NUM_SEEDS:-1}"
USE_AMBER="${USE_AMBER:-0}"
MODEL_TYPE="${MODEL_TYPE:-auto}"
CALC_EXTRA_PTM="${CALC_EXTRA_PTM:-1}"

echo ""
echo "Parameters:"
echo "  NUM_RECYCLE:    $NUM_RECYCLE"
echo "  NUM_MODELS:     $NUM_MODELS"
echo "  NUM_SEEDS:      $NUM_SEEDS"
echo "  USE_AMBER:      $USE_AMBER"
echo "  MODEL_TYPE:     $MODEL_TYPE"
echo "  CALC_EXTRA_PTM: $CALC_EXTRA_PTM"

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

# =============================================================================
# 5. Configure container environment (mirrors predict.sh)
# =============================================================================
export APPTAINERENV_CUDA_VISIBLE_DEVICES=0
export APPTAINERENV_TF_FORCE_UNIFIED_MEMORY=1
export APPTAINERENV_XLA_PYTHON_CLIENT_MEM_FRACTION=4.0
export APPTAINERENV_MPLCONFIGDIR=/tmp/mpl_$$

# =============================================================================
# 6. NumPy<2 workaround (same as predict.sh — ColabFold 1.6.0 bundles NumPy 2.x)
# =============================================================================
NUMPY_FIX_DIR="${OUTDIR}/numpy_fix"
mkdir -p "$NUMPY_FIX_DIR"

echo ""
echo "Pinning numpy<2 in ${NUMPY_FIX_DIR}..."
singularity exec \
  -B "${NUMPY_FIX_DIR}:/numpy_fix" \
  "$SIF_FILE" \
  pip install --target=/numpy_fix --no-deps --quiet "numpy<2"

export APPTAINERENV_PYTHONPATH=/numpy_fix

# =============================================================================
# 7. Run colabfold_batch
# =============================================================================
echo ""
echo "Running colabfold_batch for ${PEPTIDE_ID}..."

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
