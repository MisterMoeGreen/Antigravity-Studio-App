# Specification 03: Native Android Notification Subsystem & Bridge Architecture

> **Project**: Antigravity Studio Standalone Android Application  
> **Release Target**: `v1.5.0` (Native Notification Subsystem & Live Progress Tracking)  
> **Package ID**: `ai.agy.app` (10-character geometry)  
> **Target Architecture**: `arm64-v8a` | **SDK**: `35` (Android 10+ / API 29 through Android 15+)  
> **Branch**: `v1.5.0`  
> **Review Status**: **ARCHITECTURALLY AUDITED & HARDENED BY GEMINI 4 ARGON (`ARGON-V15-01` THROUGH `ARGON-V15-10`)**

---

## 1. Problem Statement & Operational Context

To support both **Turn Completion Notifications** (Specification 01) and **Live Progress Notifications with Action Buttons** (Specification 02) across parallel sessions, Antigravity Studio requires a robust native subsystem in Kotlin bridging the WebView frontend, Python daemon (`server.py`), and the Android OS notification service.

Adversarial audit by Gemini 4 Argon identified 5 fundamental integration challenges:
1. **Activity Lifecycle Disconnection (`ARGON-V15-01`)**:
   `MainActivity` can be paused (`onPause`), stopped (`onStop`), or destroyed by Android under memory pressure, while `AgyForegroundService` continues running `server.py` and agent CLI subprocesses. All background notifications must function independently of `MainActivity`'s presence.
2. **`websockets` HTTP Handshake Limitation (`ARGON-V15-08`)**:
   In Python's `websockets` library, `process_request` only exposes request headers and does not populate `request.body`. An abort route using HTTP `POST` body causes an `AttributeError`. Using HTTP `GET /api/session/abort?cid=...` with header authentication eliminates this failure mode completely.
3. **PendingIntent Collision in Multi-Session Operations (`ARGON-V15-08`)**:
   Android caches `PendingIntent` objects based on `Intent.filterEquals()`. Explicit unique `data = Uri.parse("agy://notif/$action/$conversationId")` guarantees 100% collision-free routing across parallel sessions.
4. **BroadcastReceiver Death During Background Abort (`ARGON-V15-06`)**:
   When the user taps "Stop", `NotificationActionReceiver.onReceive()` must use `val pendingResult = goAsync()` to prevent the OS cgroup freezer from killing the thread before the loopback abort request finishes.
5. **Cold-Start Deep Link Dropping (`ARGON-V15-07`)**:
   If `MainActivity` was destroyed while in the background and is cold-started via a notification tap, `onCreate()` must queue the conversation ID and deliver it to the WebView via `consumePendingDeepLink()` once the initial session list loads.

---

## 2. Core Objectives

1. **Centralized Native Notification Subsystem (`AgyNotificationManager`)**:
   Thread-safe Kotlin singleton coordinating channels, notification builders, session tags, foreground state tracking, native 1Hz elapsed ticker, and adaptive rate-limiting.
2. **Decoupled Notification Action Broadcast Receiver (`NotificationActionReceiver`)**:
   Standalone `BroadcastReceiver` in `AndroidManifest.xml` handling "Hide", "Dismiss" (Android 14+ swipe), and "Stop" actions using `goAsync()`.
3. **Stdout Telemetry Bridge (`ARGON-V15-01`)**:
   `server.py` emits `__AGY_NOTIFY__:` JSON lines to stdout, intercepted by `AgyForegroundService` and delivered directly to `AgyNotificationManager`.
4. **Direct Local HTTP Abort Route in `server.py` (`ARGON-V15-08`)**:
   Expose an authenticated endpoint `GET /api/session/abort?cid=...` on `127.0.0.1:$port` with `X-Auth-Token` authentication for immediate process-group abort.
5. **Cold-Start Safe Navigation Queue (`ARGON-V15-07`)**:
   `MainActivity` with `android:launchMode="singleTop"` and `consumePendingDeepLink()` bridge method.
6. **Universal Android 10+ (API 29+) Compatibility**:
   Monochrome vector drawable `@drawable/ic_stat_agy` (`ARGON-V15-09`), `FLAG_IMMUTABLE` PendingIntents, and Android 13+ `POST_NOTIFICATIONS` runtime permission gating (`ARGON-V15-10`).

---

## 3. Subsystem Architecture & IPC Map

```mermaid
graph TD
    subgraph WebView Layer
        JS_Index["index.html (sessionBuffers & UI)"]
        JS_Pill["Live Pin Button & Progress Loop"]
    end

    subgraph Native Android Layer (ai.agy.app)
        Act["MainActivity (launchMode=singleTop)"]
        Bridge["AndroidAppInterface (@JavascriptInterface)"]
        NotifMgr["AgyNotificationManager (Thread-Safe Singleton)"]
        Receiver["NotificationActionReceiver (goAsync)"]
        FGS["AgyForegroundService (stdout reader)"]
        OS_NM["android.app.NotificationManager"]
    end

    subgraph Daemon & CLI Layer
        Py_Server["server.py (Process Orchestrator)"]
        Agy_CLI["agy CLI Subprocesses (PGID setsid)"]
    end

    %% Bindings
    JS_Index -->|window.AndroidApp| Bridge
    Bridge --> NotifMgr
    Act -->|onResume / onPause| NotifMgr
    Receiver -->|cancel notification| NotifMgr
    Receiver -->|GET /api/session/abort?cid=...| Py_Server
    FGS -->|__AGY_NOTIFY__: JSON lines| NotifMgr
    NotifMgr -->|notify / cancel with (cid, id)| OS_NM
    Py_Server -->|killpg(SIGINT)| Agy_CLI
```

---

## 4. Technical Specifications

### 4.1 Native `AgyNotificationManager.kt` (`ARGON-V15-05`)

```kotlin
package ai.agy.app

import android.Manifest
import android.app.*
import android.content.*
import android.content.pm.PackageManager
import android.graphics.Color
import android.net.Uri
import android.os.Build
import android.os.SystemClock
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import kotlinx.coroutines.*
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder
import java.util.Locale
import java.util.concurrent.ConcurrentHashMap

object AgyNotificationManager {
    const val TAG = "AgyNotifMgr"

    const val CHANNEL_ID_COMPLETION = "agy_completion_channel"
    const val CHANNEL_ID_PROGRESS = "agy_progress_channel"

    const val NOTIF_ID_COMPLETION = 3001
    const val NOTIF_ID_LIVE_PROGRESS = 4001

    const val ACTION_OPEN_SESSION = "ai.agy.app.action.OPEN_SESSION"
    const val ACTION_HIDE_LIVE_NOTIFICATION = "ai.agy.app.action.HIDE_LIVE_NOTIFICATION"
    const val ACTION_DISMISS_LIVE_NOTIFICATION = "ai.agy.app.action.DISMISS_LIVE_NOTIFICATION"
    const val ACTION_STOP_AGENT = "ai.agy.app.action.STOP_AGENT"
    const val EXTRA_CONVERSATION_ID = "extra_conversation_id"

    private const val MIN_STATE_UPDATE_INTERVAL_MS = 250L
    private const val MIN_TIMER_UPDATE_INTERVAL_MS = 1000L

    data class SessionLiveState(
        val conversationId: String,
        var sessionTitle: String = "Active Session",
        var statusText: String = "Thinking...",
        var startElapsedRealtimeMs: Long = SystemClock.elapsedRealtime(),
        var elapsedSeconds: Double = 0.0,
        var lastToolCall: String = "",
        var totalToolCalls: Int = 0,
        var isRunning: Boolean = false,
        var isPinned: Boolean = false,
        var abortedByUser: Boolean = false,
        var lastNotifiedAtMs: Long = 0L,
        var lastSignature: String = ""
    )

    private val sessionStates = ConcurrentHashMap<String, SessionLiveState>()
    private val stateLock = Any()
    private val managerScope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private var tickerJob: Job? = null

    @Volatile var isAppForeground: Boolean = false
    @Volatile var activeConversationId: String? = null
    @Volatile var pendingDeepLinkConversationId: String? = null

    fun initChannels(context: Context) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val nm = context.getSystemService(NotificationManager::class.java) ?: return

            val compChannel = NotificationChannel(
                CHANNEL_ID_COMPLETION,
                "Agent Turn Completion",
                NotificationManager.IMPORTANCE_DEFAULT
            ).apply {
                description = "Alerts when an AI agent finishes a turn in the background or an inactive session"
                enableLights(true)
                lightColor = Color.rgb(0, 163, 255)
                enableVibration(true)
                setShowBadge(true)
            }
            nm.createNotificationChannel(compChannel)

            val progChannel = NotificationChannel(
                CHANNEL_ID_PROGRESS,
                "Agent Live Progress",
                NotificationManager.IMPORTANCE_LOW
            ).apply {
                description = "Silent real-time execution status, elapsed time, and tool calls for pinned sessions"
                enableLights(false)
                enableVibration(false)
                setShowBadge(false)
            }
            nm.createNotificationChannel(progChannel)
        }
    }

    fun hasNotificationPermission(context: Context): Boolean {
        return if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            ContextCompat.checkSelfPermission(
                context,
                Manifest.permission.POST_NOTIFICATIONS
            ) == PackageManager.PERMISSION_GRANTED
        } else {
            true
        }
    }

    fun onActiveSessionChanged(context: Context, conversationId: String?) {
        val normalized = conversationId?.trim()?.ifEmpty { null }
        activeConversationId = normalized
        if (normalized != null && isAppForeground) {
            clearCompletionNotification(context, normalized)
        }
    }

    fun isPinned(conversationId: String): Boolean {
        synchronized(stateLock) {
            return sessionStates[conversationId]?.let { it.isRunning && it.isPinned } == true
        }
    }

    fun pinSession(
        context: Context,
        conversationId: String,
        sessionTitle: String,
        initialStatus: String
    ): Boolean {
        if (!hasNotificationPermission(context)) {
            return false
        }
        synchronized(stateLock) {
            val state = sessionStates.getOrPut(conversationId) {
                SessionLiveState(conversationId = conversationId)
            }
            state.isRunning = true
            state.isPinned = true
            state.abortedByUser = false
            if (sessionTitle.isNotBlank()) state.sessionTitle = sessionTitle
            if (initialStatus.isNotBlank()) state.statusText = initialStatus
            if (state.startElapsedRealtimeMs == 0L) {
                state.startElapsedRealtimeMs = SystemClock.elapsedRealtime()
            }
            dispatchLiveNotificationLocked(context, state, force = true)
            ensureTickerRunning(context.applicationContext)
        }
        return true
    }

    fun unpinSession(context: Context, conversationId: String) {
        synchronized(stateLock) {
            sessionStates[conversationId]?.let {
                it.isPinned = false
                it.lastSignature = ""
            }
            val nm = context.getSystemService(NotificationManager::class.java)
            nm?.cancel(conversationId, NOTIF_ID_LIVE_PROGRESS)
        }
    }

    fun clearLiveProgressNotification(context: Context, conversationId: String) {
        synchronized(stateLock) {
            sessionStates[conversationId]?.let {
                it.isRunning = false
                it.isPinned = false
                it.lastSignature = ""
            }
            val nm = context.getSystemService(NotificationManager::class.java)
            nm?.cancel(conversationId, NOTIF_ID_LIVE_PROGRESS)
        }
    }

    fun clearCompletionNotification(context: Context, conversationId: String) {
        val nm = context.getSystemService(NotificationManager::class.java) ?: return
        nm.cancel(conversationId, NOTIF_ID_COMPLETION)
    }

    fun updateLiveProgress(
        context: Context,
        conversationId: String,
        sessionTitle: String,
        statusText: String,
        elapsedSeconds: Double,
        lastToolCall: String,
        totalToolCalls: Int
    ) {
        synchronized(stateLock) {
            val state = sessionStates.getOrPut(conversationId) {
                SessionLiveState(conversationId = conversationId)
            }
            state.isRunning = true
            if (sessionTitle.isNotBlank()) state.sessionTitle = sessionTitle
            if (statusText.isNotBlank()) state.statusText = statusText
            if (elapsedSeconds > 0.0) {
                state.elapsedSeconds = elapsedSeconds
                state.startElapsedRealtimeMs =
                    SystemClock.elapsedRealtime() - (elapsedSeconds * 1000.0).toLong()
            }
            if (lastToolCall.isNotBlank()) state.lastToolCall = lastToolCall
            if (totalToolCalls >= 0) state.totalToolCalls = totalToolCalls

            if (!state.isPinned) return
            dispatchLiveNotificationLocked(context, state, force = false)
        }
    }

    private fun ensureTickerRunning(appContext: Context) {
        if (tickerJob?.isActive == true) return
        tickerJob = managerScope.launch {
            while (isActive) {
                delay(1000L)
                var anyPinned = false
                synchronized(stateLock) {
                    val nowRealtime = SystemClock.elapsedRealtime()
                    for (state in sessionStates.values) {
                        if (state.isRunning && state.isPinned) {
                            anyPinned = true
                            state.elapsedSeconds =
                                ((nowRealtime - state.startElapsedRealtimeMs).coerceAtLeast(0L)) / 1000.0
                            dispatchLiveNotificationLocked(appContext, state, force = false)
                        }
                    }
                }
                if (!anyPinned) break
            }
        }
    }

    private fun dispatchLiveNotificationLocked(
        context: Context,
        state: SessionLiveState,
        force: Boolean
    ) {
        if (!state.isRunning || !state.isPinned || !hasNotificationPermission(context)) return

        val now = SystemClock.elapsedRealtime()
        val stateSignature = "${state.sessionTitle}|${state.statusText}|${state.lastToolCall}|${state.totalToolCalls}"
        val stateChanged = stateSignature != state.lastSignature
        val elapsedSinceLastNotif = now - state.lastNotifiedAtMs

        if (!force) {
            if (stateChanged && elapsedSinceLastNotif < MIN_STATE_UPDATE_INTERVAL_MS) {
                return
            }
            if (!stateChanged && elapsedSinceLastNotif < MIN_TIMER_UPDATE_INTERVAL_MS) {
                return
            }
        }

        state.lastSignature = stateSignature
        state.lastNotifiedAtMs = now

        val nm = context.getSystemService(NotificationManager::class.java) ?: return
        val cid = state.conversationId
        val displayTitle = state.sessionTitle.trim().ifEmpty { "Active Session" }
        val totalSecs = state.elapsedSeconds.toInt().coerceAtLeast(0)
        val formattedTime = if (totalSecs < 60) {
            "${totalSecs}s"
        } else {
            "${totalSecs / 60}m ${totalSecs % 60}s"
        }

        val contentLine = if (state.lastToolCall.isNotBlank()) {
            "${state.statusText} ($formattedTime) • ${state.lastToolCall}"
        } else {
            "${state.statusText} ($formattedTime)"
        }

        val bigTextContent = buildString {
            append("• Status: ").append(state.statusText).append("\n")
            append("• Elapsed: ").append(formattedTime).append("\n")
            if (state.lastToolCall.isNotBlank()) {
                append("• Last Tool: ").append(state.lastToolCall).append("\n")
            }
            append("• Tool Calls: ").append(state.totalToolCalls)
        }

        val pendingFlags = PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT

        val openIntent = Intent(context, MainActivity::class.java).apply {
            action = ACTION_OPEN_SESSION
            data = Uri.parse("agy://notif/open/${Uri.encode(cid)}")
            flags = Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP
            putExtra(EXTRA_CONVERSATION_ID, cid)
        }
        val openPendingIntent = PendingIntent.getActivity(context, 0, openIntent, pendingFlags)

        val hideIntent = Intent(context, NotificationActionReceiver::class.java).apply {
            action = ACTION_HIDE_LIVE_NOTIFICATION
            data = Uri.parse("agy://notif/hide/${Uri.encode(cid)}")
            putExtra(EXTRA_CONVERSATION_ID, cid)
        }
        val hidePendingIntent = PendingIntent.getBroadcast(context, 0, hideIntent, pendingFlags)

        val dismissIntent = Intent(context, NotificationActionReceiver::class.java).apply {
            action = ACTION_DISMISS_LIVE_NOTIFICATION
            data = Uri.parse("agy://notif/dismiss/${Uri.encode(cid)}")
            putExtra(EXTRA_CONVERSATION_ID, cid)
        }
        val dismissPendingIntent = PendingIntent.getBroadcast(context, 0, dismissIntent, pendingFlags)

        val stopIntent = Intent(context, NotificationActionReceiver::class.java).apply {
            action = ACTION_STOP_AGENT
            data = Uri.parse("agy://notif/stop/${Uri.encode(cid)}")
            putExtra(EXTRA_CONVERSATION_ID, cid)
        }
        val stopPendingIntent = PendingIntent.getBroadcast(context, 0, stopIntent, pendingFlags)

        val notification = NotificationCompat.Builder(context, CHANNEL_ID_PROGRESS)
            .setSmallIcon(R.drawable.ic_stat_agy)
            .setContentTitle("$displayTitle • Running")
            .setContentText(contentLine)
            .setStyle(
                NotificationCompat.BigTextStyle()
                    .setBigContentTitle("$displayTitle • Live Progress")
                    .setSummaryText(formattedTime)
                    .bigText(bigTextContent)
            )
            .setContentIntent(openPendingIntent)
            .setDeleteIntent(dismissPendingIntent)
            .addAction(
                NotificationCompat.Action.Builder(
                    android.R.drawable.ic_menu_close_clear_cancel,
                    "Hide",
                    hidePendingIntent
                ).build()
            )
            .addAction(
                NotificationCompat.Action.Builder(
                    android.R.drawable.ic_delete,
                    "Stop",
                    stopPendingIntent
                ).build()
            )
            .setOngoing(true)
            .setOnlyAlertOnce(true)
            .setPriority(NotificationCompat.PRIORITY_LOW)
            .setCategory(NotificationCompat.CATEGORY_PROGRESS)
            .build()

        try {
            nm.notify(cid, NOTIF_ID_LIVE_PROGRESS, notification)
        } catch (e: SecurityException) {
            Log.w(TAG, "POST_NOTIFICATIONS not granted: ${e.message}")
        }
    }

    fun handleDaemonEvent(context: Context, jsonPayload: String) {
        try {
            val obj = JSONObject(jsonPayload)
            val event = obj.optString("event", "")
            val cid = obj.optString("conversation_id", "").trim()
            if (cid.isEmpty()) return

            when (event) {
                "turn_start" -> {
                    synchronized(stateLock) {
                        val state = sessionStates.getOrPut(cid) { SessionLiveState(cid) }
                        state.isRunning = true
                        state.abortedByUser = false
                        state.startElapsedRealtimeMs = SystemClock.elapsedRealtime()
                        state.elapsedSeconds = 0.0
                        state.totalToolCalls = 0
                        state.lastToolCall = ""
                        state.statusText = obj.optString("status", "Thinking...")
                        val title = obj.optString("title", "")
                        if (title.isNotBlank()) state.sessionTitle = title
                    }
                }
                "live_progress" -> {
                    updateLiveProgress(
                        context = context,
                        conversationId = cid,
                        sessionTitle = obj.optString("title", ""),
                        statusText = obj.optString("status", ""),
                        elapsedSeconds = obj.optDouble("elapsed", -1.0),
                        lastToolCall = obj.optString("last_tool", ""),
                        totalToolCalls = obj.optInt("tool_count", -1)
                    )
                }
                "turn_complete" -> {
                    postCompletionNotification(
                        context = context,
                        conversationId = cid,
                        sessionTitle = obj.optString("title", ""),
                        rawPreview = obj.optString("preview", ""),
                        turnStatus = obj.optString("status", "COMPLETED")
                    )
                }
            }
        } catch (e: Exception) {
            Log.w(TAG, "Malformed daemon notification payload: ${e.message}")
        }
    }

    fun requestSessionAbort(
        context: Context,
        conversationId: String,
        onComplete: (() -> Unit)? = null
    ) {
        synchronized(stateLock) {
            val state = sessionStates.getOrPut(conversationId) {
                SessionLiveState(conversationId = conversationId)
            }
            state.abortedByUser = true
            state.isRunning = false
            state.isPinned = false
            val nm = context.getSystemService(NotificationManager::class.java)
            nm?.cancel(conversationId, NOTIF_ID_LIVE_PROGRESS)
        }

        managerScope.launch(Dispatchers.IO) {
            try {
                val port = AgyForegroundService.readyPortFlow.value ?: 8081
                val token = AgyForegroundService.authTokenFlow.value ?: ""
                val encodedCid = URLEncoder.encode(conversationId, "UTF-8")
                // ARGON-V15-08: GET request with X-Auth-Token header
                val url = URL("http://127.0.0.1:$port/api/session/abort?cid=$encodedCid")
                val conn = (url.openConnection() as HttpURLConnection).apply {
                    requestMethod = "GET"
                    connectTimeout = 1500
                    readTimeout = 1500
                    setRequestProperty("X-Auth-Token", token)
                }
                val code = conn.responseCode
                Log.i(TAG, "Native abort GET /api/session/abort returned HTTP $code for $conversationId")
                conn.disconnect()
            } catch (e: Exception) {
                Log.w(TAG, "Direct HTTP abort failed, falling back to UI bridge: ${e.message}")
            } finally {
                MainActivity.notifySessionAbort(conversationId)
                onComplete?.invoke()
            }
        }
    }

    // ARGON-V15-09: Pre-bounded, ReDoS-safe Markdown Sanitizer
    fun sanitizeMarkdownPreview(markdown: String): String {
        if (markdown.isBlank()) return ""
        var text = markdown.take(2000).trim()
        text = text.replace(Regex("```[\\s\\S]*?```"), " [code] ")
        text = text.replace(Regex("```[\\s\\S]*$"), " [code] ")
        text = text.replace(Regex("!\\[[^\\]]*]\\([^)]*\\)"), "")
        text = text.replace(Regex("\\[([^\\]]+)]\\([^)]*\\)"), "$1")
        text = text.replace(Regex("`([^`]+)`"), "$1")
        text = text.replace(Regex("(?m)^#{1,6}\\s+"), "")
        text = text.replace(Regex("(?m)^>\\s?"), "")
        text = text.replace(Regex("(\\*\\*|__|\\*|_|~~)"), "")
        text = text.replace(Regex("\\s+"), " ").trim()
        if (text.length > 180) {
            val sub = text.substring(0, 180)
            val lastSpace = sub.lastIndexOf(' ')
            text = if (lastSpace > 130) "${sub.substring(0, lastSpace)}…" else "$sub…"
        }
        return text
    }
}
```

### 4.2 `NotificationActionReceiver` in AndroidManifest (`ARGON-V15-06`)

```xml
<receiver
    android:name=".NotificationActionReceiver"
    android:exported="false">
    <intent-filter>
        <action android:name="ai.agy.app.action.HIDE_LIVE_NOTIFICATION" />
        <action android:name="ai.agy.app.action.DISMISS_LIVE_NOTIFICATION" />
        <action android:name="ai.agy.app.action.STOP_AGENT" />
    </intent-filter>
</receiver>
```

### 4.3 Query-Parameter Authenticated Abort Route in `server.py` (`ARGON-V15-08`)

Inside `handle_http_request` in `agy-ui/server.py`:

```python
from urllib.parse import urlparse, parse_qs

# Inside process_request / handle_http_request:
parsed_url = urlparse(path)
if parsed_url.path == "/api/session/abort":
    if not is_request_authorized(request):
        return Response(int(HTTPStatus.UNAUTHORIZED), "Unauthorized", Headers([]), b"Unauthorized")

    qs = parse_qs(parsed_url.query)
    cid = (qs.get("cid", [""])[0] or "").strip()
    if not cid:
        return Response(int(HTTPStatus.BAD_REQUEST), "Bad Request", Headers([]), b'{"error":"missing_cid"}')

    try:
        stopped = await self.orchestrator.abort_turn(conversation_id=cid)
        await self._cancel_all_pending_questions(conversation_id=cid, reason="notification_abort")
        await self.broadcast({
            "type": "session_status",
            "conversation_id": cid,
            "state": "idle",
            "detail": "Interrupted" if stopped else "Ready",
        })
        await self.broadcast({
            "type": "turn_complete",
            "conversation_id": cid,
            "status": "INTERRUPTED",
        })
        return Response(
            int(HTTPStatus.OK),
            "OK",
            Headers([("Content-Type", "application/json")]),
            b'{"status":"aborted"}',
        )
    except Exception as exc:
        logger.warning("Error processing HTTP abort request for %s: %s", cid, exc)
        return Response(int(HTTPStatus.INTERNAL_SERVER_ERROR), "Error", Headers([]), b'{"error":"abort_failed"}')
```

### 4.4 Monochrome Status Bar Vector Drawable (`res/drawable/ic_stat_agy.xml`) (`ARGON-V15-09`)

```xml
<vector xmlns:android="http://schemas.android.com/apk/res/android"
    android:width="24dp"
    android:height="24dp"
    android:viewportWidth="24"
    android:viewportHeight="24"
    android:tint="#FFFFFF">
    <path
        android:fillColor="#FFFFFFFF"
        android:pathData="M12,2L2,19.5h4.2l2.1,-3.8h7.4l2.1,3.8H22L12,2zM9.8,12.7L12,8.6l2.2,4.1H9.8z" />
</vector>
```

---

## 5. Verification & Acceptance Criteria

- **AC-3.1**: `AgyNotificationManager` initializes notification channels on startup.
- **AC-3.2**: `GET /api/session/abort?cid=...` endpoint halts the agent subprocess cleanly within 2 seconds without `request.body` errors (`ARGON-V15-08`).
- **AC-3.3**: Broadcast receiver handles "Hide", "Dismiss", and "Stop" actions using `goAsync()` even when `MainActivity` is paused or killed (`ARGON-V15-06`).
- **AC-3.4**: All `@JavascriptInterface` bridge methods execute safely without UI thread blocking.
- **AC-3.5**: Full binary backwards and forwards compatibility from Android 10 (API 29) to Android 15+ (API 35).
