# salami 显示卡死修复（2026-10-15）

## 现场与原因

USB 设备 `<device-serial>` 在淘宝页面切换、返回桌面期间卡死。卡死前和采集现场，
`cmd must_jank status` 均为 `session=none state=STOPPED`。

15:33:08 的 SurfaceFlinger 原生堆栈及本地源码显示以下循环等待：

1. SurfaceFlinger 主线程在 `commit()` 内持有 `mStateLock`。
2. `finalizeDisplayModeChange()` 切换显示节拍源，经
   `Scheduler::demotePacesetterDisplay()` 停止旧显示的 idle timer，等待其线程退出。
3. `IdleTimer` 在 `applyPolicy()` 中调用 `SurfaceFlinger::requestDisplayModes()`，
   同步等待 `mStateLock`，无法退出。
4. SurfaceFlinger 的创建窗口请求被阻塞，WindowManager 持有全局锁等待请求返回，
   导致界面、显示、动画线程一起阻塞。15:33:46 Watchdog 杀掉了 `system_server`。

原始证据在本机 `/tmp/salami-freeze-live-20261015-1531/`。该目录在临时文件系统中，
需要长期留存时应另行备份。

## 源码修复

文件：`frameworks/native/services/surfaceflinger/SurfaceFlinger.cpp`。

`requestDisplayModes()` 对非主线程请求采用 Scheduler 消息队列投递，立即返回，
不等待 `mStateLock`。队列任务在主线程获取 `mStateLock` 后处理原始请求。
原有主线程调用路径继续在调用者持锁时同步处理。

因此 IdleTimer 能完成回调并退出，主线程的 join 能返回，之后可以执行已排队的请求。
自动刷新率选择仍然保留，未修改 FrameTimeline 的 Jank 判定。

Git HEAD 中原有的 `requestDisplayModes()` 就包含非主线程同步获取 `mStateLock` 的路径；
本次修复替换这一路径。监测器原有改动没有新增这次发现的同步取锁行为。

新增回归用例位于 `tests/unittests/SurfaceFlinger_DisplayModeSwitching.cpp`：

- `timerModeRequestDoesNotWaitForMainThreadStateLock`：主线程持锁时，模拟 timer 的
  工作线程仍须返回，请求排队后由主线程执行。
- `mainThreadModeRequestDoesNotRelockStateLock`：主线程已持锁时不重复取锁、也不排队。

## 编译与验证

在源码根目录执行：

```bash
source build/envsetup.sh
lunch lineage_salami-bp4a-userdebug
m -j4 bacon libsurfaceflinger_unittest
```

构建日志：`/tmp/salami-display-deadlock-fix-20261015-build.log`。
标准系统安装包输出：`out/target/product/salami/lineage-23.2-20261015-UNOFFICIAL-salami.zip`。
同一天旧包的文件名可能相同，必须以新文件的 SHA256 和构建完成时间辨认。

对应的设备测试二进制在 `out/target/product/salami/data/nativetest64/libsurfaceflinger_unittest/`。
测试只覆盖本次线程/锁行为；新系统的显示驱动及长时间使用仍需刷入后实测。

刷入修复镜像后，应分别在监测器停止、运行两种情况下，重复验证：

1. 淘宝登录页面进退、返回桌面、多任务切换和输入法显示/隐藏。
2. 浏览器滚动、抖音连续播放及 App 之间切换。
3. 60/120 Hz 自动切换、熄屏再亮屏。
4. 保存电脑端 logcat，检查是否再次出现同一 SF 线程循环等待及 WMS Watchdog。

测试辅助类另同步了 `acquireVirtualDisplay()` 的第五个参数，
以适配当前源码已有的接口；此调整仅影响测试程序，不进入系统镜像。

## Windows 监测脚本

本次修复不改变 `must_jank` 命令、Binder 采集接口、JSONL 数据格式、场景分类和输出路径。
现有 `main.py`、`monitor_core.py` 和 `启动监测器.bat` 可以继续使用，不需要因本次修复修改。

监测器停止时复现说明本次死锁不依赖启动采集；这不等同于证明所有监测器源码改动均无影响。

## 本次验证结果

- 修复版 SurfaceFlinger 成功编译并进入新系统安装包，安装包 ZIP CRC 检查通过。
- `libsurfaceflinger_unittest` 构建成功。
- 在 USB 连接的 PHB110 上执行 8 个定向用例，全部通过（两个新增用例和两个已有刷新率切换用例，各在 modeset 状态机开/关两种配置执行）。
- 测试程序使用模拟图形组件，证明本次锁/消息队列行为及既有刷新率切换用例通过；没有替换手机当前的系统 SurfaceFlinger。
- 新包构建编号 `1792050526`；检查时手机旧系统构建编号 `1792042048`。实际长时间使用验证需刷入新包后完成。

交付目录：`artifacts/salami-display-deadlock-fix-20261015/`。
