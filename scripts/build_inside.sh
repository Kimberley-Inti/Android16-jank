#!/bin/bash
set -e
set -o pipefail

export PATH="/lustre/liuqinruo/bin:$PATH"
export TMPDIR=/lustre/liuqinruo/android/tmp/build
mkdir -p "$TMPDIR"

unset OUT_DIR OUT_DIR_COMMON_BASE
unset JAVA_HOME ANDROID_JAVA_HOME
unset USE_CCACHE CCACHE_EXEC

cd /lustre/liuqinruo/android/lineage23

echo "容器 CPU 范围："
grep Cpus_allowed_list /proc/self/status

source build/envsetup.sh
breakfast salami

echo "开始完整编译：$(date)"
mka -j16 bacon

echo "检查安装包和刷机镜像："
ls -lh out/target/product/salami/lineage-*.zip

for image in boot dtbo init_boot vbmeta vendor_boot recovery; do
    test -s "out/target/product/salami/${image}.img"
    ls -lh "out/target/product/salami/${image}.img"
done

echo "BASELINE_BUILD_SUCCESS"
echo "结束时间：$(date)"
