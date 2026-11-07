package ai.agy.app

import android.content.Context
import android.os.Build
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import android.util.Log
import org.json.JSONObject
import java.io.File
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

object KeystoreVault {
    private const val TAG = "KeystoreVault"
    private const val ANDROID_KEYSTORE = "AndroidKeyStore"
    private const val KEY_ALIAS = "AgyVaultMasterKey"
    private const val TRANSFORMATION = "AES/GCM/NoPadding"
    private const val GCM_TAG_LENGTH = 128

    private val lock = Any()

    private fun getOrCreateMasterKey(): SecretKey {
        val keyStore = KeyStore.getInstance(ANDROID_KEYSTORE).apply { load(null) }
        if (keyStore.containsAlias(KEY_ALIAS)) {
            val entry = keyStore.getEntry(KEY_ALIAS, null) as? KeyStore.SecretKeyEntry
            if (entry != null) return entry.secretKey
        }

        val keyGenerator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, ANDROID_KEYSTORE)

        // ARCH-V19-03: Try StrongBox first, fallback to standard hardware TEE
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
            try {
                val strongBoxSpec = KeyGenParameterSpec.Builder(
                    KEY_ALIAS,
                    KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT
                )
                    .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                    .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                    .setKeySize(256)
                    .setIsStrongBoxBacked(true)
                    .build()
                keyGenerator.init(strongBoxSpec)
                return keyGenerator.generateKey()
            } catch (e: Exception) {
                Log.w(TAG, "StrongBox unavailable, falling back to standard hardware TEE: ${e.message}")
            }
        }

        val standardSpec = KeyGenParameterSpec.Builder(
            KEY_ALIAS,
            KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT
        )
            .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
            .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
            .setKeySize(256)
            .build()
        keyGenerator.init(standardSpec)
        return keyGenerator.generateKey()
    }

    private fun getVaultFile(context: Context): File {
        val dbDir = File(context.applicationInfo.dataDir, "databases")
        if (!dbDir.exists()) dbDir.mkdirs()
        return File(dbDir, "keystore_vault.json")
    }

    private fun loadEncryptedStore(context: Context): JSONObject {
        val file = getVaultFile(context)
        if (!file.exists()) {
            return JSONObject().apply {
                put("version", 1)
                put("keys", JSONObject())
            }
        }
        return try {
            JSONObject(file.readText(Charsets.UTF_8))
        } catch (_: Exception) {
            JSONObject().apply {
                put("version", 1)
                put("keys", JSONObject())
            }
        }
    }

    private fun saveEncryptedStore(context: Context, json: JSONObject) {
        val file = getVaultFile(context)
        val tmpFile = File(file.parentFile, "${file.name}.tmp")
        tmpFile.writeText(json.toString(2), Charsets.UTF_8)
        try {
            android.system.Os.chmod(tmpFile.absolutePath, 0b110_000_000) // 0600
        } catch (_: Exception) {}
        if (!tmpFile.renameTo(file)) {
            file.delete()
            tmpFile.renameTo(file)
        }
        try {
            android.system.Os.chmod(file.absolutePath, 0b110_000_000)
        } catch (_: Exception) {}
    }

    fun encrypt(plaintext: String): Pair<String, String> {
        val key = getOrCreateMasterKey()
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.ENCRYPT_MODE, key)
        val iv = cipher.iv
        val ciphertext = cipher.doFinal(plaintext.toByteArray(Charsets.UTF_8))
        return Pair(
            Base64.encodeToString(iv, Base64.NO_WRAP),
            Base64.encodeToString(ciphertext, Base64.NO_WRAP)
        )
    }

    fun decrypt(ivBase64: String, ciphertextBase64: String): String {
        val key = getOrCreateMasterKey()
        val cipher = Cipher.getInstance(TRANSFORMATION)
        val iv = Base64.decode(ivBase64, Base64.NO_WRAP)
        val ciphertext = Base64.decode(ciphertextBase64, Base64.NO_WRAP)
        val spec = GCMParameterSpec(GCM_TAG_LENGTH, iv)
        cipher.init(Cipher.DECRYPT_MODE, key, spec)
        val plaintextBytes = cipher.doFinal(ciphertext)
        return String(plaintextBytes, Charsets.UTF_8)
    }

    fun saveKey(context: Context, name: String, value: String, description: String = "") {
        synchronized(lock) {
            val store = loadEncryptedStore(context)
            val keysObj = store.optJSONObject("keys") ?: JSONObject().also { store.put("keys", it) }
            val (ivB64, ctB64) = encrypt(value)
            val keyEntry = JSONObject().apply {
                put("iv", ivB64)
                put("ciphertext", ctB64)
                put("description", description)
                put("updated_at", System.currentTimeMillis() / 1000)
            }
            keysObj.put(name, keyEntry)
            saveEncryptedStore(context, store)
        }
    }

    fun deleteKey(context: Context, name: String): Boolean {
        synchronized(lock) {
            val store = loadEncryptedStore(context)
            val keysObj = store.optJSONObject("keys") ?: return false
            if (keysObj.has(name)) {
                keysObj.remove(name)
                saveEncryptedStore(context, store)
                return true
            }
            return false
        }
    }

    fun getDecryptedKey(context: Context, name: String): String? {
        synchronized(lock) {
            val store = loadEncryptedStore(context)
            val keysObj = store.optJSONObject("keys") ?: return null
            val entry = keysObj.optJSONObject(name) ?: return null
            val iv = entry.optString("iv")
            val ct = entry.optString("ciphertext")
            if (iv.isEmpty() || ct.isEmpty()) return null
            return try {
                decrypt(iv, ct)
            } catch (e: Exception) {
                Log.e(TAG, "Decryption failed for key $name", e)
                null
            }
        }
    }

    fun getAllDecryptedKeys(context: Context): Map<String, String> {
        synchronized(lock) {
            val map = mutableMapOf<String, String>()
            val store = loadEncryptedStore(context)
            val keysObj = store.optJSONObject("keys") ?: return map
            val it = keysObj.keys()
            while (it.hasNext()) {
                val name = it.next()
                val entry = keysObj.optJSONObject(name) ?: continue
                val iv = entry.optString("iv")
                val ct = entry.optString("ciphertext")
                if (iv.isNotEmpty() && ct.isNotEmpty()) {
                    try {
                        map[name] = decrypt(iv, ct)
                    } catch (_: Exception) {}
                }
            }
            return map
        }
    }

    fun maskApiKey(raw: String): String {
        if (raw.length <= 8) return "••••••••"
        return "${raw.take(4)}••••${raw.takeLast(4)}"
    }

    data class VaultKeyInfo(
        val name: String,
        val masked: String,
        val description: String,
        val createdAt: Long
    )

    fun listKeysMasked(context: Context): List<VaultKeyInfo> {
        synchronized(lock) {
            val list = mutableListOf<VaultKeyInfo>()
            val store = loadEncryptedStore(context)
            val keysObj = store.optJSONObject("keys") ?: return list
            val it = keysObj.keys()
            while (it.hasNext()) {
                val name = it.next()
                val entry = keysObj.optJSONObject(name) ?: continue
                val desc = entry.optString("description", "")
                val ts = entry.optLong("updated_at", 0L)
                val iv = entry.optString("iv")
                val ct = entry.optString("ciphertext")
                val masked = try {
                    val raw = decrypt(iv, ct)
                    maskApiKey(raw)
                } catch (_: Exception) {
                    "••••••••"
                }
                list.add(VaultKeyInfo(name, masked, desc, ts))
            }
            return list
        }
    }

    // ARCH-V19-04: Atomic Migration & Cryptographic Wipe of Plaintext api_keys.json
    fun migrateLegacyApiKeysIfPresent(context: Context) {
        val legacyFile = File(context.applicationInfo.dataDir, "files/home/.gemini/config/api_keys.json")
        if (!legacyFile.exists()) return

        synchronized(lock) {
            try {
                val content = legacyFile.readText(Charsets.UTF_8)
                val json = JSONObject(content)
                val keys = json.optJSONObject("keys")
                if (keys != null) {
                    val it = keys.keys()
                    while (it.hasNext()) {
                        val keyName = it.next()
                        val keyObj = keys.optJSONObject(keyName)
                        val value = keyObj?.optString("value") ?: ""
                        val desc = keyObj?.optString("description") ?: ""
                        if (value.isNotEmpty()) {
                            saveKey(context, keyName, value, desc)
                            // Verify read-back
                            val readBack = getDecryptedKey(context, keyName)
                            if (readBack != value) {
                                Log.e(TAG, "Migration readback verification failed for $keyName")
                                return
                            }
                        }
                    }
                }
                // Cryptographic wipe: overwrite with zero bytes then unlink
                val length = legacyFile.length()
                if (length > 0) {
                    legacyFile.writeBytes(ByteArray(length.toInt()) { 0 })
                }
                legacyFile.delete()
                Log.i(TAG, "Successfully migrated and securely wiped legacy api_keys.json")
            } catch (e: Exception) {
                Log.e(TAG, "Error during legacy api_keys.json migration", e)
            }
        }
    }
}
