# Specification 01: Automated Versioning & Gradle Integration (v1.0.4)

## 1. Problem Statement & Architecture

In `v1.0.0` through `v1.0.3`, the application version was hardcoded inside `app/build.gradle.kts`:
```kotlin
defaultConfig {
    versionCode = 5
    versionName = "1.0.3"
}
```

### Critical Risks Identified by Argon Review:
1. **The Ephemeral `debug.keystore` Trap (ARGON-02)**: On ephemeral GitHub Actions runners (`ubuntu-latest`), AGP generates a new random RSA keypair every run. Updating an installed APK with an APK signed by a different key fails with `INSTALL_FAILED_UPDATE_INCOMPATIBLE`, forcing the user to uninstall and lose all conversation data.
2. **Version Desynchronization & Update Loops (ARGON-04)**: `BuildConfig.APP_VERSION_CODE` is compiled into DEX. Unless injected into `server.py`'s environment, `server.py` must guess the local version or read stale files.
3. **Release Asset Race**: Querying `version.properties` on `master` before the GitHub Release assets are fully uploaded can trigger premature download loops.

---

## 2. `version.properties` Single Source of Truth

We introduce a dedicated properties file at repository root: `version.properties`.

### File Schema (`version.properties`)
```properties
# Antigravity Studio Version Specification
VERSION_CODE=6
VERSION_NAME=1.0.4
AGY_VERSION=1.4.2
AGY_SHA256=e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
```

### Definitions:
- **`VERSION_CODE`**: Strictly increasing 32-bit positive integer required by Android OS for upgrade sequencing. Monotonically incremented on every release.
- **`VERSION_NAME`**: Human-readable Semantic Version string (`MAJOR.MINOR.PATCH`).
- **`AGY_VERSION`**: The exact upstream Google Antigravity binary release packaged within this APK (`1.4.2`).
- **`AGY_SHA256`**: SHA-256 hash of the upstream package to detect silent upstream rebuilds.

---

## 3. Gradle Kotlin DSL Integration & Deterministic Signing

`app/build.gradle.kts` dynamically parses `version.properties` at configuration time, with CLI override support (`-P` flags) and deterministic keystore signing (`releasePersistent`).

### Implementation in `app/build.gradle.kts`:

```kotlin
import java.io.FileInputStream
import java.util.Properties
import java.util.Base64

// 1. Load version.properties from rootProject
val versionPropsFile = rootProject.file("version.properties")
val versionProps = Properties().apply {
    if (versionPropsFile.exists()) {
        FileInputStream(versionPropsFile).use { load(it) }
    }
}

// 2. Resolve version parameters with CLI override support (-P) and safe fallbacks
val resolvedVersionCode: Int = (project.findProperty("versionCode") as? String)?.toIntOrNull()
    ?: versionProps.getProperty("VERSION_CODE", "6").toInt()

val resolvedVersionName: String = (project.findProperty("versionName") as? String)
    ?: versionProps.getProperty("VERSION_NAME", "1.0.4")

val resolvedAgyVersion: String = (project.findProperty("agyVersion") as? String)
    ?: versionProps.getProperty("AGY_VERSION", "1.4.2")

android {
    namespace = "ai.agy.app"
    compileSdk = 35

    defaultConfig {
        applicationId = "ai.agy.app"
        minSdk = 28
        targetSdk = 35
        versionCode = resolvedVersionCode
        versionName = resolvedVersionName

        // Inject BuildConfig constants for runtime access in Kotlin
        buildConfigField("String", "AGY_RUNTIME_VERSION", "\"$resolvedAgyVersion\"")
        buildConfigField("int", "APP_VERSION_CODE", "$resolvedVersionCode")

        ndk {
            abiFilters += listOf("arm64-v8a")
        }
    }

    buildFeatures {
        buildConfig = true
    }

    signingConfigs {
        create("releasePersistent") {
            val keystoreBase64 = System.getenv("AGY_KEYSTORE_BASE64")
            if (!keystoreBase64.isNullOrBlank()) {
                // Headless CI Runner: Decode keystore from repository secrets
                val ciKeystore = File(rootProject.layout.buildDirectory.asFile.get(), "ci-release.keystore")
                ciKeystore.parentFile.mkdirs()
                ciKeystore.writeBytes(Base64.getDecoder().decode(keystoreBase64.trim()))
                storeFile = ciKeystore
                storePassword = System.getenv("AGY_KEYSTORE_PASSWORD") ?: "android"
                keyAlias = System.getenv("AGY_KEY_ALIAS") ?: "androiddebugkey"
                keyPassword = System.getenv("AGY_KEY_PASSWORD") ?: "android"
            } else {
                // Local Termux / Developer fallback: Use persistent debug.keystore
                val debugKeystore = File(System.getProperty("user.home"), ".android/debug.keystore")
                storeFile = debugKeystore
                storePassword = "android"
                keyAlias = "androiddebugkey"
                keyPassword = "android"
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            signingConfig = signingConfigs.getByName("releasePersistent")
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro"
            )
        }
        debug {
            applicationIdSuffix = ""
            signingConfig = signingConfigs.getByName("releasePersistent")
        }
    }
}
```

---

## 4. Runtime Version Propagation into Python Orchestrator

In `AgyForegroundService.kt`:
```kotlin
env["AGY_APP_VERSION_CODE"] = BuildConfig.APP_VERSION_CODE.toString()
env["AGY_APP_VERSION_NAME"] = BuildConfig.VERSION_NAME
env["AGY_RUNTIME_VERSION"] = BuildConfig.AGY_RUNTIME_VERSION
```

`server.py` compares:
```python
def check_for_updates():
    # Fetch latest published release metadata
    res = urllib.request.urlopen("https://api.github.com/repos/MisterMoeGreen/Antigravity-Studio-App/releases/latest")
    data = json.loads(res.read())
    
    # 1. Look for release-metadata.json attached asset
    remote_code = None
    for asset in data.get("assets", []):
        if asset["name"] == "release-metadata.json":
            meta = json.loads(urllib.request.urlopen(asset["browser_download_url"]).read())
            remote_code = meta.get("version_code")
            break
            
    # 2. Compare integer version codes
    local_code = int(os.environ.get("AGY_APP_VERSION_CODE", "6"))
    if remote_code and remote_code > local_code:
        return True, data
    return False, None
```

---

## 5. Verification Gates

### Verification Gate AC-1.1: File Schema
```bash
test -f version.properties && \
  grep -q '^VERSION_CODE=[0-9]\+' version.properties && \
  grep -q '^VERSION_NAME=[0-9]\+\.[0-9]\+\.[0-9]\+' version.properties && \
  grep -q '^AGY_VERSION=[0-9]\+\.[0-9]\+\.[0-9]\+' version.properties
```

### Verification Gate AC-1.2: Gradle Property Resolution & Deterministic Signing
```bash
./gradlew -q app:properties | grep -E "versionName: [0-9]+\.[0-9]+\.[0-9]+"
```
