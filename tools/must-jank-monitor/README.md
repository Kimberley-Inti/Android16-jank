# MUST Jank Monitor：Windows / Linux PC 界面

本发布包使用 Python 源码启动，包含 2026-10-18 的 Deadline 口径、low/medium/high 分级、独立类型排名和整页统计布局。不包含旧 EXE。

## Windows

1. 安装 Python 3.10+，勾选 Tcl/Tk 和 PATH；安装 Google Android Platform-Tools。
2. 让 adb.exe 位于 PATH，或把 platform-tools 文件夹放在本目录旁的同级内容位置（本目录/platform-tools/adb.exe）。
3. 手机刷入匹配的系统，启用 USB 调试并授权电脑。
4. 双击 `启动监测器.bat`；或者运行 `py -3 main.py`。
5. 选择设备，START；可连续切换不同 App 操作。STOP 后保存会话。

## Linux

```bash
sudo apt install python3-tk adb
python3 main.py
```

源码依赖仅为 Python 标准库与外部 ADB。启动脚本不会自动刷机。

## 界面与数据

- 监测页：设备累计 SurfaceFrame 指标，以及本地接收的事件列表。
- 类型统计页：low/medium/high 分别排名，原始组合与拆分标签分别统计。
- 仅设备明确 deadline_jank=1 的事件进入 Deadline 及分级主统计。
- 单独 BufferStuffing 为辅助标签；伴随 Deadline 的组合保留。
- `sessions/` 保存 raw.log、jank.jsonl、scenes.jsonl、correlated_jank.jsonl、stats.jsonl、session.json、severity_summary.json。
- 接回仅记录接回后事件，顶部设备累计可包含接回前数据。

完整说明见 [当前实现技术文档](docs/当前实现技术文档.md)。
