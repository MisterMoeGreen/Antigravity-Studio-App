# Specification 04: In-App Update Engine & Unattended Install UX (v1.0.4)

## 1. Overview & Problem Definition

In `v1.0.2` and `v1.0.3`, update notifications and installation suffered from three critical platform constraints identified during the Argon review:

1. **Android 14/15 (`targetSdk 35`) Explicit `FLAG_MUTABLE` Requirement (ARGON-05)**: Creating a mutable `PendingIntent` with an implicit `Intent` triggers an immediate `IllegalArgumentException` on Android 14/15. The `PendingIntent` must use an explicit component `Intent`.
2. **`/data/app/~~<hash>/` Rotation & Dangling Symlinks (ARGON-03)**: On upgrade, Android generates a new randomized hash directory for `nativeLibraryDir` and deletes the old one. All symlinks in `files/usr/bin/` become dangling pointers.
3. **Process Termination Recovery**: An in-place package update kills all processes in the package (`SIGKILL`). The app must declare an `android.intent.action.MY_PACKAGE_REPLACED` receiver to recover.

---

## 2. In-App Update Manager (`InAppUpdateManager.kt`)

```kotlin
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
                // Enforce silent unattended installation on Android 12+ (API 31+)
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
}
```

---

## 3. Package Replacement Recovery & Receiver (`UpdateReceiver.kt`)

Declared in `AndroidManifest.xml`:
```xml
<uses-permission android:name="android.permission.REQUEST_INSTALL_PACKAGES" />
<uses-permission android:name="android.permission.UPDATE_PACKAGES_WITHOUT_USER_ACTION" />

<receiver android:name=".UpdateReceiver" android:exported="false">
    <intent-filter>
        <action android:name="android.intent.action.MY_PACKAGE_REPLACED" />
        <action android:name="ai.agy.app.ACTION_INSTALL_COMMIT" />
    </intent-filter>
</receiver>
```

Implementation in `UpdateReceiver.kt`:
```kotlin
package ai.agy.app

import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageInstaller
import android.os.Build

class UpdateReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        when (intent.action) {
            Intent.ACTION_MY_PACKAGE_REPLACED -> {
                // Recover foreground service after in-place upgrade
                val serviceIntent = Intent(context, AgyForegroundService::class.java)
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                    context.startForegroundService(serviceIntent)
                } else {
                    context.startService(serviceIntent)
                }
            }
            "ai.agy.app.ACTION_INSTALL_COMMIT" -> {
                val status = intent.getIntExtra(PackageInstaller.EXTRA_STATUS, PackageInstaller.STATUS_FAILURE)
                if (status == PackageInstaller.STATUS_PENDING_USER_ACTION) {
                    val confirmIntent = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                        intent.getParcelableExtra(Intent.EXTRA_INTENT, Intent::class.java)
                    } else {
                        @Suppress("DEPRECATION")
                        intent.getParcelableExtra(Intent.EXTRA_INTENT)
                    }
                    confirmIntent?.apply {
                        addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                        context.startActivity(this)
                    }
                }
            }
        }
    }
}
```

---

## 4. Verification Gates

### Verification Gate AC-4.1: Android 14/15 Intent & Permission Compliance
```bash
grep -q "UPDATE_PACKAGES_WITHOUT_USER_ACTION" app/src/main/AndroidManifest.xml && \
grep -q "MY_PACKAGE_REPLACED" app/src/main/AndroidManifest.xml && \
grep -q "ACTION_INSTALL_COMMIT" app/src/main/java/ai/agy/app/InAppUpdateManager.kt
```
