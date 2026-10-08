#!/bin/bash
set -e
set -o pipefail

export PATH="/lustre/liuqinruo/bin:$PATH"
export TMPDIR=/lustre/liuqinruo/android/tmp/build
mkdir -p "$TMPDIR"

unset OUT_DIR OUT_DIR_COMMON_BASE
unset JAVA_HOME ANDROID_JAVA_HOME
unset USE_CCACHE CCACHE_EXEC

SRC=/lustre/liuqinruo/android/lineage23
WORK=/lustre/liuqinruo/android
cd "$SRC"

grep -q MUST_JANK_PROBE_V1_BEGIN \
  frameworks/base/core/java/android/view/Choreographer.java

source build/envsetup.sh
breakfast salami

echo "开始探针版增量编译：$(date)"
mka -j16 bacon

OUT="$SRC/out/target/product/salami"
VERSION=$(get_build_var LINEAGE_VERSION)
ZIP="$OUT/lineage-${VERSION}.zip"
test -s "$ZIP"

DEST="$WORK/artifacts/jank-v1-$1"
mkdir -p "$DEST"

cp -p "$ZIP" "$DEST/"

for image in boot dtbo init_boot vbmeta vendor_boot recovery; do
    test -s "$OUT/${image}.img"
    cp -p "$OUT/${image}.img" "$DEST/"
done

repo manifest -r -o "$DEST/source-manifest.xml"

git -C frameworks/base rev-parse HEAD \
  > "$DEST/frameworks-base-commit.txt"

cp "$WORK/backups/jank-v1/choreographer-jank-v1.patch" "$DEST/"

(
    cd "$DEST"
    sha256sum *.zip *.img > SHA256SUMS.txt
)

echo "探针版输出目录：$DEST"
ls -lh "$DEST"
echo "JANK_BUILD_SUCCESS"
