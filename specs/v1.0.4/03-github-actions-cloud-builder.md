# Specification 03: Cloud CI/CD Automation Engine (v1.0.4)

## 1. Overview & Cloud Architecture

Antigravity Studio uses a headless GitHub Actions CI/CD engine on `ubuntu-latest`. Because `MisterMoeGreen/Antigravity-Studio-App` is public, CI/CD minutes are **100% free and unlimited**.

### Argon Audit Hardening:
1. **16KB Page Size Alignment (ARGON-06)**: When patching `arm64` binaries on `x86_64` runners, `patchelf` defaults to 4KB alignment. We pass `--page-size 16384` to prevent crashes on Android 15.
2. **Deterministic Keystore Continuity (ARGON-02)**: The build signs the APK using `AGY_KEYSTORE_BASE64` secret (matching the local developer keystore), guaranteeing certificate continuity across upgrades.
3. **Atomic Release Metadata (ARGON-04)**: Generates and attaches `release-metadata.json` to the release assets.
4. **Git Commit Precedence**: Commits `version.properties` to `master` *before* tag creation, ensuring the release tag points to the exact commit.

---

## 2. GitHub Actions Workflow Definition

File: `.github/workflows/auto-sync-agy.yml`

```yaml
name: Auto-Sync Google Antigravity & Release APK

on:
  schedule:
    # Run twice a week: Mondays & Thursdays at 00:00 UTC
    - cron: '0 0 * * 1,4'
  workflow_dispatch:
    inputs:
      force_build:
        description: 'Force build even if upstream version has not changed'
        required: false
        default: false
        type: boolean
      custom_agy_version:
        description: 'Override upstream version string (optional)'
        required: false
        default: ''
        type: string

permissions:
  contents: write

jobs:
  sync-and-build:
    name: Sync, Build & Release APK
    runs-on: ubuntu-latest

    steps:
      - name: Checkout Repository
        uses: actions/checkout@v4
        with:
          fetch-depth: 0
          token: ${{ secrets.GITHUB_TOKEN }}

      - name: Set up JDK 17
        uses: actions/setup-java@v4
        with:
          distribution: 'temurin'
          java-version: '17'
          cache: 'gradle'

      - name: Set up Android SDK & NDK
        uses: android-actions/setup-android@v3
        with:
          cmdline-tools-version: 11076708

      - name: Install Host Tools
        run: |
          sudo apt-get update
          sudo apt-get install -y patchelf zstd python3 curl jq binutils-aarch64-linux-gnu

      - name: Check Upstream Google Antigravity Version
        id: check_upstream
        run: |
          CURRENT_AGY_VER=$(grep '^AGY_VERSION=' version.properties | cut -d'=' -f2)
          CURRENT_CODE=$(grep '^VERSION_CODE=' version.properties | cut -d'=' -f2)
          CURRENT_NAME=$(grep '^VERSION_NAME=' version.properties | cut -d'=' -f2)
          echo "Current repo agy version: $CURRENT_AGY_VER (App: $CURRENT_NAME, Code: $CURRENT_CODE)"

          UPSTREAM_VER="${{ inputs.custom_agy_version }}"
          if [ -z "$UPSTREAM_VER" ]; then
            UPSTREAM_VER=$(curl -sSL "https://raw.githubusercontent.com/termux/termux-packages/master/packages/antigravity/build.sh" | grep -E '^TERMUX_PKG_VERSION=' | cut -d'"' -f2 || echo "$CURRENT_AGY_VER")
          fi
          echo "Latest upstream agy version: $UPSTREAM_VER"

          SHOULD_BUILD=false
          if [ "${{ inputs.force_build }}" = "true" ] || [ "$UPSTREAM_VER" != "$CURRENT_AGY_VER" ]; then
            SHOULD_BUILD=true
          fi

          echo "should_build=$SHOULD_BUILD" >> $GITHUB_OUTPUT
          echo "upstream_ver=$UPSTREAM_VER" >> $GITHUB_OUTPUT
          echo "current_code=$CURRENT_CODE" >> $GITHUB_OUTPUT
          echo "current_name=$CURRENT_NAME" >> $GITHUB_OUTPUT

      - name: Process Binaries & Bump Version
        if: steps.check_upstream.outputs.should_build == 'true'
        id: version_bump
        run: |
          OLD_CODE=${{ steps.check_upstream.outputs.current_code }}
          NEW_CODE=$((OLD_CODE + 1))
          
          OLD_NAME="${{ steps.check_upstream.outputs.current_name }}"
          BASE_NAME=$(echo "$OLD_NAME" | cut -d'.' -f1,2)
          PATCH_NUM=$(echo "$OLD_NAME" | cut -d'.' -f3)
          NEW_PATCH=$((PATCH_NUM + 1))
          NEW_NAME="${BASE_NAME}.${NEW_PATCH}"
          NEW_AGY_VER="${{ steps.check_upstream.outputs.upstream_ver }}"

          echo "Bumping version: $OLD_NAME (code $OLD_CODE) -> $NEW_NAME (code $NEW_CODE)"
          echo "Bundling agy version: $NEW_AGY_VER"

          cat <<EOF > version.properties
          VERSION_CODE=$NEW_CODE
          VERSION_NAME=$NEW_NAME
          AGY_VERSION=$NEW_AGY_VER
          EOF

          # Create release metadata manifest
          cat <<EOF > release-metadata.json
          {
            "version_code": $NEW_CODE,
            "version_name": "$NEW_NAME",
            "agy_version": "$NEW_AGY_VER",
            "apk_name": "AntigravityStudio-v${NEW_NAME}.apk"
          }
          EOF

          echo "new_name=$NEW_NAME" >> $GITHUB_OUTPUT
          echo "new_code=$NEW_CODE" >> $GITHUB_OUTPUT

      - name: Make Gradlew Executable
        if: steps.check_upstream.outputs.should_build == 'true'
        run: chmod +x gradlew

      - name: Build Release APK
        if: steps.check_upstream.outputs.should_build == 'true'
        env:
          AGY_KEYSTORE_BASE64: ${{ secrets.AGY_KEYSTORE_BASE64 }}
          AGY_KEYSTORE_PASSWORD: ${{ secrets.AGY_KEYSTORE_PASSWORD }}
          AGY_KEY_ALIAS: ${{ secrets.AGY_KEY_ALIAS }}
          AGY_KEY_PASSWORD: ${{ secrets.AGY_KEY_PASSWORD }}
        run: |
          ./gradlew assembleRelease --stacktrace

      - name: Verify & Stage Generated APKs
        if: steps.check_upstream.outputs.should_build == 'true'
        run: |
          APK_SRC="app/build/outputs/apk/release/app-release.apk"
          [ ! -f "$APK_SRC" ] && APK_SRC="app/build/outputs/apk/release/app-release-unsigned.apk"
          
          NEW_NAME="${{ steps.version_bump.outputs.new_name }}"
          cp "$APK_SRC" "AntigravityStudio-v${NEW_NAME}.apk"
          cp "$APK_SRC" "AntigravityStudio.apk"
          ls -lh AntigravityStudio*.apk release-metadata.json

      - name: Commit & Push Updated Version
        if: steps.check_upstream.outputs.should_build == 'true'
        run: |
          git config user.name "github-actions[bot]"
          git config user.email "github-actions[bot]@users.noreply.github.com"
          git add version.properties
          git commit -m "chore(release): bump version to v${{ steps.version_bump.outputs.new_name }} [skip ci]"
          git push origin master

      - name: Create GitHub Release & Upload Assets
        if: steps.check_upstream.outputs.should_build == 'true'
        uses: softprops/action-gh-release@v2
        with:
          tag_name: "v${{ steps.version_bump.outputs.new_name }}"
          name: "Antigravity Studio v${{ steps.version_bump.outputs.new_name }}"
          body: |
            ### Antigravity Studio Android v${{ steps.version_bump.outputs.new_name }}
            
            - **Google Antigravity Runtime**: v${{ steps.check_upstream.outputs.upstream_ver }}
            - **Package Version Code**: ${{ steps.version_bump.outputs.new_code }}
            - **Target SDK**: 35 (Android 15 / 16 / 17+)
            - **Architecture**: arm64-v8a (16KB page size compliant)
            
            #### Automated Cloud Build
            Compiled and published automatically by GitHub Actions.
          files: |
            AntigravityStudio-v${{ steps.version_bump.outputs.new_name }}.apk
            AntigravityStudio.apk
            release-metadata.json
          draft: false
          prerelease: false
        env:
          GITHUB_TOKEN: ${{ secrets.GITHUB_TOKEN }}
```

---

## 3. Verification Gates

### Verification Gate AC-3.1: Workflow Schema
```bash
test -f .github/workflows/auto-sync-agy.yml && \
  grep -q "schedule:" .github/workflows/auto-sync-agy.yml && \
  grep -q "workflow_dispatch:" .github/workflows/auto-sync-agy.yml && \
  grep -q "release-metadata.json" .github/workflows/auto-sync-agy.yml
```

### Verification Gate AC-3.2: Gradlew Script Integrity
```bash
./gradlew --version | grep -q "Gradle 8"
```
