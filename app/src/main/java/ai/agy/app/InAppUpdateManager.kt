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
}
