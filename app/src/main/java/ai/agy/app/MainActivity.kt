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
        checkStoragePermission()

        val serviceIntent = Intent(this, AgyForegroundService::class.java)
        startForegroundService(serviceIntent)

        // Non-blocking background check for repository updates disabled in v1.1.0 in favor of on-demand check in Settings Hub

        webView = WebView(this).apply {
            settings.javaScriptEnabled = true
            settings.domStorageEnabled = true
            settings.databaseEnabled = true
            settings.allowFileAccess = false
            settings.allowContentAccess = true
            settings.mediaPlaybackRequiresUserGesture = false
            addJavascriptInterface(AndroidAppInterface(), "AndroidApp")

            webViewClient = object : WebViewClient() {
                override fun shouldOverrideUrlLoading(view: WebView?, request: WebResourceRequest?): Boolean {
                    val uri = request?.url ?: return false
                    val host = uri.host ?: ""
                    // Strictly allow local daemon navigation within the WebView
                    if (host == "127.0.0.1" || host == "localhost") {
                        return false
                    }
                    // Prevent external websites from loading in WebView and accessing @JavascriptInterface.
                    // Instead, launch all external links safely in Chrome Custom Tabs or external browser.
                    try {
                        CustomTabsIntent.Builder().setShowTitle(true).build()
                            .launchUrl(this@MainActivity, uri)
                    } catch (_: Exception) {
                        try {
                            startActivity(Intent(Intent.ACTION_VIEW, uri))
                        } catch (_: Exception) {}
                    }
                    return true
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
                webView.evaluateJavascript("(function(){ try { return Boolean(window.handleAndroidBack && window.handleAndroidBack()); } catch(e) { return false; } })();") { result ->
                    val handled = result?.trim()?.equals("true", ignoreCase = true) == true
                    if (!handled) {
                        if (webView.canGoBack()) {
                            webView.goBack()
                        } else {
                            isEnabled = false
                            onBackPressedDispatcher.onBackPressed()
                            isEnabled = true
                        }
                    }
                }
            }
        })

        // Await until EnvironmentBootstrapper completes and AgyForegroundService publishes the bound port and token
        lifecycleScope.launch(Dispatchers.IO) {
            val port = AgyForegroundService.readyPortFlow.filterNotNull().first()
            val token = AgyForegroundService.authTokenFlow.filterNotNull().first()
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
                webView.loadUrl("http://127.0.0.1:$port/?auth=$token")
            }
        }
    }

    private fun checkStoragePermission() {
        if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.R) {
            if (!android.os.Environment.isExternalStorageManager()) {
                try {
                    val intent = Intent(android.provider.Settings.ACTION_MANAGE_APP_ALL_FILES_ACCESS_PERMISSION).apply {
                        data = Uri.parse("package:$packageName")
                    }
                    startActivity(intent)
                } catch (_: Exception) {
                    try {
                        val intent = Intent(android.provider.Settings.ACTION_MANAGE_ALL_FILES_ACCESS_PERMISSION)
                        startActivity(intent)
                    } catch (_: Exception) {}
                }
            }
        } else if (android.os.Build.VERSION.SDK_INT >= android.os.Build.VERSION_CODES.M) {
            if (checkSelfPermission(android.Manifest.permission.WRITE_EXTERNAL_STORAGE) != android.content.pm.PackageManager.PERMISSION_GRANTED) {
                requestPermissions(arrayOf(android.Manifest.permission.READ_EXTERNAL_STORAGE, android.Manifest.permission.WRITE_EXTERNAL_STORAGE), 101)
            }
        }
    }

    inner class AndroidAppInterface {
        @JavascriptInterface
        fun triggerUpdateCheck(force: Boolean) {
            lifecycleScope.launch(Dispatchers.IO) {
                InAppUpdateManager.checkForUpdates(this@MainActivity, force = force)
            }
        }

        @JavascriptInterface
        fun installUpdate(apkUrl: String) {
            val safeUrl = apkUrl.trim()
            if (!safeUrl.startsWith("https://github.com/MisterMoeGreen/Antigravity-Studio-App/releases/download/") ||
                !safeUrl.endsWith(".apk")) {
                android.util.Log.e("AgyMainActivity", "Rejected untrusted APK update URL: $safeUrl")
                val errMsg = org.json.JSONObject.quote("Untrusted update URL source")
                runOnUiThread {
                    webView.evaluateJavascript("window.onUpdateError && window.onUpdateError($errMsg);", null)
                }
                return
            }
            lifecycleScope.launch(Dispatchers.IO) {
                try {
                    InAppUpdateManager.downloadAndInstall(this@MainActivity, safeUrl) { progress ->
                        runOnUiThread {
                            webView.evaluateJavascript("window.onUpdateProgress && window.onUpdateProgress($progress);", null)
                        }
                    }
                } catch (e: Exception) {
                    android.util.Log.e("AgyMainActivity", "Failed to download and install update", e)
                    val errMsg = org.json.JSONObject.quote(e.message ?: "Update download failed")
                    runOnUiThread {
                        webView.evaluateJavascript("window.onUpdateError && window.onUpdateError($errMsg);", null)
                    }
                }
            }
        }
    }
}
