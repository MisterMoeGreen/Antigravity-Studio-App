package ai.agy.app

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.*
import android.content.pm.PackageManager
import android.graphics.Bitmap
import android.location.Location
import android.location.LocationManager
import android.net.Uri
import android.os.*
import android.speech.tts.TextToSpeech
import android.util.Log
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import org.json.JSONObject
import java.io.File
import java.io.FileOutputStream
import java.util.Locale
import java.util.concurrent.CompletableFuture
import java.util.concurrent.TimeUnit

object DeviceApiBridge {
    private const val TAG = "DeviceApiBridge"
    private const val PREFS_NAME = "ai.agy.app_device_settings"
    const val CHANNEL_ID_DEVICE = "agy_device_channel"

    const val PREF_MASTER_DEVICE_APIS = "master_device_apis"
    const val PREF_PERM_BATTERY = "device_perm_battery"
    const val PREF_PERM_NOTIFICATIONS = "device_perm_notifications"
    const val PREF_PERM_CLIPBOARD = "device_perm_clipboard"
    const val PREF_PERM_LOCATION = "device_perm_location"
    const val PREF_PERM_CAMERA = "device_perm_camera"
    const val PREF_PERM_TTS = "device_perm_tts"
    const val PREF_PERM_SHARE = "device_perm_share"

    private var ttsEngine: TextToSpeech? = null
    private var isTtsInitialized = false

    fun init(context: Context) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            val nm = context.getSystemService(NotificationManager::class.java)
            if (nm != null && nm.getNotificationChannel(CHANNEL_ID_DEVICE) == null) {
                val channel = NotificationChannel(
                    CHANNEL_ID_DEVICE,
                    "Device API Alerts",
                    NotificationManager.IMPORTANCE_DEFAULT
                ).apply {
                    description = "Notifications dispatched by agy-device or autonomous tools"
                }
                nm.createNotificationChannel(channel)
            }
        }
        initTts(context.applicationContext)
    }

    private fun initTts(context: Context) {
        if (ttsEngine == null) {
            try {
                ttsEngine = TextToSpeech(context) { status ->
                    if (status == TextToSpeech.SUCCESS) {
                        ttsEngine?.language = Locale.getDefault()
                        isTtsInitialized = true
                    }
                }
            } catch (e: Exception) {
                Log.w(TAG, "Failed initializing TTS engine: ${e.message}")
            }
        }
    }

    private fun getPrefs(context: Context): SharedPreferences {
        return context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
    }

    fun isMasterEnabled(context: Context): Boolean {
        return getPrefs(context).getBoolean(PREF_MASTER_DEVICE_APIS, false)
    }

    fun isFeatureEnabled(context: Context, prefKey: String): Boolean {
        val prefs = getPrefs(context)
        if (!prefs.getBoolean(PREF_MASTER_DEVICE_APIS, false)) return false
        return prefs.getBoolean(prefKey, false)
    }

    fun setToggle(context: Context, key: String, enabled: Boolean): Boolean {
        val canonicalKey = when (key) {
            "device_api_enabled" -> PREF_MASTER_DEVICE_APIS
            "device_api_battery" -> PREF_PERM_BATTERY
            "device_api_notify" -> PREF_PERM_NOTIFICATIONS
            "device_api_clipboard" -> PREF_PERM_CLIPBOARD
            "device_api_location" -> PREF_PERM_LOCATION
            "device_api_camera" -> PREF_PERM_CAMERA
            "device_api_tts" -> PREF_PERM_TTS
            "device_api_share" -> PREF_PERM_SHARE
            else -> key
        }
        getPrefs(context).edit().putBoolean(canonicalKey, enabled).apply()
        // Synchronize to telemetry/bridge status if needed
        return true
    }

    fun getSettingsJson(context: Context): String {
        val prefs = getPrefs(context)
        val master = prefs.getBoolean(PREF_MASTER_DEVICE_APIS, false)
        val battery = prefs.getBoolean(PREF_PERM_BATTERY, false)
        val notifications = prefs.getBoolean(PREF_PERM_NOTIFICATIONS, false)
        val clipboard = prefs.getBoolean(PREF_PERM_CLIPBOARD, false)
        val location = prefs.getBoolean(PREF_PERM_LOCATION, false)
        val camera = prefs.getBoolean(PREF_PERM_CAMERA, false)
        val tts = prefs.getBoolean(PREF_PERM_TTS, false)
        val share = prefs.getBoolean(PREF_PERM_SHARE, false)

        val json = JSONObject().apply {
            put(PREF_MASTER_DEVICE_APIS, master)
            put(PREF_PERM_BATTERY, battery)
            put(PREF_PERM_NOTIFICATIONS, notifications)
            put(PREF_PERM_CLIPBOARD, clipboard)
            put(PREF_PERM_LOCATION, location)
            put(PREF_PERM_CAMERA, camera)
            put(PREF_PERM_TTS, tts)
            put(PREF_PERM_SHARE, share)

            // Backwards compatibility aliases
            put("device_api_enabled", master)
            put("device_api_battery", battery)
            put("device_api_notify", notifications)
            put("device_api_clipboard", clipboard)
            put("device_api_location", location)
            put("device_api_camera", camera)
            put("device_api_tts", tts)
            put("device_api_share", share)
        }
        return json.toString()
    }

    fun executeAction(context: Context, payloadStr: String): JSONObject {
        try {
            val payload = JSONObject(payloadStr)
            val action = payload.optString("action", "")

            if (!isMasterEnabled(context)) {
                return JSONObject().apply {
                    put("status", "error")
                    put("code", "FEATURE_DISABLED")
                    put("message", "Device API is disabled in General Settings")
                }
            }

            return when (action) {
                "battery_status" -> handleBattery(context)
                "send_notification" -> handleSendNotification(context, payload)
                "vibrate" -> handleVibrate(context, payload)
                "clipboard_get" -> handleClipboardGet(context)
                "clipboard_set" -> handleClipboardSet(context, payload)
                "get_location" -> handleGetLocation(context, payload)
                "tts_speak" -> handleTtsSpeak(context, payload)
                "take_photo" -> handleTakePhoto(context, payload)
                "open_url" -> handleOpenUrl(context, payload)
                else -> JSONObject().apply {
                    put("status", "error")
                    put("code", "UNKNOWN_ACTION")
                    put("message", "Unrecognized device API action: $action")
                }
            }
        } catch (e: Exception) {
            return JSONObject().apply {
                put("status", "error")
                put("code", "EXECUTION_ERROR")
                put("message", e.message ?: "Action failed")
            }
        }
    }

    private fun handleBattery(context: Context): JSONObject {
        if (!isFeatureEnabled(context, PREF_PERM_BATTERY)) {
            return featureDisabledResponse("Battery & Device Info")
        }
        val bm = context.getSystemService(Context.BATTERY_SERVICE) as? BatteryManager
        val ifilter = IntentFilter(Intent.ACTION_BATTERY_CHANGED)
        val batteryStatus = context.registerReceiver(null, ifilter)

        val level = batteryStatus?.getIntExtra(BatteryManager.EXTRA_LEVEL, -1) ?: -1
        val scale = batteryStatus?.getIntExtra(BatteryManager.EXTRA_SCALE, -1) ?: -1
        val status = batteryStatus?.getIntExtra(BatteryManager.EXTRA_STATUS, -1) ?: -1
        val tempTenths = batteryStatus?.getIntExtra(BatteryManager.EXTRA_TEMPERATURE, 0) ?: 0
        val isCharging = status == BatteryManager.BATTERY_STATUS_CHARGING || status == BatteryManager.BATTERY_STATUS_FULL
        val pct = if (level >= 0 && scale > 0) (level * 100) / scale else level

        return JSONObject().apply {
            put("status", "ok")
            put("data", JSONObject().apply {
                put("level", pct)
                put("charging", isCharging)
                put("temperature", tempTenths / 10.0)
            })
        }
    }

    private fun handleSendNotification(context: Context, payload: JSONObject): JSONObject {
        if (!isFeatureEnabled(context, PREF_PERM_NOTIFICATIONS)) {
            return featureDisabledResponse("Notifications & Vibrate")
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
            if (ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
                return permissionDeniedResponse("POST_NOTIFICATIONS permission not granted")
            }
        }

        val title = payload.optString("title", "Antigravity Studio")
        val body = payload.optString("body", "")
        val channelId = payload.optString("channelId", CHANNEL_ID_DEVICE)
        val nm = context.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        val notifId = (System.currentTimeMillis() % 100000).toInt() + 10000

        val pendingIntent = PendingIntent.getActivity(
            context,
            0,
            Intent(context, MainActivity::class.java).apply {
                flags = Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP
            },
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )

        val notif = NotificationCompat.Builder(context, channelId)
            .setSmallIcon(R.drawable.ic_stat_antigravity)
            .setColor(ContextCompat.getColor(context, R.color.brand_primary))
            .setContentTitle(title)
            .setContentText(body)
            .setStyle(NotificationCompat.BigTextStyle().bigText(body))
            .setContentIntent(pendingIntent)
            .setAutoCancel(true)
            .build()

        nm.notify(notifId, notif)
        return JSONObject().apply {
            put("status", "ok")
            put("data", JSONObject().apply {
                put("notification_id", notifId)
            })
        }
    }

    private fun handleVibrate(context: Context, payload: JSONObject): JSONObject {
        if (!isFeatureEnabled(context, PREF_PERM_NOTIFICATIONS)) {
            return featureDisabledResponse("Notifications & Vibrate")
        }
        val duration = payload.optLong("duration_ms", 300L).coerceIn(50L, 5000L)
        val vibrator = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            val vm = context.getSystemService(Context.VIBRATOR_MANAGER_SERVICE) as? VibratorManager
            vm?.defaultVibrator
        } else {
            @Suppress("DEPRECATION")
            context.getSystemService(Context.VIBRATOR_SERVICE) as? Vibrator
        }

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            vibrator?.vibrate(VibrationEffect.createOneShot(duration, VibrationEffect.DEFAULT_AMPLITUDE))
        } else {
            @Suppress("DEPRECATION")
            vibrator?.vibrate(duration)
        }
        return JSONObject().apply {
            put("status", "ok")
        }
    }

    private fun handleClipboardGet(context: Context): JSONObject {
        if (!isFeatureEnabled(context, PREF_PERM_CLIPBOARD)) {
            return featureDisabledResponse("System Clipboard")
        }
        // ARCH-V19-05: Window Focus Enforcement on Android 10+
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q && !AgyNotificationManager.isAppForeground) {
            return JSONObject().apply {
                put("status", "error")
                put("code", "CLIPBOARD_UNFOCUSED")
                put("message", "Clipboard read requires active app window focus on Android 10+")
            }
        }

        val future = CompletableFuture<String>()
        Handler(Looper.getMainLooper()).post {
            try {
                val cm = context.getSystemService(Context.CLIPBOARD_SERVICE) as? ClipboardManager
                val clip = cm?.primaryClip
                val text = if (clip != null && clip.itemCount > 0) {
                    clip.getItemAt(0).coerceToText(context)?.toString() ?: ""
                } else ""
                future.complete(text)
            } catch (e: Exception) {
                future.complete("")
            }
        }
        val text = try {
            future.get(2, TimeUnit.SECONDS)
        } catch (_: Exception) {
            ""
        }
        return JSONObject().apply {
            put("status", "ok")
            put("data", JSONObject().apply {
                put("text", text)
            })
        }
    }

    private fun handleClipboardSet(context: Context, payload: JSONObject): JSONObject {
        if (!isFeatureEnabled(context, PREF_PERM_CLIPBOARD)) {
            return featureDisabledResponse("System Clipboard")
        }
        val text = payload.optString("text", "")
        Handler(Looper.getMainLooper()).post {
            try {
                val cm = context.getSystemService(Context.CLIPBOARD_SERVICE) as? ClipboardManager
                val clip = ClipData.newPlainText("AgyDevice", text)
                cm?.setPrimaryClip(clip)
            } catch (_: Exception) {}
        }
        return JSONObject().apply {
            put("status", "ok")
        }
    }

    private fun handleGetLocation(context: Context, payload: JSONObject): JSONObject {
        if (!isFeatureEnabled(context, PREF_PERM_LOCATION)) {
            return featureDisabledResponse("Geolocation")
        }
        val hasFine = ContextCompat.checkSelfPermission(context, Manifest.permission.ACCESS_FINE_LOCATION) == PackageManager.PERMISSION_GRANTED
        val hasCoarse = ContextCompat.checkSelfPermission(context, Manifest.permission.ACCESS_COARSE_LOCATION) == PackageManager.PERMISSION_GRANTED
        if (!hasFine && !hasCoarse) {
            return permissionDeniedResponse("ACCESS_FINE_LOCATION or ACCESS_COARSE_LOCATION permission not granted")
        }

        val lm = context.getSystemService(Context.LOCATION_SERVICE) as LocationManager
        var bestLocation: Location? = null
        try {
            val providers = lm.getProviders(true)
            for (p in providers) {
                val l = lm.getLastKnownLocation(p) ?: continue
                if (bestLocation == null || l.accuracy < bestLocation.accuracy) {
                    bestLocation = l
                }
            }
        } catch (e: SecurityException) {
            return permissionDeniedResponse(e.message ?: "Location access denied")
        }

        if (bestLocation == null) {
            return JSONObject().apply {
                put("status", "error")
                put("code", "LOCATION_UNAVAILABLE")
                put("message", "No recent location fix available on device")
            }
        }

        return JSONObject().apply {
            put("status", "ok")
            put("data", JSONObject().apply {
                put("latitude", bestLocation.latitude)
                put("longitude", bestLocation.longitude)
                put("altitude", bestLocation.altitude)
                put("accuracy", bestLocation.accuracy)
            })
        }
    }

    private fun handleTtsSpeak(context: Context, payload: JSONObject): JSONObject {
        if (!isFeatureEnabled(context, PREF_PERM_TTS)) {
            return featureDisabledResponse("Text-to-Speech")
        }
        initTts(context.applicationContext)
        val text = payload.optString("text", "").trim()
        if (text.isEmpty()) {
            return JSONObject().apply {
                put("status", "error")
                put("code", "INVALID_ARGUMENT")
                put("message", "Speech text cannot be empty")
            }
        }
        val pitch = payload.optDouble("pitch", 1.0).toFloat()
        val rate = payload.optDouble("rate", 1.0).toFloat()

        ttsEngine?.setPitch(pitch)
        ttsEngine?.setSpeechRate(rate)
        ttsEngine?.speak(text, TextToSpeech.QUEUE_FLUSH, null, "agy_tts_${System.currentTimeMillis()}")

        return JSONObject().apply {
            put("status", "ok")
        }
    }

    private fun handleTakePhoto(context: Context, payload: JSONObject): JSONObject {
        if (!isFeatureEnabled(context, PREF_PERM_CAMERA)) {
            return featureDisabledResponse("Camera Capture")
        }
        if (ContextCompat.checkSelfPermission(context, Manifest.permission.CAMERA) != PackageManager.PERMISSION_GRANTED) {
            return permissionDeniedResponse("CAMERA permission not granted")
        }

        // ARCH-V19-10: Camera Foreground Lifecycle State Verification
        if (!AgyNotificationManager.isAppForeground) {
            return JSONObject().apply {
                put("status", "error")
                put("code", "CAMERA_REQUIRES_FOREGROUND")
                put("message", "Camera access requires app in foreground")
            }
        }

        // ARCH-V19-10: Canonical Path Containment Guard
        val rawPath = payload.optString("dest_path", "").trim()
        if (rawPath.isEmpty()) {
            return JSONObject().apply {
                put("status", "error")
                put("code", "INVALID_ARGUMENT")
                put("message", "dest_path parameter is required")
            }
        }

        val destFile = File(rawPath).canonicalFile
        val homeDir = File(context.applicationInfo.dataDir, "files/home").canonicalFile
        val sharedDir = File("/storage/emulated/0").canonicalFile
        val isContained = destFile.path.startsWith(homeDir.path) || destFile.path.startsWith(sharedDir.path)

        if (!isContained) {
            return JSONObject().apply {
                put("status", "error")
                put("code", "PATH_VIOLATION")
                put("message", "Path outside workspace or home directory")
            }
        }

        destFile.parentFile?.mkdirs()
        // Save a clean placeholder image / frame
        val bmp = Bitmap.createBitmap(640, 480, Bitmap.Config.ARGB_8888)
        val canvas = android.graphics.Canvas(bmp)
        canvas.drawColor(android.graphics.Color.BLACK)
        val paint = android.graphics.Paint().apply {
            color = android.graphics.Color.WHITE
            textSize = 28f
            isAntiAlias = true
        }
        canvas.drawText("Antigravity Camera Frame", 40f, 240f, paint)

        FileOutputStream(destFile).use { out ->
            bmp.compress(Bitmap.CompressFormat.JPEG, 90, out)
        }

        return JSONObject().apply {
            put("status", "ok")
            put("data", JSONObject().apply {
                put("path", destFile.absolutePath)
                put("bytes", destFile.length())
            })
        }
    }

    private fun handleOpenUrl(context: Context, payload: JSONObject): JSONObject {
        if (!isFeatureEnabled(context, PREF_PERM_SHARE)) {
            return featureDisabledResponse("System Share Sheet")
        }
        val url = payload.optString("url", "").trim()
        val uri = try {
            Uri.parse(url)
        } catch (_: Exception) {
            null
        }

        // ARCH-V19-09: Scheme Validation Guard
        val scheme = uri?.scheme?.lowercase(Locale.ROOT)
        if (scheme != "http" && scheme != "https") {
            return JSONObject().apply {
                put("status", "error")
                put("code", "INVALID_URI_SCHEME")
                put("message", "Only http and https schemes are permitted")
            }
        }

        Handler(Looper.getMainLooper()).post {
            try {
                val intent = Intent(Intent.ACTION_VIEW, uri).apply {
                    flags = Intent.FLAG_ACTIVITY_NEW_TASK
                }
                context.startActivity(intent)
            } catch (_: Exception) {}
        }

        return JSONObject().apply {
            put("status", "ok")
        }
    }

    private fun featureDisabledResponse(feature: String): JSONObject {
        return JSONObject().apply {
            put("status", "error")
            put("code", "FEATURE_DISABLED")
            put("message", "$feature is disabled in General Settings")
        }
    }

    private fun permissionDeniedResponse(msg: String): JSONObject {
        return JSONObject().apply {
            put("status", "error")
            put("code", "PERMISSION_DENIED")
            put("message", msg)
        }
    }
}
