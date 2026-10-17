# Specification 04: Hardened Android Application Infrastructure

## 1. Overview & Component Architecture

The Android app container is built with modern Kotlin and Android Jetpack libraries. It is composed of three core subsystems:
1. **`EnvironmentBootstrapper`**: Extracts `assets/usr-data.tar.zst` on cold start and creates the symlink farm linking `/data/data/ai.agy.app/files/usr/` to `context.applicationInfo.nativeLibraryDir` (including all Glibc, Bionic, and Python C-extensions from `.symlinks.manifest`).
2. **`AgyForegroundService`**: Manages the background process lifecycle, acquires an Android `PARTIAL_WAKE_LOCK`, drains process stdout/stderr pipes to prevent kernel buffer deadlocks, synchronizes DNS and battery telemetry, and supervises `server.py`.
3. **`MainActivity`**: Hosts a full-screen, hardware-accelerated `android.webkit.WebView`, handles file uploads via `WebChromeClient.onShowFileChooser()`, and catches Google OAuth redirects.

---

## 2. Manifest (`app/src/main/AndroidManifest.xml`)

```xml
<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android"
    xmlns:tools="http://schemas.android.com/tools">

    <uses-permission android:name="android.permission.INTERNET" />
    <uses-permission android:name="android.permission.ACCESS_NETWORK_STATE" />
    <uses-permission android:name="android.permission.WAKE_LOCK" />
    <uses-permission android:name="android.permission.FOREGROUND_SERVICE" />
    <uses-permission android:name="android.permission.FOREGROUND_SERVICE_SPECIAL_USE" />
    <uses-permission android:name="android.permission.POST_NOTIFICATIONS" />
    <uses-permission android:name="android.permission.REQUEST_INSTALL_PACKAGES" />

    <application
        android:label="Antigravity Studio"
        android:icon="@mipmap/ic_launcher"
        android:roundIcon="@mipmap/ic_launcher_round"
        android:extractNativeLibs="true"
        android:usesCleartextTraffic="true"
        android:theme="@style/Theme.AppCompat.DayNight.NoActionBar">

        <activity
            android:name=".MainActivity"
            android:exported="true"
            android:configChanges="orientation|screenSize|keyboardHidden|uiMode"
            android:windowSoftInputMode="adjustResize">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>

        <service
            android:name=".AgyForegroundService"
            android:enabled="true"
            android:exported="false"
            android:foregroundServiceType="specialUse">
            <property
                android:name="android.app.PROPERTY_SPECIAL_USE_FGS_SUBTYPE"
                android:value="Local autonomous coding agent daemon and WebSocket orchestrator" />
        </service>

        <receiver
            android:name=".UpdateReceiver"
            android:exported="false" />

        <provider
            android:name="androidx.core.content.FileProvider"
            android:authorities="ai.agy.app.fileprovider"
            android:exported="false"
            android:grantUriPermissions="true">
            <meta-data
                android:name="android.support.FILE_PROVIDER_PATHS"
                android:resource="@xml/file_paths" />
        </provider>

    </application>
</manifest>
```

### FileProvider Paths Configuration (`app/src/main/res/xml/file_paths.xml`)

```xml
<?xml version="1.0" encoding="utf-8"?>
<paths xmlns:android="http://schemas.android.com/apk/res/android">
    <files-path name="internal_files" path="." />
    <cache-path name="internal_cache" path="." />
    <external-files-path name="external_files" path="." />
</paths>
```

---

## 3. Bootstrapper (`app/src/main/java/ai/agy/app/EnvironmentBootstrapper.kt`)

```kotlin
package ai.agy.app

import android.content.Context
import android.os.Build
import android.system.Os
import com.github.luben.zstd.ZstdInputStream
import org.apache.commons.compress.archivers.tar.TarArchiveInputStream
import java.io.File
import java.io.FileOutputStream

object EnvironmentBootstrapper {

    private val BIN_SYMLINKS = mapOf(
        "agy" to "libagy.so",
        "bash" to "libbash.so",
        "sh" to "libbash.so",
        "busybox" to "libbusybox.so",
        "git" to "libgit.so",
        "git-remote-https" to "libgit_remote_https.so",
        "git-remote-http" to "libgit_remote_https.so",
        "rg" to "librg.so",
        "curl" to "libcurl_bin.so",
        "python3" to "libpython3_bin.so",
        "python3.12" to "libpython3_bin.so",
        "python" to "libpython3_bin.so"
    )

    private val GIT_CORE_SYMLINKS = mapOf(
        "git" to "libgit.so",
        "git-remote-https" to "libgit_remote_https.so",
        "git-remote-http" to "libgit_remote_https.so",
        "git-remote-ftp" to "libgit_remote_https.so",
        "git-remote-ftps" to "libgit_remote_https.so"
    )

    private val BUSYBOX_APPLETS = listOf(
        "[", "[[", "awk", "base64", "basename", "cat", "chmod", "chown", "cmp",
        "comm", "cp", "cut", "date", "dd", "df", "diff", "dirname", "dos2unix",
        "du", "echo", "egrep", "env", "expr", "false", "fgrep", "find", "fold",
        "free", "grep", "gunzip", "gzip", "head", "hostname", "id", "install",
        "kill", "killall", "less", "ln", "ls", "md5sum", "mkdir", "mktemp",
        "more", "mv", "nc", "netstat", "nice", "nohup", "nproc", "od", "paste",
        "patch", "pgrep", "pidof", "pkill", "printenv", "printf", "ps", "pwd",
        "readlink", "realpath", "rev", "rm", "rmdir", "sed", "seq", "setsid",
        "sha256sum", "shuf", "sleep", "sort", "split", "stat", "strings", "stty",
        "sync", "tac", "tail", "tar", "tee", "test", "time", "timeout", "top",
        "touch", "tr", "true", "truncate", "tty", "uname", "uniq", "unix2dos",
        "unlink", "unzip", "uptime", "usleep", "vi", "watch", "wc", "wget",
        "which", "whoami", "xargs", "xxd", "xz", "yes", "zcat"
    )

    fun initialize(context: Context) {
        val filesDir = File("/data/data/${context.packageName}/files")
        val usrDir = File(filesDir, "usr")
        val homeDir = File(filesDir, "home")
        val workspaceDir = File(homeDir, "workspace")
        val glibcDir = File(usrDir, "glibc")
        val glibcLibDir = File(glibcDir, "lib")
        val usrLibDir = File(usrDir, "lib")
        val binDir = File(usrDir, "bin")
        val gitCoreDir = File(usrDir, "libexec/git-core")
        val etcDir = File(usrDir, "etc")
        val tmpDir = File(usrDir, "tmp")

        listOf(usrDir, homeDir, workspaceDir, glibcLibDir, usrLibDir, binDir, gitCoreDir, etcDir, tmpDir).forEach {
            it.mkdirs()
        }

        // 1. Unpack assets/usr-data.tar.zst on first launch or version upgrade
        val versionFile = File(usrDir, ".version_code")
        val pkgInfo = context.packageManager.getPackageInfo(context.packageName, 0)
        val currentVersion = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            pkgInfo.longVersionCode
        } else {
            @Suppress("DEPRECATION") pkgInfo.versionCode.toLong()
        }
        val installedVersion = if (versionFile.exists()) versionFile.readText().trim().toLongOrNull() else -1L

        if (installedVersion != currentVersion) {
            extractZstdTarAsset(context, "usr-data.tar.zst", usrDir)
            versionFile.writeText(currentVersion.toString())
        }

        // 2. Refresh symlinks to application nativeLibraryDir (< 5 ms)
        val nativeDir = context.applicationInfo.nativeLibraryDir

        // Reconstruct all SONAME, Glibc, and Python lib-dynload symlinks from manifest
        val manifestFile = File(usrDir, ".symlinks.manifest")
        if (manifestFile.exists()) {
            manifestFile.forEachLine { line ->
                val parts = line.trim().split("|")
                if (parts.size == 2) {
                    val relLink = parts[0]
                    val targetSo = parts[1]
                    val linkFile = File(usrDir, relLink)
                    linkFile.parentFile?.mkdirs()
                    createOrUpdateSymlink(linkFile, "$nativeDir/$targetSo")
                }
            }
        }

        BIN_SYMLINKS.forEach { (binName, targetSo) ->
            createOrUpdateSymlink(File(binDir, binName), "$nativeDir/$targetSo")
        }

        GIT_CORE_SYMLINKS.forEach { (binName, targetSo) ->
            createOrUpdateSymlink(File(gitCoreDir, binName), "$nativeDir/$targetSo")
        }

        BUSYBOX_APPLETS.forEach { applet ->
            if (!BIN_SYMLINKS.containsKey(applet)) {
                createOrUpdateSymlink(File(binDir, applet), "$nativeDir/libbusybox.so")
            }
        }

        // Symlink usr/glibc/etc -> usr/etc so Termux Glibc finds resolv.conf & nsswitch.conf
        createOrUpdateSymlink(File(glibcDir, "etc"), etcDir.absolutePath)

        // 3. Guarantee base networking and Git config files exist
        val nsswitch = File(etcDir, "nsswitch.conf")
        if (!nsswitch.exists()) {
            nsswitch.writeText("hosts: files dns\n")
        }
        val hosts = File(etcDir, "hosts")
        if (!hosts.exists()) {
            hosts.writeText("127.0.0.1 localhost\n::1 localhost\n")
        }
        val resolv = File(etcDir, "resolv.conf")
        if (!resolv.exists()) {
            resolv.writeText("nameserver 8.8.8.8\nnameserver 1.1.1.1\noptions timeout:2 attempts:3\n")
        }
        val sysGitConfig = File(etcDir, "gitconfig")
        if (!sysGitConfig.exists()) {
            sysGitConfig.writeText(
                "[http]\n\tsslCAInfo = ${usrDir.absolutePath}/etc/tls/cert.pem\n" +
                "[safe]\n\tdirectory = *\n"
            )
        }
        val userGitConfig = File(homeDir, ".gitconfig")
        if (!userGitConfig.exists()) {
            userGitConfig.writeText(
                "[user]\n\tname = Antigravity Agent\n\temail = agent@ai.agy.app\n" +
                "[init]\n\tdefaultBranch = main\n"
            )
        }
    }

    private fun createOrUpdateSymlink(linkFile: File, targetPath: String) {
        try {
            if (linkFile.isDirectory && !java.nio.file.Files.isSymbolicLink(linkFile.toPath())) {
                linkFile.deleteRecursively()
            } else {
                Os.unlink(linkFile.absolutePath)
            }
        } catch (_: Exception) {}
        try {
            Os.symlink(targetPath, linkFile.absolutePath)
        } catch (e: Exception) {
            e.printStackTrace()
        }
    }

    private fun extractZstdTarAsset(context: Context, assetName: String, destDir: File) {
        val canonicalDest = destDir.canonicalPath
        context.assets.open(assetName).use { rawIn ->
            ZstdInputStream(rawIn).use { zstdIn ->
                TarArchiveInputStream(zstdIn).use { tarIn ->
                    var entry = tarIn.nextTarEntry
                    while (entry != null) {
                        val outFile = File(destDir, entry.name)
                        // Zip slip protection
                        if (!outFile.canonicalPath.startsWith(canonicalDest)) {
                            entry = tarIn.nextTarEntry
                            continue
                        }
                        if (entry.isDirectory) {
                            outFile.mkdirs()
                        } else if (entry.isSymbolicLink) {
                            createOrUpdateSymlink(outFile, entry.linkName)
                        } else {
                            outFile.parentFile?.mkdirs()
                            FileOutputStream(outFile).use { out ->
                                tarIn.copyTo(out)
                            }
                            if ((entry.mode and 0b001000000) != 0) {
                                outFile.setExecutable(true, false)
                            }
                        }
                        entry = tarIn.nextTarEntry
                    }
                }
            }
        }
    }
}
```

---

## 4. Foreground Service (`app/src/main/java/ai/agy/app/AgyForegroundService.kt`)

```kotlin
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
    private var daemonProcess: Process? = null
    private val serviceScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var networkCallback: ConnectivityManager.NetworkCallback? = null
    private var batteryReceiver: BroadcastReceiver? = null

    companion object {
        const val TAG = "AgyService"
        const val CHANNEL_ID = "agy_daemon_channel"
        const val NOTIFICATION_ID = 2001

        private val _readyPortFlow = MutableStateFlow<Int?>(null)
        val readyPortFlow: StateFlow<Int?> = _readyPortFlow.asStateFlow()
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

        val notification = Notification.Builder(this, CHANNEL_ID)
            .setContentTitle("Antigravity Studio Running")
            .setContentText("Local AI coding agent active on port :$port")
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentIntent(pendingIntent)
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
        env["PYTHONHOME"] = usr
        env["PYTHONPATH"] = "$usr/lib/python3.12:$usr/lib/python3.12/site-packages"

        daemonProcess = pb.redirectErrorStream(true).start()
        Log.i(TAG, "Spawned server.py process on 127.0.0.1:$port")

        // CRITICAL: Continuously drain stdout/stderr pipe to prevent 64KB kernel buffer deadlocks
        serviceScope.launch {
            val stream = daemonProcess?.inputStream ?: return@launch
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

    override fun onDestroy() {
        serviceScope.cancel()
        daemonProcess?.destroyForcibly()
        networkCallback?.let {
            val cm = getSystemService(Context.CONNECTIVITY_SERVICE) as ConnectivityManager
            try { cm.unregisterNetworkCallback(it) } catch (_: Exception) {}
        }
        batteryReceiver?.let {
            try { unregisterReceiver(it) } catch (_: Exception) {}
        }
        wakeLock?.let { if (it.isHeld) it.release() }
        super.onDestroy()
    }

    override fun onBind(intent: Intent?): IBinder? = null
}
```

---

## 5. Main Activity (`app/src/main/java/ai/agy/app/MainActivity.kt`)

```kotlin
package ai.agy.app

import android.annotation.SuppressLint
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.webkit.*
import androidx.activity.OnBackPressedCallback
import androidx.activity.result.contract.ActivityResultContracts
import androidx.appcompat.app.AppCompatActivity
import androidx.browser.customtabs.CustomTabsIntent
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.*
import kotlinx.coroutines.flow.filterNotNull
import kotlinx.coroutines.flow.first
import java.net.HttpURLConnection
import java.net.URL

class MainActivity : AppCompatActivity() {

    private lateinit var webView: WebView
    private var filePathCallback: ValueCallback<Array<Uri>>? = null

    private val fileChooserLauncher = registerForActivityResult(
        ActivityResultContracts.StartActivityForResult()
    ) { result ->
        val callback = filePathCallback ?: return@registerForActivityResult
        filePathCallback = null
        val uris = WebChromeClient.FileChooserParams.parseResult(result.resultCode, result.data)
        callback.onReceiveValue(uris)
    }

    private val notificationPermissionLauncher = registerForActivityResult(
        ActivityResultContracts.RequestPermission()
    ) { /* Notification permission status */ }

    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)

        if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.TIRAMISU) {
            notificationPermissionLauncher.launch(android.Manifest.permission.POST_NOTIFICATIONS)
        }

        val serviceIntent = Intent(this, AgyForegroundService::class.java)
        startForegroundService(serviceIntent)

        webView = WebView(this).apply {
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            settings.databaseEnabled = true
            settings.allowFileAccess = false
            settings.allowContentAccess = true
            settings.mediaPlaybackRequiresUserGesture = false

            webViewClient = object : WebViewClient() {
                override fun shouldOverrideUrlLoading(view: WebView?, request: WebResourceRequest?): Boolean {
                    val url = request?.url?.toString() ?: return false
                    if (url.startsWith("https://accounts.google.com/o/oauth2")) {
                        CustomTabsIntent.Builder().setShowTitle(true).build()
                            .launchUrl(this@MainActivity, Uri.parse(url))
                        return true
                    }
                    return false
                }
            }

            webChromeClient = object : WebChromeClient() {
                override fun onShowFileChooser(
                    webView: WebView?,
                    filePathCallback: ValueCallback<Array<Uri>>?,
                    fileChooserParams: FileChooserParams?
                ): Boolean {
                    this@MainActivity.filePathCallback?.onReceiveValue(null)
                    this@MainActivity.filePathCallback = filePathCallback
                    return try {
                        val intent = fileChooserParams?.createIntent() ?: Intent(Intent.ACTION_GET_CONTENT).apply {
                            addCategory(Intent.CATEGORY_OPENABLE)
                            type = "*/*"
                        }
                        fileChooserLauncher.launch(intent)
                        true
                    } catch (_: Exception) {
                        this@MainActivity.filePathCallback = null
                        false
                    }
                }
            }
        }
        setContentView(webView)

        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() {
                if (webView.canGoBack()) {
                    webView.goBack()
                } else {
                    isEnabled = false
                    onBackPressedDispatcher.onBackPressed()
                }
            }
        })

        // Await until EnvironmentBootstrapper completes and AgyForegroundService publishes the bound port
        lifecycleScope.launch(Dispatchers.IO) {
            val port = AgyForegroundService.readyPortFlow.filterNotNull().first()
            val healthUrl = "http://127.0.0.1:$port/health"
            for (i in 1..100) {
                try {
                    val conn = URL(healthUrl).openConnection() as HttpURLConnection
                    conn.connectTimeout = 300
                    conn.readTimeout = 300
                    if (conn.responseCode == 200) break
                } catch (_: Exception) {}
                delay(100)
            }
            withContext(Dispatchers.Main) {
                webView.loadUrl("http://127.0.0.1:$port/")
            }
        }
    }
}
```
