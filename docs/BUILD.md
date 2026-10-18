# 当前源码复现与构建

本仓库保存补丁和被修改文件，不包含完整 Android 源码或厂商提取文件。

## 基线

使用 `source/current-baseline-manifest.xml`，其中三个被修改仓库 revision 为应用补丁前的上游提交；`baseline/` 保留的是较早版本记录。不能把新的本地备份 commit 当作可从 LineageOS 官方下载的 revision。

`source/baseline-revisions.json` 记录每个补丁的上游基线和原工作区快照。若使用 Repo 自定义 manifest，需要按 Repo 的工作流把此 XML 配置为 manifest；厂商依赖仍需按对应设备树准备，manifest 不是厂商文件提取包。

## 应用补丁

以下命令在项目仓库根目录运行；`/path/to/lineage` 为 Android 源码根目录：

```bash
python3 scripts/apply_must_patches.py --android-root /path/to/lineage
python3 scripts/apply_must_patches.py --android-root /path/to/lineage --apply
```

第一条只做检查，第二条才应用。脚本要求三仓库 HEAD 与记录基线一致且工作区干净，并在所有补丁预检成功后才依次应用。不要把 `source/modified-files/` 直接整体覆盖到不匹配的版本。

## 本机编译 salami

在完成本机 Android 构建依赖、设备树和厂商依赖后：

```bash
cd /path/to/lineage
source build/envsetup.sh
lunch lineage_salami-bp4a-userdebug
m -j4 bacon
```

并发度应按可用内存调整。该命令使用已有输出时为增量构建；若需要完全干净构建，应先保留现有产物，再将 OUT_DIR 指向独立空目录并确认输出路径。本文不提供删除旧 out 的自动操作。

产物通常在 `$OUT_DIR/target/product/salami/`，默认是 `out/target/product/salami/`。不要使用 emu64x 的镜像刷 salami。

## 模拟器

目标为 `lineage_sdk_phone_x86_64-bp4a-userdebug`。要使用随仓库提供的 `start-emulator.sh`，它需要位于 Android 树的 `tools/must-jank-monitor/`，以及该树对应的 emu64x 构建产物、ADB 和 avbtool。可将本仓库的工具目录复制到 Android 树对应位置后使用；已存在工具时先保留原版本。

脚本默认 SDK 为 `/usr/lib/android-sdk`，可用 MUST_SDK_ROOT 覆盖；还支持 MUST_PRODUCT_OUT、MUST_EMULATOR_ROOT。它使用当前系统磁盘与对应 AVB 元数据，不沿用其他构建的摘要。

## 产物对应关系

本次 Release 的 ROM 是 2026-10-15 已有构建；PC 源码是 2026-10-18。发布不会自动触发重新编译。`source/firmware-build-info.json` 保存已有构建的来源信息，不能把 PC 界面更新时间当作镜像编译日期。
