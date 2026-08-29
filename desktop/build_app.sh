#!/bin/bash
# Rebuild "Job App.app" from the launcher and the icon. Safe to run again any time.
#
#   python3 desktop/make_icon.py     # only if the icon changed
#   bash desktop/build_app.sh
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
APP="$ROOT/Job App.app"

rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

sed "s|__PROJECT_ROOT__|$ROOT|g" "$HERE/launcher.sh" > "$APP/Contents/MacOS/JobApp"
chmod +x "$APP/Contents/MacOS/JobApp"
cp "$HERE/AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"

cat > "$APP/Contents/Info.plist" <<'PLIST'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key>              <string>Job App</string>
  <key>CFBundleDisplayName</key>       <string>Job App</string>
  <key>CFBundleIdentifier</key>        <string>com.sameeriyer.jobapp</string>
  <key>CFBundleExecutable</key>        <string>JobApp</string>
  <key>CFBundleIconFile</key>          <string>AppIcon</string>
  <key>CFBundlePackageType</key>       <string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>CFBundleVersion</key>           <string>1</string>
  <key>LSMinimumSystemVersion</key>    <string>12.0</string>
  <key>NSHighResolutionCapable</key>   <true/>
</dict>
</plist>
PLIST

# An unsigned bundle has no stable identity, so macOS cannot record a permission grant
# against it and every launch from a protected folder such as Desktop fails silently.
codesign --force --sign - --identifier com.sameeriyer.jobapp "$APP" 2>/dev/null \
  && echo "signed (ad hoc)" || echo "WARNING: codesign failed, Desktop access may be refused"

touch "$APP"
echo "built: $APP"
