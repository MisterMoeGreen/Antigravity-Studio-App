# Module 04: Monochrome Status Bar Notification Icon

## 1. Problem Statement & Root Cause

Notifications posted by `AgyForegroundService` (used for background task completion, question prompts, and service persistence) currently display a generic "A" icon in the Android system status bar.

### Root Cause Analysis
Since Android 5.0 (API 21), the Android Notification Subsystem enforces strict rendering constraints on the **Small Icon** (`NotificationCompat.Builder.setSmallIcon`):
1. **Pure Alpha Mask Constraint**: Android ignores all RGB color channels and treats the drawable solely as an alpha mask.
2. **Color Incompatibility**: If an app passes its standard full-color launcher icon (`R.mipmap.ic_launcher`) or an image with an opaque background to `setSmallIcon`, the Android framework cannot render the colors. Depending on the OEM skin (Samsung OneUI, Google Pixel, Xiaomi HyperOS), Android either renders an unsightly solid white box or falls back to an arbitrary generic letter placeholder (e.g., the letter "A").
3. **Dual Icon Architecture**:
   * **Small Icon (`setSmallIcon`)**: Appears in the top status bar, always-on display (AOD), and the bottom badge of the notification shade. Must be a **monochrome silhouette on a transparent background**.
   * **Large Icon (`setLargeIcon`)**: Appears on the right side of the expanded notification card. Supports **full-color bitmaps**.

---

## 2. Asset Specification & Vector Geometry

The new status bar icon is defined as a scalable Android Vector Drawable (`ic_stat_antigravity.xml`) mathematically traced from the official Antigravity Studio portal logo (`agystudio.png`).

```
┌───────────────────────────────────────────────┐
│ 24dp x 24dp Canvas (Viewport 500 x 500)       │
│                                               │
│       ┌───────────────────────────────┐       │
│       │       .---..                  │       │
│       │      /       \   .            │       │
│       │     |    (o)  \ /             │       │
│       │     |          |              │       │
│       │     |          |              │       │
│       │    /            \             │       │
│       │   '              '            │       │
│       └───────────────────────────────┘       │
│   ◄─ 4.2dp ─►  15.6dp x 14.5dp  ◄─ 4.2dp ─►   │
└───────────────────────────────────────────────┘
```

### 2.1 Vector Coordinates & Safe Margins
* **Target Viewport**: `24dp × 24dp` with virtual coordinates `500 × 500`.
* **Artwork Boundaries**: X: 88 to 412 (width 325 / 15.6dp), Y: 99 to 400 (height 302 / 14.5dp).
* **Safe Padding**: 15% transparent padding on all sides (~4.2dp lateral, ~4.75dp vertical), ensuring no edge-clipping on rounded status bar corners or notch cutouts.
* **Fill Color**: `#FFFFFFFF` (Pure white alpha mask).

### 2.2 Vector XML Specification (`res/drawable/ic_stat_antigravity.xml`)

```xml
<vector xmlns:android="http://schemas.android.com/apk/res/android"
    android:width="24dp"
    android:height="24dp"
    android:viewportWidth="500"
    android:viewportHeight="500">
    <group
        android:translateY="500"
        android:scaleX="0.1"
        android:scaleY="-0.1">
        <!-- Left Arch Swoosh -->
        <path
            android:fillColor="#FFFFFFFF"
            android:pathData="M2345,3991 c-220,-63 -391,-248 -528,-571 -54,-128 -94,-250 -157,-480 -104,-379 -221,-772 -270,-910 -125,-350 -267,-606 -436,-789 -73,-79 -91,-124 -71,-173 51,-121 188,-100 386,58 213,171 371,443 499,859 41,134 71,244 168,620 70,274 67,263 98,365 49,165 88,267 156,405 145,297 303,426 520,428 120,0 206,-29 320,-110 l24,-18 -19,34 c-11,19 -56,71 -100,116 -86,87 -176,141 -285,170 -75,20 -229,18 -305,-4z"/>
        <!-- Right Arch Swoosh -->
        <path
            android:fillColor="#FFFFFFFF"
            android:pathData="M2627,3719 c-72,-17 -207,-101 -207,-129 0,-4 19,-4 42,2 84,18 186,-20 258,-99 82,-88 141,-200 206,-392 51,-148 59,-176 144,-506 166,-646 231,-846 347,-1065 69,-132 127,-214 222,-313 171,-179 358,-266 442,-206 32,23 55,84 45,122 -3,14 -40,65 -82,114 -96,111 -149,185 -213,297 -140,242 -262,574 -401,1086 -145,537 -195,675 -303,835 -139,206 -318,297 -500,254z"/>
        <!-- Interior Gravity Accent -->
        <path
            android:fillColor="#FFFFFFFF"
            android:pathData="M2421,3182 c-29,-24 -35,-36 -39,-83 -4,-49 -1,-59 23,-87 14,-17 44,-37 66,-43 55,-17 113,9 141,64 26,52 17,100 -27,144 -28,28 -40,33 -82,33 -39,0 -56,-6 -82,-28z"/>
    </group>
</vector>
```

---

## 3. Host Android Service Integration (`AgyForegroundService.kt`)

In `app/src/main/java/ai/agy/app/service/AgyForegroundService.kt`:

```kotlin
// ARGON-V19-07: Explicit PendingIntent.FLAG_IMMUTABLE for Android 12+ (targetSdk 34+)
val pendingIntentFlags = PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE

val pendingIntent = PendingIntent.getActivity(
    this,
    0,
    Intent(this, MainActivity::class.java).apply {
        flags = Intent.FLAG_ACTIVITY_SINGLE_TOP or Intent.FLAG_ACTIVITY_CLEAR_TOP
    },
    pendingIntentFlags
)

// Build notification for background daemon and progress updates
val notificationBuilder = NotificationCompat.Builder(this, CHANNEL_ID_FOREGROUND)
    // Small Icon: Monochrome silhouette for Status Bar
    .setSmallIcon(R.drawable.ic_stat_antigravity)
    
    // Brand Accent Color: Tints the small icon in expanded notification shade
    .setColor(ContextCompat.getColor(this, R.color.brand_primary))
    
    // Large Icon: Full-color app logo in expanded shade card
    .setLargeIcon(BitmapFactory.decodeResource(resources, R.mipmap.ic_launcher))
    
    .setContentTitle(title)
    .setContentText(message)
    .setPriority(NotificationCompat.PRIORITY_LOW)
    .setOngoing(isPersistent)
    .setContentIntent(pendingIntent)
```

### Brand Tinting
* In the status bar, Android automatically renders the icon in white (dark mode) or dark gray (light mode).
* In the expanded notification header, Android applies `setColor(R.color.brand_primary)` (Antigravity Blue `#2F338C`), providing visual cohesion with the brand identity.

---

## 4. Multi-Density Verification

To verify visual clarity across different Android display densities, the vector asset scales cleanly without aliasing across:
* **mdpi** (`1x`): 24 × 24 px
* **hdpi** (`1.5x`): 36 × 36 px
* **xhdpi** (`2x`): 48 × 48 px
* **xxhdpi** (`3x`): 72 × 72 px
* **xxxhdpi** (`4x`): 96 × 96 px

---

## 5. Implementation Acceptance Criteria

- [ ] **AC-NOTIF-01**: `app/src/main/res/drawable/ic_stat_antigravity.xml` exists and compiles cleanly with Android AAPT2.
- [ ] **AC-NOTIF-02**: `AgyForegroundService.kt` invokes `setSmallIcon(R.drawable.ic_stat_antigravity)`.
- [ ] **AC-NOTIF-03**: Status bar notifications display the sharp Antigravity portal silhouette with zero generic "A" placeholders.
- [ ] **AC-NOTIF-04**: Pulling down the notification shade reveals the full-color app logo as the Large Icon.
- [ ] **AC-NOTIF-05**: Icon renders properly on both Dark Mode and Light Mode status bars without clipping.
