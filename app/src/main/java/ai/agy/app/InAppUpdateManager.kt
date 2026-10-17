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

        // 1. Download APK with progress tracking
        val connection = URL(apkDownloadUrl).openConnection()
        val totalLength = connection.contentLength
        connection.getInputStream().use { input ->
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

            val intent = Intent(context, UpdateReceiver::class.java)
            val pendingIntent = PendingIntent.getBroadcast(
                context,
                sessionId,
                intent,
                PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_MUTABLE
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
     * Avoids infinite update prompt loops via semantic version comparison and 24h throttling.
     */
    suspend fun checkForUpdates(context: Context) = withContext(Dispatchers.IO) {
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

                val pInfo = context.packageManager.getPackageInfo(context.packageName, 0)
                val currentVersion = pInfo.versionName

                if (!isNewerVersion(rawTagName, currentVersion)) {
                    android.util.Log.d(TAG, "Installed version ($currentVersion) is up-to-date with remote release ($rawTagName)")
                    return@withContext
                }

                // Check 24-hour throttling to prevent repeated prompts on every app start
                val prefs = context.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE)
                val lastAttemptedTag = prefs.getString(KEY_LAST_ATTEMPTED_TAG, "")
                val lastAttemptedTime = prefs.getLong(KEY_LAST_ATTEMPTED_TIME, 0L)
                val now = System.currentTimeMillis()

                if (lastAttemptedTag == rawTagName && (now - lastAttemptedTime) < 24 * 3600 * 1000L) {
                    android.util.Log.d(TAG, "Update for $rawTagName already attempted recently; throttling")
                    return@withContext
                }

                val assets = jsonObj.optJSONArray("assets")
                var downloadUrl: String? = null
                if (assets != null) {
                    for (i in 0 until assets.length()) {
                        val asset = assets.getJSONObject(i)
                        if (asset.optString("name").endsWith(".apk")) {
                            downloadUrl = asset.optString("browser_download_url")
                            break
                        }
                    }
                }

                if (downloadUrl != null) {
                    android.util.Log.i(TAG, "New version detected ($rawTagName > $currentVersion). Starting download...")
                    prefs.edit()
                        .putString(KEY_LAST_ATTEMPTED_TAG, rawTagName)
                        .putLong(KEY_LAST_ATTEMPTED_TIME, now)
                        .apply()

                    downloadAndInstall(context, downloadUrl) { progress ->
                        android.util.Log.i(TAG, "Download progress: $progress%")
                    }
                }
            }
        } catch (e: Exception) {
            android.util.Log.w(TAG, "Update check failed: ${e.message}")
        }
    }
}
