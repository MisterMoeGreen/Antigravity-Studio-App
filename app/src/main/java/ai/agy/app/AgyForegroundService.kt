package ai.agy.app

import android.app.*
import android.content.*
import android.content.pm.ServiceInfo
import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.drawable.BitmapDrawable
import android.net.*
import android.os.*
import android.util.Log
import androidx.core.content.ContextCompat
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import org.json.JSONObject
import kotlin.concurrent.thread
import java.io.BufferedReader
import java.io.BufferedWriter
import java.io.File
import java.io.InputStreamReader
import java.io.OutputStreamWriter
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket

class AgyForegroundService : Service() {

    private var wakeLock: PowerManager.WakeLock? = null
    private var daemonProcess: java.lang.Process? = null
    private val serviceScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var networkCallback: ConnectivityManager.NetworkCallback? = null
    private var batteryReceiver: BroadcastReceiver? = null
    private var vaultServerSocket: ServerSocket? = null

    companion object {
        const val TAG = "AgyService"
        const val CHANNEL_ID = "agy_daemon_channel"
        const val NOTIFICATION_ID = 2001
        const val ACTION_STOP_SERVICE = "ai.agy.app.action.STOP_SERVICE"
        const val ACTION_UPDATE_NOTIFICATION = "ai.agy.app.action.UPDATE_NOTIFICATION"

        private val _readyPortFlow = MutableStateFlow<Int?>(null)
        val readyPortFlow: StateFlow<Int?> = _readyPortFlow.asStateFlow()

        private val _authTokenFlow = MutableStateFlow<String?>(null)
        val authTokenFlow: StateFlow<String?> = _authTokenFlow.asStateFlow()
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_STOP_SERVICE -> {
                Log.i(TAG, "Stop action requested via notification")
                stopServiceGracefully()
                return START_NOT_STICKY
            }
            ACTION_UPDATE_NOTIFICATION -> {
                val port = _readyPortFlow.value ?: 8081
                startStickyNotification(port)
                return START_STICKY
            }
        }
        return START_STICKY
    }

    override fun onTaskRemoved(rootIntent: Intent?) {
        super.onTaskRemoved(rootIntent)
        Log.i(TAG, "Antigravity Studio task removed from Recents; retaining AgyForegroundService and daemon.")
        refreshWakeLock()
    }

    override fun onCreate() {
        super.onCreate()
        _readyPortFlow.value = null
        _authTokenFlow.value = null
        AgyNotificationManager.initChannels(this)
        KeystoreVault.migrateLegacyApiKeysIfPresent(this)
        acquireWakeLock()
        startStickyNotification(8081)

        serviceScope.launch {
            EnvironmentBootstrapper.initialize(this@AgyForegroundService)
            reconcileSymlinkFarmAndLock(this@AgyForegroundService)
            registerDnsWatcher()
            registerTelemetryBridge()
            cleanupStaleDaemonProcesses()

            val port = pickAvailablePort(8081, listOf(8082, 8090, 8091))
            val token = java.util.UUID.randomUUID().toString().replace("-", "")
            _authTokenFlow.value = token
            startStickyNotification(port)
            startPythonDaemon(port, token)
            _readyPortFlow.value = port
        }
    }

    private fun acquireWakeLock() {
        val pm = getSystemService(Context.POWER_SERVICE) as PowerManager
        if (wakeLock == null) {
            wakeLock = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "AgyStudio::DaemonWakeLock").apply {
                setReferenceCounted(false)
            }
        }
        if (wakeLock?.isHeld != true) {
            wakeLock?.acquire()
        }
    }

    fun refreshWakeLock() {
        try {
            acquireWakeLock()
        } catch (e: Exception) {
            Log.w(TAG, "Failed refreshing partial wake lock: ${e.message}")
        }
    }

    private fun getLauncherLargeIconBitmap(): Bitmap? {
        val drawable = ContextCompat.getDrawable(this, R.mipmap.ic_launcher) ?: return null
        if (drawable is BitmapDrawable && drawable.bitmap != null) {
            return drawable.bitmap
        }
        val width = if (drawable.intrinsicWidth > 0) drawable.intrinsicWidth else 192
        val height = if (drawable.intrinsicHeight > 0) drawable.intrinsicHeight else 192
        val bitmap = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888)
        val canvas = Canvas(bitmap)
        drawable.setBounds(0, 0, canvas.width, canvas.height)
        drawable.draw(canvas)
        return bitmap
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
            Intent(this, MainActivity::class.java).apply {
                flags = Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP
            },
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT
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

        val largeBmp = getLauncherLargeIconBitmap()
        val notifBuilder = Notification.Builder(this, CHANNEL_ID)
            .setContentTitle("Antigravity Studio Running")
            .setContentText("Local AI coding agent active on port :$port")
            .setSmallIcon(R.drawable.ic_stat_antigravity)
            .setColor(ContextCompat.getColor(this, R.color.brand_primary))
            .setContentIntent(pendingIntent)
            .addAction(stopAction)
            .setOngoing(true)
            .setOnlyAlertOnce(true)

        if (largeBmp != null) {
            notifBuilder.setLargeIcon(android.graphics.drawable.Icon.createWithBitmap(largeBmp))
        }
        val notification = notifBuilder.build()

        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            startForeground(
                NOTIFICATION_ID,
                notification,
                ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE
            )
        } else {
            startForeground(NOTIFICATION_ID, notification)
        }
        try {
            nm.notify(NOTIFICATION_ID, notification)
        } catch (_: Exception) {}
    }

    private fun startPythonDaemon(port: Int, token: String) {
        val root = "/data/data/$packageName/files"
        val usr = "$root/usr"
        val home = "$root/home"
        val nativeDir = applicationInfo.nativeLibraryDir

        // Securely write ephemeral session auth token with 0600 permissions
        try {
            val tokenFile = File("$usr/tmp/.auth_token")
            tokenFile.parentFile?.mkdirs()
            tokenFile.writeText(token)
            android.system.Os.chmod(tokenFile.absolutePath, 0b110_000_000)
        } catch (_: Exception) {}

        // Clean up any stale orphaned server processes before spawning
        cleanupStaleDaemonProcesses()

        val vaultPort = startVaultBridgeServer(token)

        val pb = ProcessBuilder(
            "$usr/bin/python3",
            "$usr/share/agy-ui/server.py"
        ).directory(File("$home/workspace"))

        val env = pb.environment()
        env["HOME"] = home
        env["PREFIX"] = usr
        env["PATH"] = "$usr/bin:$home/.local/bin:/system/bin"
        env["PYTHONUSERBASE"] = "$home/.local"
        env["TMPDIR"] = "$usr/tmp"
        env["SHELL"] = "$usr/bin/agy-sh"
        env["PROOT_LOADER"] = "$nativeDir/libproot_loader.so"
        env["PROOT_TMP_DIR"] = "$usr/tmp"
        env["AGY_BIN"] = "$usr/bin/agy"
        env["AGY_DISABLE_AUTO_UPDATE"] = "1"
        env["AGY_WEB_PORT"] = port.toString()
        env["AGY_WEB_HOST"] = "127.0.0.1"
        env["AGY_AUTH_TOKEN"] = token
        env["AGY_INTERNAL_VAULT_TOKEN"] = token
        env["AGY_VAULT_PORT"] = vaultPort.toString()
        env["GODEBUG"] = "netdns=cgo"
        env["LD_LIBRARY_PATH"] = "$usr/lib:$nativeDir"
        env["LD_PRELOAD"] = "$usr/lib/libagy_shim.so"
        env["SSL_CERT_FILE"] = "$usr/etc/tls/cert.pem"
        env["GIT_SSL_CAINFO"] = "$usr/etc/tls/cert.pem"
        env["GIT_EXEC_PATH"] = "$usr/libexec/git-core"
        env["AGY_APP_VERSION"] = BuildConfig.VERSION_NAME
        env["AGY_APP_VERSION_CODE"] = BuildConfig.APP_VERSION_CODE.toString()
        env["AGY_RUNTIME_VERSION"] = BuildConfig.AGY_RUNTIME_VERSION
        env["AGY_MIN_VERSION"] = BuildConfig.AGY_MIN_VERSION
        env["ENGINE_COMPAT_EPOCH"] = BuildConfig.ENGINE_COMPAT_EPOCH.toString()
        env["FORCE_ENGINE_UPDATE"] = BuildConfig.FORCE_ENGINE_UPDATE.toString()
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
                            val currentLine = line ?: continue
                            if (currentLine.startsWith("__AGY_NOTIFY__:")) {
                                val jsonPayload = currentLine.removePrefix("__AGY_NOTIFY__:").trim()
                                refreshWakeLock()
                                AgyNotificationManager.handleDaemonEvent(this@AgyForegroundService, jsonPayload)
                                continue
                            }
                            Log.d("AgyServer", currentLine)
                            writer.write(currentLine)
                            writer.newLine()
                            writer.flush()
                        }
                    }
                }
            }
        }
    }

    private fun cleanupStaleDaemonProcesses() {
        val root = "/data/data/$packageName/files"
        val usr = "$root/usr"
        val myPid = android.os.Process.myPid()
        try {
            val pidFile = File("$usr/tmp/server.pid")
            if (pidFile.exists()) {
                val oldPid = pidFile.readText().trim().toIntOrNull()
                if (oldPid != null && oldPid > 0 && oldPid != myPid) {
                    try {
                        android.system.Os.kill(oldPid, 9)
                    } catch (_: Exception) {}
                }
                pidFile.delete()
            }
            // Scan /proc for any leftover server.py or agy daemon instances belonging to this UID
            val procDir = File("/proc")
            procDir.listFiles()?.forEach { file ->
                if (file.isDirectory && file.name.all { it.isDigit() }) {
                    val pid = file.name.toIntOrNull() ?: return@forEach
                    if (pid != myPid) {
                        try {
                            val cmdline = File(file, "cmdline").readText()
                            if (cmdline.contains("server.py") || cmdline.contains("/opt/agy/agy") || cmdline.contains("agy acp")) {
                                android.system.Os.kill(pid, 9)
                            }
                        } catch (_: Exception) {}
                    }
                }
            }
            val pkillBin = File("$usr/bin/pkill")
            if (pkillBin.exists()) {
                Runtime.getRuntime().exec(arrayOf(pkillBin.absolutePath, "-9", "-f", "server.py")).waitFor()
                Runtime.getRuntime().exec(arrayOf(pkillBin.absolutePath, "-9", "-f", "/opt/agy/agy")).waitFor()
            }
        } catch (_: Exception) {}
    }

    private fun pickAvailablePort(preferred: Int, fallbacks: List<Int>): Int {
        for (port in listOf(preferred) + fallbacks) {
            try {
                ServerSocket().use { socket ->
                    socket.reuseAddress = true
                    socket.bind(java.net.InetSocketAddress("127.0.0.1", port))
                    return port
                }
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
        _authTokenFlow.value = null
        try {
            File("/data/data/$packageName/files/usr/tmp/.auth_token").delete()
        } catch (_: Exception) {}
        try {
            serviceScope.cancel()
        } catch (_: Exception) {}
        try {
            daemonProcess?.destroyForcibly()
            daemonProcess = null
        } catch (_: Exception) {}
        try {
            thread(isDaemon = true, name = "AgyCleanup") {
                cleanupStaleDaemonProcesses()
            }
        } catch (_: Exception) {
            cleanupStaleDaemonProcesses()
        }
        networkCallback?.let {
            val cm = getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
            try { cm.unregisterNetworkCallback(it) } catch (_: Exception) {}
            networkCallback = null
        }
        batteryReceiver?.let {
            try { unregisterReceiver(it) } catch (_: Exception) {}
            batteryReceiver = null
        }
        vaultServerSocket?.let {
            try { it.close() } catch (_: Exception) {}
            vaultServerSocket = null
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

    private fun startVaultBridgeServer(token: String): Int {
        val serverSocket = ServerSocket(0, 50, InetAddress.getByName("127.0.0.1"))
        vaultServerSocket = serverSocket
        val vaultPort = serverSocket.localPort

        serviceScope.launch(Dispatchers.IO) {
            while (isActive && !serverSocket.isClosed) {
                try {
                    val client = serverSocket.accept()
                    launch(Dispatchers.IO) {
                        handleVaultClient(client, token)
                    }
                } catch (_: Exception) {}
            }
        }
        return vaultPort
    }

    private fun handleVaultClient(socket: Socket, expectedToken: String) {
        try {
            socket.soTimeout = 5000
            val reader = BufferedReader(InputStreamReader(socket.getInputStream(), Charsets.UTF_8))
            val writer = BufferedWriter(OutputStreamWriter(socket.getOutputStream(), Charsets.UTF_8))

            val line = reader.readLine() ?: return
            val req = JSONObject(line)
            val token = req.optString("token")
            if (token != expectedToken) {
                val err = JSONObject().apply {
                    put("status", "error")
                    put("message", "Unauthorized")
                }
                writer.write(err.toString() + "\n")
                writer.flush()
                return
            }

            val action = req.optString("action")
            val res = JSONObject()
            when (action) {
                "get_all" -> {
                    res.put("status", "ok")
                    val keysObj = JSONObject()
                    KeystoreVault.getAllDecryptedKeys(this@AgyForegroundService).forEach { (k, v) ->
                        if (k != "__MCP_VAULT__") {
                            keysObj.put(k, v)
                        }
                    }
                    res.put("keys", keysObj)
                }
                "save_key" -> {
                    val name = req.getString("name")
                    val value = req.getString("value")
                    val desc = req.optString("description", "")
                    KeystoreVault.saveKey(this@AgyForegroundService, name, value, desc)
                    res.put("status", "ok")
                }
                "delete_key" -> {
                    val name = req.getString("name")
                    val ok = KeystoreVault.deleteKey(this@AgyForegroundService, name)
                    res.put("status", if (ok) "ok" else "not_found")
                }
                "get_mcp_vault" -> {
                    val mcpJson = KeystoreVault.getDecryptedKey(this@AgyForegroundService, "__MCP_VAULT__") ?: "{}"
                    res.put("status", "ok")
                    res.put("vault", JSONObject(mcpJson))
                }
                "save_mcp_vault" -> {
                    val vaultObj = req.getJSONObject("vault")
                    KeystoreVault.saveKey(this@AgyForegroundService, "__MCP_VAULT__", vaultObj.toString(), "Encrypted MCP Vault")
                    res.put("status", "ok")
                }
                else -> {
                    res.put("status", "error")
                    res.put("message", "Unknown action")
                }
            }
            writer.write(res.toString() + "\n")
            writer.flush()
        } catch (e: Exception) {
            Log.w(TAG, "Vault client error: ${e.message}")
        } finally {
            try { socket.close() } catch (_: Exception) {}
        }
    }

    fun reconcileSymlinkFarmAndLock(context: Context) {
        val filesDir = context.filesDir
        val usrBinDir = File(filesDir, "usr/bin")
        File(filesDir, "home/.local/bin").mkdirs()
        val nativeLibDir = File(context.applicationInfo.nativeLibraryDir)

        usrBinDir.mkdirs()
        // 1. Temporarily unlock usr/bin (0700) so we can replace dangling symlinks after an APK upgrade
        android.system.Os.chmod(usrBinDir.absolutePath, 0b111_000_000)

        val symlinkMap = mapOf(
            "agy" to "libagy_launcher.so",
            "agy.va39" to "libagy_launcher.so",
            "bash" to "libbash.so",
            "sh" to "libbash.so",
            "git" to "libgit.so",
            "python3" to "libpython3_bin.so",
            "ssh" to "libssh_bin.so",
            "ssh-keygen" to "libssh_keygen.so",
            "ask_question" to "libask_question.so",
            "proot" to "libproot.so",
            "agy-sh" to "libagy_sh.so",
            "apk" to "libagy_sh.so",
            "pkg" to "libagy_sh.so",
            "node" to "libagy_sh.so",
            "nodejs" to "libagy_sh.so",
            "npm" to "libagy_sh.so",
            "npx" to "libagy_sh.so",
            "cargo" to "libagy_sh.so",
            "rustc" to "libagy_sh.so",
            "gcc" to "libagy_sh.so",
            "g++" to "libagy_sh.so",
            "clang" to "libagy_sh.so",
            "clang++" to "libagy_sh.so",
            "make" to "libagy_sh.so",
            "php" to "libagy_sh.so",
            "go" to "libagy_sh.so",
            "java" to "libagy_sh.so",
            "javac" to "libagy_sh.so"
        )

        for ((binName, soName) in symlinkMap) {
            val targetSo = File(nativeLibDir, soName)
            val linkFile = File(usrBinDir, binName)
            try {
                // Unconditionally remove first: File.exists() returns false on dangling symlinks!
                android.system.Os.remove(linkFile.absolutePath)
            } catch (_: Exception) {}
            try {
                android.system.Os.symlink(targetSo.absolutePath, linkFile.absolutePath)
            } catch (_: Exception) {}
        }

        // 2. Lock usr/bin to 0500 (dr-x------): kernel VFS blocks raw Go SVC #0 unlinkat/renameat2!
        android.system.Os.chmod(usrBinDir.absolutePath, 0b101_000_000)
    }

    override fun onDestroy() {
        stopServiceGracefully()
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null
}
