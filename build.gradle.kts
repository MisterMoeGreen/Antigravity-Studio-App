plugins {
    id("com.android.application") version "8.5.2" apply false
    id("org.jetbrains.kotlin.android") version "1.9.24" apply false
}

// Dynamically resolve AAPT2 binary location if default override path is missing (ARGON-09)
val configuredAapt2 = project.findProperty("android.aapt2FromMavenOverride") as? String
if (configuredAapt2 == null || !java.io.File(configuredAapt2).exists()) {
    val aapt2Candidates = listOf(
        "/usr/bin/aapt2",
        "/data/data/com.termux/files/usr/bin/aapt2",
        "/data/data/ai.agy.app/files/home/.local/bin/aapt2",
        "/data/data/ai.agy.app/files/usr/bin/aapt2"
    )
    val validAapt2 = aapt2Candidates.firstOrNull { java.io.File(it).exists() }
    if (validAapt2 != null) {
        System.setProperty("android.aapt2FromMavenOverride", validAapt2)
    }
}
