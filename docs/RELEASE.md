# salami 实验镜像与 PC 工具发布说明

发布版本：must-jank-2026.10.18（实验预发布）。ROM 构建日期 2026-10-15；PC 源码更新日期 2026-10-18。

## 下载和恢复系统 ZIP

从同一次 Release 下载：

- lineage-23.2-20261015-UNOFFICIAL-salami.zip.part.001
- lineage-23.2-20261015-UNOFFICIAL-salami.zip.part.002
- release-assets.json
- merge_rom.py
- Windows 用户还可下载 merge-rom.cmd

放到同一目录。Windows 安装 Python 3.10+ 后双击 merge-rom.cmd，或运行：

```powershell
py -3 merge_rom.py
```

Linux：

```bash
python3 merge_rom.py
```

脚本逐段检查大小和 SHA-256，合并到临时文件，校验完整 ZIP 后才生成正式文件。已存在目标时拒绝覆盖。分段文件本身不可刷写，不要改后缀后直接 sideload。

完整 ZIP SHA-256：

```text
042544561c6fbeaab3da37bbdd17c635ed6882e68728513f12adb756aa698270
```

原始大小：2,175,603,032 字节。其余配套镜像的大小与校验值见 release-assets.json / SHA256SUMS.txt。

## 配套镜像

boot.img、dtbo.img、init_boot.img、vbmeta.img、vendor_boot.img、recovery.img 来自同一个 salami 构建。该包为 UNOFFICIAL userdebug 实验构建，不是官方 nightly。

安装步骤、型号及原厂固件前提以 [LineageOS salami 官方安装指南](https://wiki.lineageos.org/devices/salami/install/) 当时的要求为准。先备份手机数据；不提供自动刷机脚本。不要混用其他设备或构建的镜像。

## PC 工具

MUST_Jank_Monitor_PC_20261018.zip 为最新 Python 源码启动包，不含旧 EXE，不含任何用户 session。需 Python/Tkinter 和 ADB；Windows 双击包内的启动监测器.bat。

## 验证范围

固件已有显示死锁修复构建记录与 mock 原生验证；PC 有既有语义及 GUI 用例记录。此次发布未重新编译，也不宣称全量真机稳定性已验证。自动调度/GC/锁/GPU 根因诊断尚未实现。

## 发布内容与源代码

Release 对应仓库 tag 包含三个 Android 补丁、上游基线 manifest、被修改文件快照和 PC 源码。SHA256SUMS.txt 覆盖实际上传的分段、镜像、工具和元数据；原始 ZIP 的校验值另在 release-assets.json 的 rom 字段中记录。
