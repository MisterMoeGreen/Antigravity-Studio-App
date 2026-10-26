package ai.agy.app

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
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

    private val RE_CODE_FENCE_BLOCK = Regex("```[\\s\\S]*?```")
    private val RE_CODE_FENCE_UNCLOSED = Regex("```[\\s\\S]*$")
    private val RE_MARKDOWN_IMAGE = Regex("!\\[[^\\]]*]\\([^)]*\\)")
    private val RE_MARKDOWN_LINK = Regex("\\[([^\\]]+)]\\([^)]*\\)")
    private val RE_INLINE_CODE = Regex("`([^`]+)`")
    private val RE_HEADERS = Regex("(?m)^#{1,6}\\s+")
    private val RE_BLOCKQUOTES = Regex("(?m)^>\\s?")
    private val RE_EMPHASIS = Regex("(\\*\\*|__|\\*|_|~~)")
    private val RE_MULTI_WHITESPACE = Regex("\\s+")

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
        var lastCompletionNotifiedAtMs: Long = 0L,
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
            val nowRealtime = SystemClock.elapsedRealtime()
            if (state.startElapsedRealtimeMs <= 0L) {
                state.startElapsedRealtimeMs = nowRealtime
            }
            state.elapsedSeconds = ((nowRealtime - state.startElapsedRealtimeMs).coerceAtLeast(0L)) / 1000.0
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
                    if (!anyPinned) {
                        tickerJob = null
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

    fun shouldSendCompletionNotification(conversationId: String): Boolean {
        return !(isAppForeground && activeConversationId == conversationId)
    }

    fun postCompletionNotification(
        context: Context,
        conversationId: String,
        sessionTitle: String,
        rawPreview: String,
        turnStatus: String = "COMPLETED"
    ) {
        val wasAbortedByUser: Boolean
        val isDuplicateCompletion: Boolean
        synchronized(stateLock) {
            val now = SystemClock.elapsedRealtime()
            val state = sessionStates.getOrPut(conversationId) { SessionLiveState(conversationId) }
            wasAbortedByUser = state.abortedByUser
            isDuplicateCompletion = !state.isRunning && (now - state.lastCompletionNotifiedAtMs) < 1500L
            state.isRunning = false
            state.isPinned = false
            state.lastSignature = ""
            state.lastCompletionNotifiedAtMs = now
            val nm = context.getSystemService(NotificationManager::class.java)
            nm?.cancel(conversationId, NOTIF_ID_LIVE_PROGRESS)
        }

        if (wasAbortedByUser || isDuplicateCompletion || turnStatus.equals("INTERRUPTED", ignoreCase = true)) {
            return
        }

        if (!shouldSendCompletionNotification(conversationId) || !hasNotificationPermission(context)) {
            return
        }

        val nm = context.getSystemService(NotificationManager::class.java) ?: return
        val cleanPreview = sanitizeMarkdownPreview(rawPreview)
        val displayTitle = sessionTitle.trim().ifEmpty { "Session ${conversationId.take(6)}" }
        val statusHeading = if (turnStatus.equals("ERROR", ignoreCase = true)) {
            "Agent encountered an error"
        } else {
            "Agent finished"
        }

        val clickIntent = Intent(context, MainActivity::class.java).apply {
            action = ACTION_OPEN_SESSION
            data = Uri.parse("agy://notif/complete/${Uri.encode(conversationId)}")
            flags = Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP
            putExtra(EXTRA_CONVERSATION_ID, conversationId)
        }
        val contentPendingIntent = PendingIntent.getActivity(
            context,
            0,
            clickIntent,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )

        val bodyText = cleanPreview.ifEmpty {
            if (turnStatus.equals("ERROR", ignoreCase = true)) "Turn terminated with an error."
            else "Turn execution completed."
        }

        val notification = NotificationCompat.Builder(context, CHANNEL_ID_COMPLETION)
            .setSmallIcon(R.drawable.ic_stat_agy)
            .setContentTitle(displayTitle)
            .setContentText("$statusHeading • $bodyText")
            .setStyle(
                NotificationCompat.BigTextStyle()
                    .setBigContentTitle(displayTitle)
                    .setSummaryText(statusHeading)
                    .bigText(bodyText)
            )
            .setContentIntent(contentPendingIntent)
            .setAutoCancel(true)
            .setOnlyAlertOnce(false)
            .setPriority(NotificationCompat.PRIORITY_DEFAULT)
            .setCategory(NotificationCompat.CATEGORY_MESSAGE)
            .build()

        try {
            nm.notify(conversationId, NOTIF_ID_COMPLETION, notification)
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
                val url = URL("http://127.0.0.1:$port/api/session/abort?cid=$encodedCid")
                val conn = (url.openConnection() as HttpURLConnection).apply {
                    requestMethod = "GET"
                    connectTimeout = 4000
                    readTimeout = 4000
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

    fun sanitizeMarkdownPreview(markdown: String): String {
        if (markdown.isBlank()) return ""
        var text = markdown.take(2000).trim()
        text = text.replace(RE_CODE_FENCE_BLOCK, " [code] ")
        text = text.replace(RE_CODE_FENCE_UNCLOSED, " [code] ")
        text = text.replace(RE_MARKDOWN_IMAGE, "")
        text = text.replace(RE_MARKDOWN_LINK, "$1")
        text = text.replace(RE_INLINE_CODE, "$1")
        text = text.replace(RE_HEADERS, "")
        text = text.replace(RE_BLOCKQUOTES, "")
        text = text.replace(RE_EMPHASIS, "")
        text = text.replace(RE_MULTI_WHITESPACE, " ").trim()
        if (text.length > 180) {
            val sub = text.substring(0, 180)
            val lastSpace = sub.lastIndexOf(' ')
            text = if (lastSpace > 130) "${sub.substring(0, lastSpace)}…" else "$sub…"
        }
        return text
    }
}
