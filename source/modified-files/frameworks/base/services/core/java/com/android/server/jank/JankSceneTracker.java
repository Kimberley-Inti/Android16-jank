package com.android.server.jank;

import android.content.ComponentName;
import android.content.Context;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.util.ArrayMap;
import android.util.Slog;
import android.view.MotionEvent;
import android.view.VelocityTracker;
import android.view.ViewConfiguration;
import android.view.WindowManagerPolicyConstants.PointerEventListener;

import com.android.server.wm.ActivityMetricsLaunchObserver;

import java.util.ArrayList;

/** Emits interaction intervals. Frame ownership is deliberately left to SurfaceFlinger. */
final class JankSceneTracker extends ActivityMetricsLaunchObserver
        implements PointerEventListener {
    private static final String TAG = "MUST_SCENE";
    private final Handler mHandler = new Handler(Looper.getMainLooper());
    private final int mTouchThresholdPx;
    private final ArrayMap<Long, Scene> mActive = new ArrayMap<>();
    private final ArrayMap<Long, Long> mLaunches = new ArrayMap<>();
    private String mSession;
    private String mFocusedPackage;
    private long mNextId;
    private long mTouchScene;
    private float mDownX;
    private float mDownY;
    private int mMoveCount;
    private VelocityTracker mVelocity;

    JankSceneTracker(Context context) {
        mTouchThresholdPx = Math.max(24,
                ViewConfiguration.get(context).getScaledTouchSlop() * 2);
    }

    private static final class Scene {
        final long id;
        final String name;
        final String detail;
        final long startNs;
        Scene(long id, String name, String detail, long startNs) {
            this.id = id;
            this.name = name;
            this.detail = detail;
            this.startNs = startNs;
        }
    }

    synchronized void start(String session, String focusedPackage) {
        mActive.clear();
        mLaunches.clear();
        mNextId = 0;
        mTouchScene = 0;
        mSession = session;
        mFocusedPackage = focusedPackage;
        Slog.i(TAG, "event=CONTEXT session=" + session + " ts_ns="
                + SystemClock.elapsedRealtimeNanos() + " foreground=" + safe(focusedPackage));
    }

    synchronized void stop() {
        if (mSession == null) return;
        for (Scene scene : new ArrayList<>(mActive.values())) end(scene.id);
        mSession = null;
        mLaunches.clear();
        mTouchScene = 0;
        if (mVelocity != null) { mVelocity.recycle(); mVelocity = null; }
    }

    synchronized String dump() {
        final StringBuilder active = new StringBuilder();
        for (Scene scene : mActive.values()) {
            if (active.length() > 0) active.append(',');
            active.append(scene.name);
        }
        return "active_scenes=" + (active.length() == 0 ? "UNKNOWN" : active)
                + " foreground=" + safe(mFocusedPackage);
    }

    private static String safe(String text) {
        if (text == null || text.isEmpty()) return "unknown";
        return text.replaceAll("[^A-Za-z0-9._/-]", "_");
    }

    private long begin(String name, String detail, String confidence) {
        if (mSession == null) return 0;
        final long id = ++mNextId;
        final long now = SystemClock.elapsedRealtimeNanos();
        mActive.put(id, new Scene(id, name, detail, now));
        Slog.i(TAG, "event=BEGIN session=" + mSession + " scene_id=" + id + " scene="
                + name + " ts_ns=" + now + " confidence=" + confidence + " " + detail);
        return id;
    }

    private void end(long id) {
        final Scene scene = mActive.remove(id);
        if (scene == null || mSession == null) return;
        final long now = SystemClock.elapsedRealtimeNanos();
        Slog.i(TAG, "event=END session=" + mSession + " scene_id=" + id + " scene="
                + scene.name + " ts_ns=" + now + " duration_ns="
                + (now - scene.startNs) + " " + scene.detail);
    }

    synchronized void onFocusChanged(String from, String to, int displayId) {
        if (mSession == null) return;
        if (mTouchScene != 0) {
            end(mTouchScene);
            mTouchScene = 0;
            if (mVelocity != null) { mVelocity.recycle(); mVelocity = null; }
        }
        mFocusedPackage = to;
        if (from == null || to == null || from.equals(to)) return;
        final long id = begin("APP_SWITCH", "from=" + safe(from) + " to=" + safe(to)
                + " display_id=" + displayId, "MEDIUM");
        mHandler.postDelayed(() -> { synchronized (JankSceneTracker.this) { end(id); } }, 500);
    }

    @Override public synchronized void onActivityLaunched(long launchId, ComponentName name,
            int temperature, String processName, int uid) {
        if (mSession == null || name == null) return;
        if (name.getPackageName().equals(mFocusedPackage)) return;
        final String subtype = temperature == TEMPERATURE_COLD ? "COLD_START"
                : temperature == TEMPERATURE_WARM ? "WARM_START" : "HOT_START";
        final long id = begin("APP_LAUNCH", "package=" + safe(name.getPackageName())
                + " subscene=" + subtype + " owner_uid=" + uid, "HIGH");
        mLaunches.put(launchId, id);
    }

    @Override public synchronized void onActivityLaunchFinished(long launchId,
            ComponentName name, long timestampNanos, int launchMode) {
        final Long id = mLaunches.remove(launchId);
        if (id != null) end(id);
    }

    @Override public synchronized void onActivityLaunchCancelled(long launchId) {
        final Long id = mLaunches.remove(launchId);
        if (id != null) end(id);
    }

    @Override public synchronized void onPointerEvent(MotionEvent event) {
        if (mSession == null) return;
        switch (event.getActionMasked()) {
            case MotionEvent.ACTION_DOWN:
                mDownX = event.getX(); mDownY = event.getY(); mMoveCount = 0;
                if (mVelocity != null) mVelocity.recycle();
                mVelocity = VelocityTracker.obtain();
                mVelocity.addMovement(event);
                break;
            case MotionEvent.ACTION_MOVE:
                if (mVelocity != null) mVelocity.addMovement(event);
                final float dx = event.getX() - mDownX;
                final float dy = event.getY() - mDownY;
                if (dx * dx + dy * dy > mTouchThresholdPx * mTouchThresholdPx) ++mMoveCount;
                if (mTouchScene == 0 && mMoveCount >= 2) {
                    mTouchScene = begin("SCROLL_TOUCH", "package=" + safe(mFocusedPackage)
                            + " source=pointer_motion", "MEDIUM");
                }
                break;
            case MotionEvent.ACTION_UP:
            case MotionEvent.ACTION_CANCEL:
                if (mVelocity != null) {
                    mVelocity.addMovement(event);
                    mVelocity.computeCurrentVelocity(1000);
                }
                final boolean flingGesture = event.getActionMasked() == MotionEvent.ACTION_UP
                        && mTouchScene != 0 && mVelocity != null
                        && Math.hypot(mVelocity.getXVelocity(), mVelocity.getYVelocity()) > 1200;
                end(mTouchScene);
                mTouchScene = 0;
                if (flingGesture) {
                    // Gesture velocity is evidence of a fling request, not proof the app scrolled.
                    final long id = begin("SCROLL_FLING", "package=" + safe(mFocusedPackage)
                            + " source=velocity_inference", "LOW");
                    mHandler.postDelayed(
                            () -> { synchronized (JankSceneTracker.this) { end(id); } }, 700);
                }
                if (mVelocity != null) { mVelocity.recycle(); mVelocity = null; }
                break;
            default:
                break;
        }
    }
}
