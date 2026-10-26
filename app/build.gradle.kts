import java.io.FileInputStream
import java.util.Properties
import java.util.Base64

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// 1. Load version.properties from rootProject
val versionPropsFile = rootProject.file("version.properties")
val versionProps = Properties().apply {
    if (versionPropsFile.exists()) {
        FileInputStream(versionPropsFile).use { load(it) }
    }
}

// 2. Resolve version parameters with CLI override support (-P) and safe fallbacks
val resolvedVersionCode: Int = (project.findProperty("versionCode") as? String)?.toIntOrNull()
    ?: versionProps.getProperty("VERSION_CODE", "10").toInt()

val resolvedVersionName: String = (project.findProperty("versionName") as? String)
    ?: versionProps.getProperty("VERSION_NAME", "1.2.0")

val resolvedAgyVersion: String = (project.findProperty("agyVersion") as? String)
    ?: versionProps.getProperty("AGY_VERSION", "1.4.2")

version = resolvedVersionName
extra.set("versionName", resolvedVersionName)

android {
    namespace = "ai.agy.app"
    compileSdk = 35

    defaultConfig {
        applicationId = "ai.agy.app" // Exactly 10 chars matching "com.termux"
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

    packaging {
        jniLibs {
            // CRITICAL: Forces physical extraction to /data/app/.../lib/arm64/ with SELinux apk_data_file
            useLegacyPackaging = true
        }
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

    lint {
        checkReleaseBuilds = false
        abortOnError = false
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.15.0")
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.webkit:webkit:1.12.1")
    implementation("androidx.browser:browser:1.8.0")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")
    implementation("com.github.luben:zstd-jni:1.5.6-8@aar")
    implementation("org.apache.commons:commons-compress:1.27.1")
}

tasks.register<Exec>("syncWebAssets") {
    inputs.dir(rootProject.file("agy-ui"))
    outputs.file(project.file("src/main/assets/usr-data.tar.zst"))
    commandLine("bash", "${rootProject.rootDir}/scripts/sync_web_assets.sh")
}

tasks.named("preBuild") {
    dependsOn("syncWebAssets")
}
