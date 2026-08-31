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

# A compiled binary, not a script. macOS gives file-access permissions to the process it
# launches; for a script that is /bin/bash, so a grant made to Job App applies to nothing.
#
# Recompiling produces a different binary even from identical source, and Full Disk Access
# is recorded against the binary. So it is only rebuilt when launcher.c has actually
# changed. Every other rebuild reuses the one already carrying the grant.
DEST_BIN="$HOME/Desktop/Job App.app/Contents/MacOS/JobApp"
STAMP="$HERE/.launcher.sha256"
NOW="$(shasum -a 256 "$HERE/launcher.c" | cut -d" " -f1)"

if [ -x "$DEST_BIN" ] && [ -f "$STAMP" ] && [ "$(cat "$STAMP")" = "$NOW" ]; then
  cp "$DEST_BIN" "$APP/Contents/MacOS/JobApp"
  REUSED=1
else
  clang -O2 -Wall -o "$APP/Contents/MacOS/JobApp" "$HERE/launcher.c"
  echo "$NOW" > "$STAMP"
  REUSED=0
fi
sed "s|__PROJECT_ROOT__|$ROOT|g" "$HERE/run.sh" > "$APP/Contents/Resources/run.sh"
chmod +x "$APP/Contents/MacOS/JobApp" "$APP/Contents/Resources/run.sh"
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
if [ "$REUSED" = "0" ]; then
  codesign --force --sign - --identifier com.sameeriyer.jobapp "$APP" 2>/dev/null \
    && echo "signed (ad hoc)" || echo "WARNING: codesign failed, Desktop access may be refused"
fi

# The app goes on the Desktop, as the real bundle rather than a shortcut to one.
# macOS grants folder permission to a signed bundle; point Full Disk Access at an alias
# or a symlink and the grant lands on nothing, which looks exactly like a broken app.
DEST="$HOME/Desktop/Job App.app"
[ -L "$HOME/Desktop/Job App" ] && rm -f "$HOME/Desktop/Job App"

# Full Disk Access is recorded against the binary. Replacing an unchanged one would throw
# the grant away and send him back to System Settings for nothing, so only the parts that
# actually differ get copied.
if [ "$REUSED" = "1" ]; then
  cp "$APP/Contents/Resources/run.sh" "$DEST/Contents/Resources/run.sh"
  cp "$APP/Contents/Resources/AppIcon.icns" "$DEST/Contents/Resources/AppIcon.icns"
  cp "$APP/Contents/Info.plist" "$DEST/Contents/Info.plist"
  chmod +x "$DEST/Contents/Resources/run.sh"
  echo "updated in place, permission kept"
else
  rm -rf "$DEST"
  cp -R "$APP" "$DEST"
  codesign --force --sign - --identifier com.sameeriyer.jobapp "$DEST" 2>/dev/null || true
  echo "NOTE: the launcher binary changed, so macOS will ask for Full Disk Access again"
fi
touch "$APP" "$DEST"

# LaunchServices caches what it knows about a bundle, and copying files into one behind
# its back leaves that cache stale. The symptom is a double-click that does nothing at
# all: no window, no error, not even a line in the log, and `open` failing with -600.
LSREG=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister
[ -x "$LSREG" ] && "$LSREG" -f "$DEST" 2>/dev/null && echo "re-registered with LaunchServices"

echo "built:     $APP"
echo "installed: $DEST"
