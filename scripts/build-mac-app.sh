#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_NAME="Smoothie"
VERSION="${SMOOTHIE_VERSION:-0.0.3}"
DIST_DIR="$ROOT_DIR/dist"
APP_DIR="$DIST_DIR/$APP_NAME.app"
CONTENTS_DIR="$APP_DIR/Contents"
RESOURCES_DIR="$CONTENTS_DIR/Resources"

cd "$ROOT_DIR"
mkdir -p "$DIST_DIR"

if [[ ! -d "webui/node_modules" ]]; then
  npm --prefix webui install
fi
npm --prefix webui run build

swift build --package-path macapp -c release
rm -rf "$APP_DIR"
mkdir -p "$CONTENTS_DIR/MacOS" "$RESOURCES_DIR"
cp "macapp/.build/arm64-apple-macosx/release/SmoothieMenuBar" "$CONTENTS_DIR/MacOS/SmoothieMenuBar"

cat > "$CONTENTS_DIR/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleDisplayName</key><string>Smoothie</string>
  <key>CFBundleExecutable</key><string>SmoothieMenuBar</string>
  <key>CFBundleIdentifier</key><string>com.smoothie.menu-bar</string>
  <key>CFBundleName</key><string>Smoothie</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>LSMinimumSystemVersion</key><string>13.0</string>
  <key>LSUIElement</key><true/>
  <key>NSMicrophoneUsageDescription</key><string>Smoothie needs microphone access to record audio for local transcription.</string>
</dict>
</plist>
PLIST

# Bundle the server source and the built web dashboard. Runtime data is copied
# to Application Support on first launch, never written inside the app bundle.
rsync -a --delete \
  --exclude '.venv' \
  --exclude 'data' \
  --exclude 'config.yaml' \
  --exclude '__pycache__' \
  backend/ "$RESOURCES_DIR/backend/"
rsync -a webui/dist/ "$RESOURCES_DIR/webui/dist/"

PYTHON_BIN="${PYTHON_BIN:-$(command -v python3)}"
"$PYTHON_BIN" -m venv --copies "$RESOURCES_DIR/backend/.venv"
"$RESOURCES_DIR/backend/.venv/bin/python" -m pip install --upgrade pip
"$RESOURCES_DIR/backend/.venv/bin/pip" install -r "$RESOURCES_DIR/backend/requirements.txt"

chmod +x "$CONTENTS_DIR/MacOS/SmoothieMenuBar"
codesign --force --deep --sign - "$APP_DIR" >/dev/null

DMG_PATH="$DIST_DIR/$APP_NAME-$VERSION.dmg"
rm -f "$DMG_PATH"
if ! hdiutil create -volname "$APP_NAME" -srcfolder "$APP_DIR" -ov -format UDZO "$DMG_PATH" >/dev/null; then
  echo "Warning: hdiutil could not create a DMG; creating a zip fallback instead." >&2
  rm -f "$DMG_PATH"
  ditto -c -k --sequesterRsrc --keepParent "$APP_DIR" "$DIST_DIR/$APP_NAME-$VERSION.zip"
fi

printf 'Built:\n  %s\n' "$APP_DIR"
if [[ -f "$DMG_PATH" ]]; then
  printf '  %s\n' "$DMG_PATH"
else
  printf '  %s\n' "$DIST_DIR/$APP_NAME-$VERSION.zip"
fi
