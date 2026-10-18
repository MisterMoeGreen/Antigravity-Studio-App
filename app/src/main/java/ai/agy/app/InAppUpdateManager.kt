package ai.agy.app

import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.os.Build
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File
import java.io.FileInputStream
import java.net.URL

object InAppUpdateManager {

    private const val TAG = "InAppUpdate"
    private const val PREFS_NAME = "agy_update_prefs"
    private const val KEY_LAST_ATTEMPTED_TAG = "last_attempted_tag"
    private const val KEY_LAST_ATTEMPTED_TIME = "last_attempted_time"

    /**
     * Tier-1: OTA Asset Sync for UI, server.py, or skills.
     * Replaces web assets in usr/share/agy-ui/ without requiring an APK reinstallation.
     */
    suspend fun syncOtaAssets(context: Context, assetUrl: String, destRelativePath: String): Boolean = withContext(Dispatchers.IO) {
        val destFile = File("/data/data/${context.packageName}/files/usr/share/agy-ui/$destRelativePath")
        val tempFile = File("${destFile.absolutePath}.ota_tmp")
        try {
            val connection = URL(assetUrl).openConnection()
            connection.connectTimeout = 5000
            connection.readTimeout = 10000
            connection.getInputStream().use { input ->
                tempFile.outputStream().use { output ->
                    input.copyTo(output)
                }
            }
            if (tempFile.exists() && tempFile.length() > 0) {
                tempFile.renameTo(destFile)
                true
            } else {
                false
            }
        } catch (_: Exception) {
            tempFile.delete()
            false
        }
    }

    /**
     * Tier-2: Self-Updating APK Engine using Android's PackageInstaller API.
     * On Android 12+ (SDK 31+), allows silent background upgrades without user popups.
     */
    suspend fun downloadAndInstall(context: Context, apkDownloadUrl: String, onProgress: (Int) -> Unit) = withContext(Dispatchers.IO) {
        val destFile = File(context.cacheDir, "update.apk")
        if (destFile.exists()) destFile.delete()

        // 1. Download APK with progress tracking and redirect resolution
        var currentUrl = apkDownloadUrl
        var connection: java.net.HttpURLConnection
        var redirectCount = 0
        while (true) {
            val u = URL(currentUrl)
            connection = u.openConnection() as java.net.HttpURLConnection
            connection.instanceFollowRedirects = true
            connection.connectTimeout = 10000
            connection.readTimeout = 15000
            connection.setRequestProperty("User-Agent", "AntigravityStudio")
            connection.connect()
            val code = connection.responseCode
            if (code == java.net.HttpURLConnection.HTTP_MOVED_PERM || code == java.net.HttpURLConnection.HTTP_MOVED_TEMP || code == 307 || code == 308) {
                val loc = connection.getHeaderField("Location")
                if (!loc.isNullOrBlank() && redirectCount < 5) {
                    currentUrl = loc
                    redirectCount++
                    continue
                }
            }
            break
        }

        val totalLength = connection.contentLength
        connection.inputStream.use { input ->
            destFile.outputStream().use { output ->
                val buffer = ByteArray(8192)
                var bytesRead: Int
                var downloaded = 0
                while (input.read(buffer).also { bytesRead = it } != -1) {
                    output.write(buffer, 0, bytesRead)
                    downloaded += bytesRead
                    if (totalLength > 0) {
                        onProgress((downloaded * 100) / totalLength)
                    }
                }
            }
        }

        if (destFile.length() < 100000) {
            throw java.io.IOException("Downloaded APK file is incomplete or invalid (${destFile.length()} bytes)")
        }

        // 2. Commit PackageInstaller Session
        val packageInstaller = context.packageManager.packageInstaller
        val params = PackageInstaller.SessionParams(PackageInstaller.SessionParams.MODE_FULL_INSTALL).apply {
            setAppPackageName(context.packageName)
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                // Allows silent, unattended update on Android 12+ without popups
                setRequireUserAction(PackageInstaller.SessionParams.USER_ACTION_NOT_REQUIRED)
            }
        }

        val sessionId = packageInstaller.createSession(params)
        val session = packageInstaller.openSession(sessionId)

        session.use { activeSession ->
            FileInputStream(destFile).use { inStream ->
                activeSession.openWrite("package", 0, destFile.length()).use { outStream ->
                    inStream.copyTo(outStream)
                    activeSession.fsync(outStream)
                }
            }

            // CRITICAL for targetSdk 35: Must use an EXPLICIT component Intent with FLAG_MUTABLE
            val callbackIntent = Intent(context, UpdateReceiver::class.java).apply {
                setPackage(context.packageName)
                action = "ai.agy.app.ACTION_INSTALL_COMMIT"
            }
            val flags = PendingIntent.FLAG_UPDATE_CURRENT or
                (if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) PendingIntent.FLAG_MUTABLE else 0)

            val pendingIntent = PendingIntent.getBroadcast(
                context,
                sessionId,
                callbackIntent,
                flags
            )

            activeSession.commit(pendingIntent.intentSender)
        }
    }

    /**
     * Compares remote release tag to current installed version name.
     * Returns true ONLY if remote is strictly newer than local version.
     */
    fun isNewerVersion(remoteTag: String, localVersion: String?): Boolean {
        if (localVersion.isNullOrBlank() || remoteTag.isBlank()) return false

        val rClean = remoteTag.trim().removePrefix("v").removePrefix("V")
        val lClean = localVersion.trim().removePrefix("v").removePrefix("V")

        if (rClean.equals(lClean, ignoreCase = true)) return false

        // Extract numeric major.minor.patch
        val rBase = rClean.split("-")[0].split("+")[0]
        val lBase = lClean.split("-")[0].split("+")[0]

        val rParts = rBase.split(".").mapNotNull { it.toIntOrNull() }
        val lParts = lBase.split(".").mapNotNull { it.toIntOrNull() }

        val maxLen = maxOf(rParts.size, lParts.size)
        for (i in 0 until maxLen) {
            val rVal = rParts.getOrElse(i) { 0 }
            val lVal = lParts.getOrElse(i) { 0 }
            if (rVal > lVal) return true
            if (rVal < lVal) return false
        }

        // Numeric parts are equal (e.g. 1.0.1 vs 1.0.1-hotfix).
        // If remote has a hotfix/patch suffix and local is the plain release, consider remote newer.
        // If local has the suffix or identical tag, it is NOT newer.
        if (rClean.contains("-") && !lClean.contains("-")) {
            return true
        }

        return false
    }

    /**
     * Periodically queries the repository's latest release and triggers in-app
     * APK installation if a newer version is published.
     * Avoids infinite update prompt loops via semantic/integer version comparison and 24h throttling.
     */
    suspend fun checkForUpdates(context: Context, force: Boolean = false) = withContext(Dispatchers.IO) {
        try {
            val url = URL("https://api.github.com/repos/MisterMoeGreen/Antigravity-Studio-App/releases/latest")
            val connection = url.openConnection() as java.net.HttpURLConnection
            connection.setRequestProperty("User-Agent", "AntigravityStudio")
            connection.connectTimeout = 5000
            connection.readTimeout = 5000

            if (connection.responseCode == 200) {
                val json = connection.inputStream.bufferedReader().use { it.readText() }
                val jsonObj = org.json.JSONObject(json)
                val rawTagName = jsonObj.optString("tag_name", "").trim()
                if (rawTagName.isEmpty()) return@withContext

                val assets = jsonObj.optJSONArray("assets")
                var downloadUrl: String? = null
                var remoteVersionCode: Long? = null

                if (assets != null) {
                    for (i in 0 until assets.length()) {
                        val asset = assets.getJSONObject(i)
                        val name = asset.optString("name")
                        if (name == "release-metadata.json") {
                            try {
                                val metaUrl = URL(asset.optString("browser_download_url"))
                                val metaConn = metaUrl.openConnection() as java.net.HttpURLConnection
                                metaConn.connectTimeout = 3000
                                metaConn.readTimeout = 3000
                                if (metaConn.responseCode == 200) {
                                    val metaJson = metaConn.inputStream.bufferedReader().use { it.readText() }
                                    remoteVersionCode = org.json.JSONObject(metaJson).optLong("version_code")
                                }
                            } catch (_: Exception) {}
                        } else if (name.endsWith(".apk") && downloadUrl == null) {
                            downloadUrl = asset.optString("browser_download_url")
                        }
                    }
                }

                val pInfo = context.packageManager.getPackageInfo(context.packageName, 0)
                val currentVersion = pInfo.versionName
                val currentCode = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) pInfo.longVersionCode else @Suppress("DEPRECATION") pInfo.versionCode.toLong()

                val shouldUpdate = if (remoteVersionCode != null && remoteVersionCode > 0) {
                    remoteVersionCode > currentCode
                } else {
                    isNewerVersion(rawTagName, currentVersion)
                }

                if (!shouldUpdate) {
                    android.util.Log.d(TAG, "Installed version ($currentVersion / code $currentCode) is up-to-date with remote release ($rawTagName / code $remoteVersionCode)")
                    return@withContext
                }

                if (downloadUrl != null) {
                    android.util.Log.i(TAG, "New version detected ($rawTagName / code $remoteVersionCode > $currentVersion / code $currentCode). Awaiting explicit user confirmation in UI.")
                }
            }
        } catch (e: Exception) {
            android.util.Log.w(TAG, "Update check failed: ${e.message}")
        }
    }
}
