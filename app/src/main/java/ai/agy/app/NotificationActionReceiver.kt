package ai.agy.app

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.util.Log

class NotificationActionReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        val cid = intent.getStringExtra(AgyNotificationManager.EXTRA_CONVERSATION_ID)
            ?.trim()
            ?.takeIf { it.isNotEmpty() }
            ?: return

        when (intent.action) {
            AgyNotificationManager.ACTION_HIDE_LIVE_NOTIFICATION,
            AgyNotificationManager.ACTION_DISMISS_LIVE_NOTIFICATION -> {
                Log.i("AgyNotifReceiver", "Live notification hidden/dismissed for session $cid")
                AgyNotificationManager.unpinSession(context, cid)
                MainActivity.notifyLiveNotificationHidden(cid)
            }
            AgyNotificationManager.ACTION_STOP_AGENT -> {
                Log.i("AgyNotifReceiver", "Stop requested from notification for session $cid")
                val pendingResult = goAsync()
                AgyNotificationManager.requestSessionAbort(context, cid) {
                    try {
                        pendingResult.finish()
                    } catch (_: Exception) {}
                }
            }
        }
    }
}
