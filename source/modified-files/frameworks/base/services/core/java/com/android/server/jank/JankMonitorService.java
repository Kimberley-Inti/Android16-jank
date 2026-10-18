package com.android.server.jank;

import android.content.Context;
import android.os.Binder;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.os.Parcel;
import android.os.Process;
import android.os.ResultReceiver;
import android.os.ShellCallback;
import android.os.ShellCommand;
import android.os.ServiceManager;
import android.os.SystemClock;
import android.util.Slog;

import com.android.server.LocalServices;
import com.android.server.SystemService;
import com.android.server.wm.ActivityTaskManagerInternal;
import com.android.server.wm.ActivityAssistInfo;
import com.android.server.wm.WindowManagerService;

import java.io.FileDescriptor;
import java.io.PrintWriter;
import java.util.Locale;
import java.util.UUID;

/** Controls a continuous system-wide FrameTimeline monitoring session. */
public final class JankMonitorService extends SystemService {
    private static final String SERVICE = "must_jank";
    private static final int SF_TRANSACTION = 1050;
    private static final String SF_DESCRIPTOR = "android.ui.ISurfaceComposer";
    private static volatile JankMonitorService sInstance;

    private enum State { STOPPED, STARTING, RUNNING, STOPPING }

    private final Handler mHandler = new Handler(Looper.getMainLooper());
    private final JankSceneTracker mScenes;
    private final IBinder mBinder = new MonitorBinder();
    private final Runnable mPeriodicStats = new Runnable() {
        @Override public void run() {
            if (mState != State.RUNNING) return;
            try {
                Slog.i("MUST_JANK_STATS", "session=" + mSession + " ts_ns="
                        + SystemClock.elapsedRealtimeNanos() + " " + surfaceFlinger(2, null));
            } catch (RuntimeException e) {
                Slog.w("MUST_JANK_STATS", "session=" + mSession + " error=sf_unavailable");
            }
            mHandler.postDelayed(this, 5000);
        }
    };

    private volatile State mState = State.STOPPED;
    private String mSession = "";
    private long mStartNs;
    private String mLastStats = "total_frames=0 janky_frames=0";
    private WindowManagerService mWindowManager;
    private ActivityTaskManagerInternal mAtm;

    public JankMonitorService(Context context) {
        super(context);
        mScenes = new JankSceneTracker(context);
        sInstance = this;
    }

    @Override public void onStart() {
        publishBinderService(SERVICE, mBinder);
    }

    /** Called once after WindowManager is initialized by SystemServer. */
    public void attachWindowManager(WindowManagerService windowManager) {
        mWindowManager = windowManager;
        mAtm = LocalServices.getService(ActivityTaskManagerInternal.class);
    }

    /** Posted from WindowManager only when the focused task changes. */
    public static void onFocusedAppChanged(String from, String to, int displayId) {
        final JankMonitorService service = sInstance;
        if (service != null && service.mState == State.RUNNING) {
            service.mScenes.onFocusChanged(from, to, displayId);
        }
    }

    private static String surfaceFlinger(int operation, String session) {
        final IBinder binder = ServiceManager.getService("SurfaceFlinger");
        if (binder == null) throw new IllegalStateException("SurfaceFlinger unavailable");
        final Parcel request = Parcel.obtain();
        final Parcel reply = Parcel.obtain();
        try {
            request.writeInterfaceToken(SF_DESCRIPTOR);
            request.writeInt(operation);
            if (operation == 1) request.writeString(session);
            if (!binder.transact(SF_TRANSACTION, request, reply, 0)) {
                throw new IllegalStateException("SurfaceFlinger rejected monitor transaction");
            }
            return reply.readString();
        } catch (android.os.RemoteException e) {
            throw new IllegalStateException(e);
        } finally {
            request.recycle();
            reply.recycle();
        }
    }

    private synchronized String start() {
        if (mState != State.STOPPED) return status();
        if (mWindowManager == null || mAtm == null) return "error=services_not_ready";
        mState = State.STARTING;
        final String session = Long.toString(SystemClock.elapsedRealtimeNanos(), 36) + "_"
                + UUID.randomUUID().toString().substring(0, 8);
        try {
            mStartNs = SystemClock.elapsedRealtimeNanos();
            surfaceFlinger(1, session);
            mSession = session;
            final java.util.List<ActivityAssistInfo> visible = mAtm.getTopVisibleActivities(0);
            final String focused = visible.isEmpty() ? null
                    : visible.get(0).getComponentName().getPackageName();
            mScenes.start(session, focused);
            mWindowManager.registerPointerEventListener(mScenes, 0);
            mAtm.getLaunchObserverRegistry().registerLaunchObserver(mScenes);
            mState = State.RUNNING;
            Slog.i("MUST_JANK_SESSION", "event=START session=" + session + " ts_ns="
                    + mStartNs + " state=RUNNING");
            mHandler.postDelayed(mPeriodicStats, 5000);
            return status();
        } catch (RuntimeException e) {
            try { mWindowManager.unregisterPointerEventListener(mScenes, 0); }
            catch (RuntimeException ignored) { }
            try { mAtm.getLaunchObserverRegistry().unregisterLaunchObserver(mScenes); }
            catch (RuntimeException ignored) { }
            try { surfaceFlinger(0, null); }
            catch (RuntimeException ignored) { }
            mScenes.stop();
            mState = State.STOPPED;
            return "error=" + e.getClass().getSimpleName() + " message=" + e.getMessage();
        }
    }

    private synchronized String stop() {
        if (mState == State.STOPPED) return status();
        if (mState != State.RUNNING) return "error=busy state=" + mState;
        mState = State.STOPPING;
        mHandler.removeCallbacks(mPeriodicStats);
        try {
            mLastStats = surfaceFlinger(2, null);
        } catch (RuntimeException e) {
            mLastStats = "quality=PARTIAL reason=sf_stats_unavailable";
        }
        try {
            surfaceFlinger(0, null);
        } catch (RuntimeException e) {
            mState = State.RUNNING;
            mHandler.postDelayed(mPeriodicStats, 5000);
            return "error=sf_stop_failed state=RUNNING";
        }
        mWindowManager.unregisterPointerEventListener(mScenes, 0);
        mAtm.getLaunchObserverRegistry().unregisterLaunchObserver(mScenes);
        mScenes.stop();
        final long end = SystemClock.elapsedRealtimeNanos();
        Slog.i("MUST_JANK_STATS", "session=" + mSession + " ts_ns=" + end
                + " final=1 " + mLastStats);
        Slog.i("MUST_JANK_SESSION", "event=STOP session=" + mSession + " ts_ns="
                + end + " duration_ns=" + (end - mStartNs) + " state=STOPPED");
        mState = State.STOPPED;
        return "session=" + mSession + " state=STOPPED " + mLastStats;
    }

    private synchronized String status() {
        return "session=" + (mSession.isEmpty() ? "none" : mSession) + " state=" + mState;
    }

    private synchronized String stats() {
        if (mState == State.RUNNING) {
            try { return surfaceFlinger(2, null); }
            catch (RuntimeException e) { return "quality=PARTIAL reason=sf_stats_unavailable"; }
        }
        return mLastStats;
    }

    private final class MonitorBinder extends Binder {
        private void checkCaller() {
            final int uid = Binder.getCallingUid();
            if (uid != Process.SHELL_UID && uid != Process.ROOT_UID
                    && uid != Process.SYSTEM_UID) {
                throw new SecurityException("must_jank is restricted to shell/system");
            }
        }

        @Override protected void dump(FileDescriptor fd, PrintWriter writer, String[] args) {
            checkCaller();
            writer.println("MUST Jank Monitor");
            writer.println(status());
            writer.println("elapsed_ns=" + (mState == State.RUNNING
                    ? SystemClock.elapsedRealtimeNanos() - mStartNs : 0));
            writer.println(stats());
            writer.println(mScenes.dump());
            writer.println("jank_by_scene=PC_CORRELATION_REQUIRED");
        }

        @Override public void onShellCommand(FileDescriptor in, FileDescriptor out,
                FileDescriptor err, String[] args, ShellCallback callback,
                ResultReceiver resultReceiver) {
            checkCaller();
            new ShellCommand() {
                @Override public int onCommand(String cmd) {
                    final PrintWriter writer = getOutPrintWriter();
                    switch (cmd == null ? "" : cmd.toLowerCase(Locale.ROOT)) {
                        case "start": {
                            final String result = start();
                            writer.println(result);
                            return result.startsWith("error=") ? 1 : 0;
                        }
                        case "stop": {
                            final String result = stop();
                            writer.println(result);
                            return result.startsWith("error=") ? 1 : 0;
                        }
                        case "status": writer.println(status()); return 0;
                        case "stats": writer.println(stats()); return 0;
                        default: return handleDefaultCommands(cmd);
                    }
                }
                @Override public void onHelp() {
                    getOutPrintWriter().println("must_jank start|stop|status|stats");
                }
            }.exec(this, in, out, err, args, callback, resultReceiver);
        }
    }
}
