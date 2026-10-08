#!/bin/bash
#SBATCH --job-name=salami_a16
#SBATCH --cpus-per-task=16
#SBATCH --mem=64G
#SBATCH --time=12:00:00
#SBATCH --output=/lustre/liuqinruo/android/logs/build_%j.log
#SBATCH --chdir=/lustre/liuqinruo/android/lineage23

set -e

module load apptainer/1.5.3

echo "开始时间：$(date)"
echo "计算节点：$(hostname)"
echo "宿主机 CPU 范围："
grep Cpus_allowed_list /proc/self/status

apptainer exec --cleanenv \
  --bind /lustre/liuqinruo:/lustre/liuqinruo \
  /lustre/liuqinruo/ubuntu_prepared.sif \
  bash --noprofile --norc \
  /lustre/liuqinruo/android/jobs/build_inside.sh
