# Add project specific ProGuard rules here.
-keepattributes *Annotation*
-dontwarn com.github.luben.zstd.**
-keep class com.github.luben.zstd.** { *; }
-dontwarn org.apache.commons.compress.**
-keep class org.apache.commons.compress.** { *; }
