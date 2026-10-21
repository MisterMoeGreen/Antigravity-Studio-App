package ai.agy.app

import android.content.Context
import android.os.Build
import android.system.Os
import com.github.luben.zstd.ZstdInputStream
import org.apache.commons.compress.archivers.tar.TarArchiveInputStream
import android.util.Log
import java.io.File
import java.io.FileOutputStream

object EnvironmentBootstrapper {

    private const val TAG = "Bootstrapper"

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
        "python3.14" to "libpython3_bin.so",
        "python3.12" to "libpython3_bin.so",
        "python" to "libpython3_bin.so",
        "env" to "libbusybox.so",
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
        try {
            Os.chmod(binDir.absolutePath, 0b111_000_000)
        } catch (_: Exception) {}

        // 1. Unpack assets/usr-data.tar.zst on first launch or version/build upgrade
        val versionFile = File(usrDir, ".version_code")
        val pkgInfo = context.packageManager.getPackageInfo(context.packageName, 0)
        val currentVersion = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            pkgInfo.longVersionCode
        } else {
            @Suppress("DEPRECATION") pkgInfo.versionCode.toLong()
        }
        val currentBuildId = "${currentVersion}_${pkgInfo.lastUpdateTime}"
        val installedBuildId = if (versionFile.exists()) versionFile.readText().trim() else ""

        if (installedBuildId != currentBuildId) {
            Log.i(TAG, "Unpacking assets/usr-data.tar.zst (build $installedBuildId -> $currentBuildId)...")
            extractZstdTarAsset(context, "usr-data.tar.zst", usrDir)
            versionFile.writeText(currentBuildId)
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

        // Symlink usr/glibc/etc -> usr/etc so Glibc getaddrinfo & NSS resolve dynamically
        createOrUpdateSymlink(File(glibcDir, "etc"), etcDir.absolutePath)

        // Symlink libproot_loader.so for PRoot anonymous memory loader (Spec 01)
        createOrUpdateSymlink(File(usrLibDir, "libproot_loader.so"), "$nativeDir/libproot_loader.so")

        // Extract rootfs.tar.zst on first launch, guarded by os-release existence check (Spec 03)
        val rootfsDir = File(filesDir, "rootfs")
        val osRelease = File(rootfsDir, "etc/os-release")

        if (!osRelease.exists()) {
            Log.i(TAG, "First boot: Extracting Alpine rootfs to ${rootfsDir.absolutePath}...")
            rootfsDir.mkdirs()
            extractZstdTarAsset(context, "rootfs.tar.zst", rootfsDir)

            // Seed default DNS and package repository mirrors
            File(rootfsDir, "etc/resolv.conf").writeText("nameserver 8.8.8.8\nnameserver 1.1.1.1\n")
            File(rootfsDir, "etc/apk/repositories").writeText(
                "http://dl-cdn.alpinelinux.org/alpine/v3.20/main\n" +
                "http://dl-cdn.alpinelinux.org/alpine/v3.20/community\n"
            )
        } else {
            Log.i(TAG, "Alpine rootfs already initialized. Skipping extract to preserve user packages.")
        }

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

        // 4. Seed and synchronize Antigravity OAuth Token with shared storage
        val appGeminiDir = File(homeDir, ".gemini/antigravity-cli")
        appGeminiDir.mkdirs()
        val appTokenFile = File(appGeminiDir, "antigravity-oauth-token")

        val candidateTokenPaths = listOf(
            File("/storage/emulated/0/Documents/AgyStudio/antigravity-oauth-token"),
            File("/storage/emulated/0/Documents/antigravity-oauth-token"),
            File(android.os.Environment.getExternalStoragePublicDirectory(android.os.Environment.DIRECTORY_DOCUMENTS), "AgyStudio/antigravity-oauth-token")
        )

        for (candidate in candidateTokenPaths) {
            try {
                if (candidate.exists() && candidate.length() > 0) {
                    if (!appTokenFile.exists() || candidate.lastModified() > appTokenFile.lastModified()) {
                        candidate.copyTo(appTokenFile, overwrite = true)
                        Log.i(TAG, "Successfully seeded antigravity-oauth-token from ${candidate.absolutePath}")
                    }
                    break
                }
            } catch (_: Exception) {}
        }

        // Mirror active token back to shared storage for seamless retention across reinstalls
        try {
            val backupDest = File("/storage/emulated/0/Documents/AgyStudio/antigravity-oauth-token")
            if (appTokenFile.exists() && (!backupDest.exists() || appTokenFile.lastModified() > backupDest.lastModified())) {
                backupDest.parentFile?.mkdirs()
                appTokenFile.copyTo(backupDest, overwrite = true)
            }
        } catch (_: Exception) {}

        // 5. Seed and synchronize default_app_prompt.md -> ~/.gemini/config/GEMINI.md
        try {
            val geminiConfigDir = File(homeDir, ".gemini/config")
            val geminiRulesDir = File(geminiConfigDir, "rules")
            geminiRulesDir.mkdirs()
            val geminiMd = File(geminiConfigDir, "GEMINI.md")
            val defaultPromptAsset = File(usrDir, "share/agy-ui/default_app_prompt.md")
            val userInstructionsFile = File(geminiRulesDir, "user_instructions.md")

            var basePrompt = if (defaultPromptAsset.exists()) {
                defaultPromptAsset.readText(Charsets.UTF_8).trim()
            } else if (geminiMd.exists()) {
                val raw = geminiMd.readText(Charsets.UTF_8).trim()
                if (raw.contains("\n\n# User Custom Instructions")) {
                    raw.substringBefore("\n\n# User Custom Instructions").trim()
                } else {
                    raw
                }
            } else ""

            if (userInstructionsFile.exists()) {
                val userText = userInstructionsFile.readText(Charsets.UTF_8).trim()
                if (userText.isNotEmpty()) {
                    basePrompt += "\n\n# User Custom Instructions\n\n$userText\n"
                }
            }

            if (basePrompt.isNotEmpty()) {
                geminiMd.writeText(basePrompt, Charsets.UTF_8)
            }
            createOrUpdateSymlink(File(homeDir, ".gemini/GEMINI.md"), geminiMd.absolutePath)
        } catch (_: Exception) {}

        // 6. Seed all builtin skills (isolated from user-editable skills)
        try {
            val skillsSourceDir = File(usrDir, "share/agy-ui/skills")
            val builtinSkillsDir = File(homeDir, ".gemini/antigravity-cli/builtin/skills")
            if (skillsSourceDir.isDirectory) {
                skillsSourceDir.listFiles()?.forEach { skillDir ->
                    if (skillDir.isDirectory) {
                        val srcSkillMd = File(skillDir, "SKILL.md")
                        val destSkillDir = File(builtinSkillsDir, skillDir.name)
                        val destSkillMd = File(destSkillDir, "SKILL.md")
                        if (srcSkillMd.exists() && (!destSkillMd.exists() || srcSkillMd.lastModified() > destSkillMd.lastModified())) {
                            destSkillDir.mkdirs()
                            srcSkillMd.copyTo(destSkillMd, overwrite = true)
                        }
                    }
                }
            }
            val legacySkillDir = File(homeDir, ".gemini/config/skills/ask-question")
            if (legacySkillDir.exists()) {
                legacySkillDir.deleteRecursively()
            }
        } catch (_: Exception) {}

        // 7. Seed standard SSH known_hosts for GitHub & GitLab (MITM defense)
        try {
            val sshDir = File(homeDir, ".ssh")
            sshDir.mkdirs()
            Os.chmod(sshDir.absolutePath, 0b111_000_000) // 0700
            val knownHostsFile = File(sshDir, "known_hosts")
            if (!knownHostsFile.exists() || knownHostsFile.length() == 0L) {
                val defaultKnownHosts = """
# Standard GitHub & GitLab Host Keys (Pre-seeded for MITM protection)
github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLnxAzETJ+kytsnupzs0KECc
github.com ecdsa-sha2-nistp256 AAAAE2VjZHNhLXNoYTItbmlzdHAyNTYAAAAIbmlzdHAyNTYAAABBBEmKSENjQEezOmxkZMy7opKgwFB9nkt5YRrYMjNuG5N87uR23pHrCEhVlW/9pcKGqvdqQD37OOcxSk+4HtddKeU=
github.com ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABgQCj7ndNxQowgcQnjshcLrqPEiiphnt+VTTvDP6mHBL9j1aNUkY4Ue1gvwnGLVlOhGeYrnZaMgRK6+PKCUXaDb6wqqsz/mt4C27fqBQU2vqh4WhhxxnrRqwUS199IjVCc692crkFAIbN5MmOJayUEYW6dJitterzH1FF951LT8GI626floIbEk6Y+io65NVzMiahEr72MZ+OYyavVVpXN55nj5dTC65NT6IFMgE44zfCunCob046g300jdKGig7/MrX05h+29KNeFHB64THV6/22+OxqAEysmVuzZMgyGw28BIbwOmvyEQdzTEKLRgIC6uSohHGZObLnElJyMxXxRHtk=
gitlab.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIAfuCHKVTjquxvt6CM6tdG4SLp1Btn/nOeHHE5UOzRdf
gitlab.com ecdsa-sha2-nistp256 AAAAE2VjZHNhLXNoYTItbmlzdHAyNTYAAAAIbmlzdHAyNTYAAABBBFSMqzJeV9rUzU4kWitGjeR4PWSa29SPqJ1fUtEQjZecqErYzWCisKed3UngAJ7G3Prd21OUI+9LEUhgBQgkE4=
gitlab.com ssh-rsa AAAAB3NzaC1yc2EAAAADAQABAAABAQCsj2bNKTBSpIYDEGb9KzsGh3ULVNop7ZREmlLtQt8Am2ihw6AZMul+JY3PHyhmPv0BJPiteVeRR6LvxEWPzSjiBQMWVkC5DUbe0Sf/IAFc8OOMrHJedk20YDY95baa+UxP9GWHYktqfZjcKyaWimSCnE9GlReUxN1bf2II6iA6VNqznZaoKn373VK40fi/mzFjZtZN3wN84YOE2YCGkkgrVKmBQY57A6egIp+gwpDgoIfpbIdeINhA4eMZXdZmU3a100HJaTDBprzQdJJ6WNiUomScLurZs48NRH/9jeGvokUKdtWhyYXuCY6U5O+Bigfp8w6hMZ9HS8xexNu7twPD
""".trimIndent() + "\n"
                knownHostsFile.writeText(defaultKnownHosts)
                Os.chmod(knownHostsFile.absolutePath, 0b110_000_000) // 0600
            }
        } catch (_: Exception) {}
    }

    private fun createOrUpdateSymlink(linkFile: File, targetPath: String) {
        try {
            if (linkFile.isDirectory && !java.nio.file.Files.isSymbolicLink(linkFile.toPath())) {
                linkFile.deleteRecursively()
            } else {
                Os.remove(linkFile.absolutePath)
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
                            outFile.setWritable(true, false)
                        } else if (entry.isSymbolicLink) {
                            createOrUpdateSymlink(outFile, entry.linkName)
                        } else {
                            outFile.parentFile?.mkdirs()
                            FileOutputStream(outFile).use { out ->
                                tarIn.copyTo(out)
                            }
                            outFile.setWritable(true, false)
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
