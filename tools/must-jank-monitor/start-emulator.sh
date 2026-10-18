#!/usr/bin/env bash
# Launch the local emu64x build with its GPT system disk and matching AVB data.
set -euo pipefail

project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
product_out="${MUST_PRODUCT_OUT:-$project_root/out/target/product/emu64x}"
runtime_root="${MUST_EMULATOR_ROOT:-/tmp/jankmonitor-emulator}"
sysdir="$runtime_root/diag-sysdir"
datadir="$runtime_root/diag-sysdir-data"
avd_home="$runtime_root/avd"
sdk_root="${MUST_SDK_ROOT:-/usr/lib/android-sdk}"
emulator="$project_root/prebuilts/android-emulator/linux-x86_64/emulator"
adb="$project_root/out/host/linux-x86/bin/adb"
avbtool="$project_root/out/host/linux-x86/bin/avbtool"

cd "$project_root"
for file in system-qemu.img vendor.img ramdisk-qemu.img kernel-ranchu userdata.img encryptionkey.img advancedFeatures.ini vbmeta.img system.img; do
    if [[ ! -f "$product_out/$file" ]]; then
        echo "缺少编译产物：$product_out/$file" >&2
        exit 1
    fi
done
product_out="$(realpath "$product_out")"
if [[ ! -d "$sdk_root" ]]; then
    echo "SDK 目录不存在：$sdk_root；请设置 MUST_SDK_ROOT。" >&2
    exit 1
fi

"$adb" start-server
device_state="$("$adb" devices | awk '$1 == "emulator-5554" {print $2}')"
if [[ "$device_state" == device ]]; then
    echo "emulator-5554 已在线。"
    "$adb" -s emulator-5554 shell getprop sys.boot_completed
    exit 0
fi
if [[ -n "$device_state" ]]; then
    echo "emulator-5554 当前为 $device_state；请先关闭该实例，再重新启动。" >&2
    exit 1
fi

mkdir -p "$sysdir" "$datadir" "$avd_home/JankMonitor.avd"
# In this AVD mode the emulator opens sysdir/system.img, even with -system set.
# That name must resolve to the GPT disk containing vbmeta and super.
ln -sfn "$product_out/system-qemu.img" "$sysdir/system.img"
ln -sfn "$product_out/vendor.img" "$sysdir/vendor.img"
ln -sfn "$product_out/ramdisk-qemu.img" "$sysdir/ramdisk.img"
ln -sfn "$product_out/kernel-ranchu" "$sysdir/kernel-qemu"
ln -sfn "$product_out/userdata.img" "$sysdir/userdata.img"
cp "$product_out/advancedFeatures.ini" "$sysdir/advancedFeatures.ini"
if [[ ! -e "$sysdir/encryptionkey.img" ]]; then
    cp --sparse=always "$product_out/encryptionkey.img" "$sysdir/encryptionkey.img"
fi
if [[ ! -e "$datadir/userdata.img" ]]; then
    cp --sparse=always "$product_out/userdata.img" "$datadir/userdata.img"
fi

if [[ ! -f "$avd_home/JankMonitor.ini" ]]; then
    cat > "$avd_home/JankMonitor.ini" <<EOF
avd.ini.encoding=UTF-8
path=$avd_home/JankMonitor.avd
target=android-36
EOF
fi
if [[ ! -f "$avd_home/JankMonitor.avd/config.ini" ]]; then
    cat > "$avd_home/JankMonitor.avd/config.ini" <<EOF
AvdId=JankMonitor
abi.type=x86_64
hw.cpu.arch=x86_64
hw.cpu.ncore=4
hw.gpu.enabled=yes
hw.keyboard=yes
hw.lcd.density=480
hw.ramSize=4096
disk.dataPartition.size=10G
image.sysdir.1=$sysdir
skin.name=1080x1920
skin.path=1080x1920
tag.id=default
EOF
fi

# Recompute the digest and total VBMeta bytes for this build and its chains.
# Never reuse a digest copied from a previous build's command line.
avb_metadata="$(python3 - "$product_out" "$avbtool" <<'PY'
import pathlib
import re
import subprocess
import sys

product = pathlib.Path(sys.argv[1]).resolve()
tool = sys.argv[2]
seen = set()

def vbmeta_size(name):
    if name in seen:
        raise SystemExit('Repeated/cyclic AVB chain: ' + name)
    seen.add(name)
    info = subprocess.check_output(
        [tool, 'info_image', '--image', str(product / (name + '.img'))], text=True)
    size = 0
    for block in ('Header', 'Authentication', 'Auxiliary'):
        match = re.search(r'^' + block + r' Block:\s+(\d+) bytes', info, re.M)
        if not match:
            raise SystemExit('Missing AVB block size for ' + name)
        size += int(match.group(1))
    for child in re.findall(r'Chain Partition descriptor:\s+Partition Name:\s+(\S+)', info):
        size += vbmeta_size(child)
    return size

size = vbmeta_size('vbmeta')
digest = subprocess.check_output(
    [tool, 'calculate_vbmeta_digest', '--image', str(product / 'vbmeta.img'),
     '--hash_algorithm', 'sha256'], text=True).strip()
if not re.fullmatch(r'[0-9a-fA-F]{64}', digest):
    raise SystemExit('Unexpected AVB digest')
print(digest, size)
PY
)"
read -r vbmeta_digest vbmeta_size <<< "$avb_metadata"
boot_log="$datadir/boot-$(date +%Y%m%d-%H%M%S).log"
echo "启动 emulator-5554；日志：$boot_log"
echo "运行数据：$datadir（保留应用与设置；/tmp 清空后将重新初始化）"

# Proxy variables are removed only for this process; the host VPN stays active.
exec env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u http_proxy -u https_proxy -u all_proxy \
    ANDROID_HOME="$sdk_root" ANDROID_SDK_ROOT="$sdk_root" \
    ANDROID_AVD_HOME="$avd_home" ANDROID_PRODUCT_OUT="$sysdir" \
    ANDROID_BUILD_TOP="$project_root" \
    "$emulator" -avd JankMonitor -port 5554 -sysdir "$sysdir" \
    -kernel "$sysdir/kernel-qemu" -ramdisk "$sysdir/ramdisk.img" \
    -initdata "$sysdir/userdata.img" -encryption-key "$sysdir/encryptionkey.img" \
    -data "$datadir/userdata.img" -datadir "$datadir" -cache "$datadir/cache.img" \
    -append-userspace-opt androidboot.boot_devices=pci0000:00/0000:00:03.0 \
    -append-userspace-opt "androidboot.vbmeta.size=$vbmeta_size" \
    -append-userspace-opt androidboot.vbmeta.hash_alg=sha256 \
    -append-userspace-opt "androidboot.vbmeta.digest=$vbmeta_digest" \
    -no-snapshot -no-snapshot-load -no-snapshot-save \
    -accel on -memory 4096 -cores 4 -gpu swiftshader_indirect -show-kernel \
    > "$boot_log" 2>&1
