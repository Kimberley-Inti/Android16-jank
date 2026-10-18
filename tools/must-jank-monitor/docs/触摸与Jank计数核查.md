# 触摸与 Jank 计数核查（2026-10-18）

## 结论

Android 源码未发现仅因轻微 MOVE 或没有页面切换就直接增加 Jank 的逻辑；此次不修改 Android 源码、不重新编译镜像。
PC 脚本存在统计口径标注问题：所有 MUST_JANK 标签事件都被称为 Jank 事件，包含非 deadline 标签。现已修复，默认列表及分布只显示 deadline_jank=1。

## 源码路径

- `frameworks/native/services/surfaceflinger/Scheduler/FrameTimeline.cpp` 的 `collectMustJank()`：
  `mMustJankyFrames` 只在 AppDeadlineMissed、SurfaceFlingerCpuDeadlineMissed、SurfaceFlingerGpuDeadlineMissed 的任意位命中时增加。
  PredictionError、BufferStuffing、SurfaceFlingerScheduling 等标签仍记录，但不自动计入 Deadline 指标。
- `SurfaceFrame::classifyJankLocked()` 使用预测/实际完成与呈现时刻判定，与是否发生页面切换无关。
- `frameworks/base/services/core/java/com/android/server/jank/JankSceneTracker.java`：
  触摸位移阈值为 max(24px, 2 × scaledTouchSlop)，至少两次超阈 MOVE 才发出 SCROLL_TOUCH。
  此分类只证明触摸位移达到条件，不能证明 App 内容实际滚动，不会直接修改帧计数。
- `frameworks/base/core/java/android/view/View.java` 的触摸 MOVE 可以更新 drawableHotspotChanged；
  页面未切换也可能因按压反馈、动画或其他图层提交新帧。若新帧被 FrameTimeline 判为 deadline miss，计数增加合理。

指标单位是 SurfaceFrame。一个显示帧中多个图层可以分别被判定，不能把此数量理解为肉眼可感知的卡顿次数或页面切换次数。
设备统计覆盖整个 session 的全部应用/图层，前台 App 只是场景上下文，并非隐含过滤条件。

## 本次已有日志快照

session: `q42q63lq6_b9369829`，本地接入后快照，不是整个设备会话。

- 标签事件：9,370
- deadline_jank=1：714
- deadline_jank=0：8,656
- 单独 BufferStuffing（jank_type=64）：8,116
- 重复 (session, event_id)：0
- deadline_jank 与源码 deadline bitmask 不一致：0

很多事件来自 Launcher、Taskbar 和 NotificationShade；不能把全局计数全归因于当时触摸的 App。
此快照没有逐次标注用户手指动作时刻，因此不能断言用户所指的每一次增加都由非 deadline 标签造成。
如果顶部「Deadline Jank 帧」增加，来源仍是设备 FrameTimeline 统计，需要按对应帧的 owner、类型和时间排查。
非 deadline 标签也可能提示队列等性能问题，不能把它们一概解释成正常或无影响。

## PC 修复

修改 `gui.py`、`monitor_core.py`，新增 `test_monitor_semantics.py`。

1. 分开统计本地 Deadline、辅助标签和判定缺失，默认按 Deadline 过滤列表及分布。
2. 顶部明确设备全会话/全图层统计；不以本地事件条数覆盖设备计数。
3. SCROLL_TOUCH / SCROLL_FLING 明确显示为触摸/抛滑请求推断。
4. 根据 start/status 命令的 session ID 绑定采集，丢弃旧 session 日志，按 (session, event_id) 去重。
5. 缺少 deadline_jank 时归入判定缺失，不凭「有任意 jank_type」认定 deadline jank。
6. 保留 `jank.jsonl` / `correlated_jank.jsonl` 中的非 deadline 原始标签。
7. `session.json` 的 total_jank 现在指本地接收到的 Deadline 事件；received_label_events 保留全部标签事件数，
   auxiliary_label_events 和 unknown_deadline_events 分别记录其他两类。janky_frames 仍是设备全会话指标。
8. 收到周期 stats 后保存 session.json，避免运行期间读取到接入时的旧值。
9. 接回会话时立即显示查询到的设备已有统计，不再等下一次周期上报才由 0 跳到历史累计值。

## 验证

8 项回归测试全部通过，包括真实 Tk 控件的过滤/计数检查；测试未调用手机 ADB。
已有 9,370 条事件用新采集器回放，与 deadline/非 deadline 字段逐条统计一致；注入一条重复事件被排除。
验证日志及冻结快照在 `/tmp/must-jank-audit-20261018/`。

## 使用更新

已打开的旧 Python 进程不会自动加载文件修改。先在旧界面停止并保存本次采集，关闭窗口，再运行：

```bash
cd "/path/to/Android16-jank/tools/must-jank-monitor"
export PATH="/path/to/lineage/out/host/linux-x86/bin:$PATH"
/usr/bin/python3 main.py
```

本次修改的是 Python 源码；现有 Windows EXE 没有重新打包，继续运行旧 EXE 不会包含本次修复。
