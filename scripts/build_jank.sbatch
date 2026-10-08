#!/bin/bash
#SBATCH --job-name=salami_jank
#SBATCH --cpus-per-task=16
#SBATCH --mem=64G
#SBATCH --time=12:00:00
#SBATCH --output=/lustre/liuqinruo/android/logs/jank_%j.log
#SBATCH --chdir=/lustre/liuqinruo/android/lineage23

set -e
module load apptainer/1.5.3

echo "作业 ID：$SLURM_JOB_ID"
echo "开始时间：$(date)"
echo "节点：$(hostname)"
grep Cpus_allowed_list /proc/self/status

apptainer exec --cleanenv \
  --bind /lustre/liuqinruo:/lustre/liuqinruo \
  /lustre/liuqinruo/ubuntu_prepared.sif \
  bash --noprofile --norc \
  /lustre/liuqinruo/android/jobs/build_jank_inside.sh \
  "$SLURM_JOB_ID"
