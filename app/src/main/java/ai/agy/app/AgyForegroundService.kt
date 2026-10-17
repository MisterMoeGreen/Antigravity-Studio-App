package ai.agy.app

import android.app.*
import android.content.*
import android.content.pm.ServiceInfo
import android.net.*
import android.os.*
import android.util.Log
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import org.json.JSONObject
import java.io.BufferedReader
import java.io.File
import java.io.InputStreamReader
import java.net.ServerSocket

class AgyForegroundService : Service() {

    private var wakeLock: PowerManager.WakeLock? = null
    private var daemonProcess: java.lang.Process? = null
    private val serviceScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var networkCallback: ConnectivityManager.NetworkCallback? = null
    private var batteryReceiver: BroadcastReceiver? = null

    companion object {
        const val TAG = "AgyService"
        const val CHANNEL_ID = "agy_daemon_channel"
        const val NOTIFICATION_ID = 2001
        const val ACTION_STOP_SERVICE = "ai.agy.app.action.STOP_SERVICE"

        private val _readyPortFlow = MutableStateFlow<Int?>(null)
        val readyPortFlow: StateFlow<Int?> = _readyPortFlow.asStateFlow()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (intent?.action == ACTION_STOP_SERVICE) {
            Log.i(TAG, "Stop action requested via notification")
            stopServiceGracefully()
            return START_NOT_STICKY
        }
        return START_STICKY
    }

    override fun onCreate() {
        super.onCreate()
        acquireWakeLock()
        startStickyNotification(8081)

        serviceScope.launch {
            EnvironmentBootstrapper.initialize(this@AgyForegroundService)
            registerDnsWatcher()
            registerTelemetryBridge()

            val port = pickAvailablePort(8081, listOf(8082, 8090, 8091))
            startStickyNotification(port)
            startPythonDaemon(port)
            _readyPortFlow.value = port
        }
    }

    private fun acquireWakeLock() {
        val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
        wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "AgyStudio::DaemonWakeLock").apply {
            setReferenceCounted(false)
            acquire()
        }
    }

    private fun startStickyNotification(port: Int) {
        val nm = getSystemService(NotificationManager::class.java)
        val channel = NotificationChannel(
            CHANNEL_ID,
            "Antigravity Agent Daemon",
            NotificationManager.IMPORTANCE_LOW
        )
        nm.createNotificationChannel(channel)

        val pendingIntent = PendingIntent.getActivity(
            this, 0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE
        )

        val stopIntent = Intent(this, AgyForegroundService::class.java).apply {
            action = ACTION_STOP_SERVICE
        }
        val stopPendingIntent = PendingIntent.getService(
            this,
            1,
            stopIntent,
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
        )

        val stopAction = Notification.Action.Builder(
            android.graphics.drawable.Icon.createWithResource(this, android.R.drawable.ic_menu_close_clear_cancel),
            "Stop",
            stopPendingIntent
        ).build()

        val notification = Notification.Builder(this, CHANNEL_ID)
            .setContentTitle("Antigravity Studio Running")
            .setContentText("Local AI coding agent active on port :$port")
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentIntent(pendingIntent)
            .addAction(stopAction)
            .setOngoing(true)
            .build()

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(
                NOTIFICATION_ID,
                notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE
            )
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
    }

    private fun startPythonDaemon(port: Int) {
        val root = "/data/data/$packageName/files"
        val usr = "$root/usr"
        val home = "$root/home"
        val nativeDir = applicationInfo.nativeLibraryDir

        // Clean up any stale orphaned server processes before spawning
        try {
            Runtime.getRuntime().exec(arrayOf("killall", "server.py")).waitFor()
        } catch (_: Exception) {}

        val pb = ProcessBuilder(
            "$usr/bin/python3",
            "$usr/share/agy-ui/server.py"
        ).directory(File("$home/workspace"))

        val env = pb.environment()
        env["HOME"] = home
        env["PREFIX"] = usr
        env["PATH"] = "$usr/bin:/system/bin"
        env["TMPDIR"] = "$usr/tmp"
        env["SHELL"] = "$usr/bin/bash"
        env["AGY_BIN"] = "$usr/bin/agy"
        env["AGY_WEB_PORT"] = port.toString()
        env["AGY_WEB_HOST"] = "127.0.0.1"
        env["GODEBUG"] = "netdns=cgo"
        env["LD_LIBRARY_PATH"] = "$usr/lib:$nativeDir"
        env["LD_PRELOAD"] = "$usr/lib/libagy_shim.so"
        env["SSL_CERT_FILE"] = "$usr/etc/tls/cert.pem"
        env["GIT_SSL_CAINFO"] = "$usr/etc/tls/cert.pem"
        env["GIT_EXEC_PATH"] = "$usr/libexec/git-core"
        val pInfo = packageManager.getPackageInfo(packageName, 0)
        val vName = pInfo.versionName ?: "1.0.1"
        val vCode = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) pInfo.longVersionCode else @Suppress("DEPRECATION") pInfo.versionCode.toLong()
        env["AGY_APP_VERSION"] = vName
        env["AGY_APP_VERSION_CODE"] = vCode.toString()
        env["PYTHONHOME"] = usr
        env["PYTHONPATH"] = "$usr/lib/python3.14:$usr/lib/python3.14/site-packages:$usr/lib/python3.12:$usr/lib/python3.12/site-packages"

        daemonProcess = pb.redirectErrorStream(true).start()
        Log.i(TAG, "Spawned server.py process on 127.0.0.1:$port")

        // CRITICAL: Continuously drain stdout/stderr pipe to prevent 64KB kernel buffer deadlocks
        val spawnedProc = daemonProcess
        if (spawnedProc != null) {
            serviceScope.launch {
                val stream = spawnedProc.inputStream
                val logFile = File("$usr/tmp/server.log")
                BufferedReader(InputStreamReader(stream)).use { reader ->
                    logFile.bufferedWriter().use { writer ->
                        var line: String?
                        while (reader.readLine().also { line = it } != null) {
                            Log.d("AgyServer", line!!)
                            writer.write(line)
                            writer.newLine()
                            writer.flush()
                        }
                    }
                }
            }
        }
    }

    private fun pickAvailablePort(preferred: Int, fallbacks: List<Int>): Int {
        for (port in listOf(preferred) + fallbacks) {
            try {
                ServerSocket(port).use { return port }
            } catch (_: Exception) {}
        }
        return ServerSocket(0).use { it.localPort }
    }

    private fun registerDnsWatcher() {
        val cm = getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
        val request = NetworkRequest.Builder()
            .addCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
            .build()

        val callback = object : ConnectivityManager.NetworkCallback() {
            override fun onLinkPropertiesChanged(network: Network, linkProperties: LinkProperties) {
                val servers = linkProperties.dnsServers.mapNotNull { it.hostAddress }
                val activeDns = if (servers.isNotEmpty()) servers else listOf("8.8.8.8", "1.1.1.1")
                val content = StringBuilder("# Dynamic DNS generated by Antigravity Studio\n")
                activeDns.forEach { content.append("nameserver $it\n") }
                content.append("options timeout:2 attempts:3\n")

                val resolvFile = File("/data/data/$packageName/files/usr/etc/resolv.conf")
                try {
                    val tmp = File("${resolvFile.absolutePath}.tmp")
                    tmp.writeText(content.toString())
                    tmp.renameTo(resolvFile)
                } catch (_: Exception) {}
            }
        }
        networkCallback = callback
        cm.registerNetworkCallback(request, callback)
    }

    private fun registerTelemetryBridge() {
        val filter = IntentFilter(Intent.ACTION_BATTERY_CHANGED)
        val receiver = object : BroadcastReceiver() {
            override fun onReceive(context: Context, intent: Intent) {
                val level = intent.getIntExtra(BatteryManager.EXTRA_LEVEL, -1)
                val scale = intent.getIntExtra(BatteryManager.EXTRA_SCALE, 100)
                val status = intent.getIntExtra(BatteryManager.EXTRA_STATUS, -1)
                val tempTenths = intent.getIntExtra(BatteryManager.EXTRA_TEMPERATURE, 0)
                val isCharging = status == BatteryManager.BATTERY_STATUS_CHARGING ||
                                 status == BatteryManager.BATTERY_STATUS_FULL

                val json = JSONObject().apply {
                    put("device_model", Build.MODEL)
                    put("device_manufacturer", Build.MANUFACTURER)
                    put("android_release", Build.VERSION.RELEASE)
                    put("sdk_int", Build.VERSION.SDK_INT)
                    put("battery_pct", if (scale > 0) (level * 100) / scale else level)
                    put("charging", isCharging)
                    put("temp_c", tempTenths / 10.0)
                    put("timestamp", System.currentTimeMillis() / 1000)
                }
                try {
                    File("/data/data/$packageName/files/usr/tmp/android_telemetry.json").writeText(json.toString())
                } catch (_: Exception) {}
            }
        }
        batteryReceiver = receiver
        registerReceiver(receiver, filter)
    }

    private fun stopServiceGracefully() {
        Log.i(TAG, "Stopping Antigravity background service and daemon...")
        _readyPortFlow.value = null
        try {
            serviceScope.cancel()
        } catch (_: Exception) {}
        try {
            daemonProcess?.destroyForcibly()
            daemonProcess = null
        } catch (_: Exception) {}
        try {
            Runtime.getRuntime().exec(arrayOf("killall", "-9", "server.py", "agy"))
        } catch (_: Exception) {}
        networkCallback?.let {
            val cm = getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
            try { cm.unregisterNetworkCallback(it) } catch (_: Exception) {}
            networkCallback = null
        }
        batteryReceiver?.let {
            try { unregisterReceiver(it) } catch (_: Exception) {}
            batteryReceiver = null
        }
        wakeLock?.let {
            if (it.isHeld) it.release()
            wakeLock = null
        }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.N) {
            stopForeground(STOP_FOREGROUND_REMOVE)
        } else {
            @Suppress("DEPRECATION")
            stopForeground(true)
        }
        stopSelf()
    }

    override fun onDestroy() {
        stopServiceGracefully()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null
}
