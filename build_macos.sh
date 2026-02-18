#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# build_macos.sh  —  Leo Health  macOS .app + .dmg builder
#
# Creates a fully self-contained macOS application that your mom (or anyone)
# can double-click to open the Leo Health dashboard in their browser.
# No Python installation required on the target Mac.
#
# Requirements (developer machine only):
#   • macOS 12+
#   • Python 3.9+ with pip
#   • ~300 MB free disk space for the build
#
# Usage:
#   chmod +x build_macos.sh
#   ./build_macos.sh
#
# Output:
#   dist/LeoHealth.dmg   ← share this with anyone
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

APP_NAME="Leo Health"
BUNDLE_ID="io.leo-health.dashboard"
VERSION="1.0.0"
ENTRY="leo_health/dashboard.py"
DMG_OUT="dist/LeoHealth.dmg"
ICON_SRC="build/AppIcon.png"          # optional — we generate one if missing

BOLD="\033[1m"; RESET="\033[0m"; GREEN="\033[32m"; RED="\033[31m"; DIM="\033[2m"
info()  { echo -e "  ${BOLD}${GREEN}▸${RESET} $*"; }
step()  { echo; echo -e "  ${BOLD}$*${RESET}"; }
warn()  { echo -e "  ${BOLD}${RED}⚠${RESET}  $*"; }
die()   { echo -e "\n  ${RED}✗  $*${RESET}\n"; exit 1; }

echo
echo -e "  ${BOLD}🦁  Leo Health — macOS Build${RESET}"
echo -e "  ${DIM}Building a self-contained .app + .dmg${RESET}"
echo

# ── Sanity checks ──────────────────────────────────────────────────────────────
[[ "$(uname)" == "Darwin" ]] || die "Must run on macOS"
command -v python3 >/dev/null 2>&1 || die "python3 not found"
command -v pip3    >/dev/null 2>&1 || die "pip3 not found"

PYTHON_VER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
info "Python $PYTHON_VER detected"

# ── Install PyInstaller ────────────────────────────────────────────────────────
step "Installing PyInstaller (build-time only)…"
pip3 install pyinstaller --quiet --upgrade
info "PyInstaller ready"

# ── Generate a simple icon if none exists ─────────────────────────────────────
mkdir -p build dist

if [[ ! -f "$ICON_SRC" ]]; then
  step "Generating placeholder icon…"
  python3 - <<'PYEOF'
import struct, zlib, base64

# Minimal 512×512 PNG with gradient and lion emoji placeholder
# We generate a simple purple-to-red gradient PNG programmatically.
def make_png(size=512):
    import sys
    w = h = size
    rows = []
    for y in range(h):
        row = [0]  # filter type None
        for x in range(w):
            # radial gradient
            dx = (x - w/2) / (w/2)
            dy = (y - h/2) / (h/2)
            d  = min(1.0, (dx*dx + dy*dy)**0.5)
            r  = int(255 * (1-d) * 0.9 + d * 0.2)  # fade from bright
            # Purple-red gradient: red channel increases with y, blue with x
            R  = int(180 + 75 * (y/h))
            G  = int(50  + 40 * (1-d))
            B  = int(240 - 100 * (y/h))
            A  = 255 if d < 0.90 else int(255*(1-(d-0.90)/0.10))
            row += [max(0,min(255,R)), max(0,min(255,G)), max(0,min(255,B)), max(0,min(255,A))]
        rows.append(bytes(row))

    def chunk(name, data):
        c = zlib.crc32(name + data) & 0xffffffff
        return struct.pack('>I', len(data)) + name + data + struct.pack('>I', c)

    sig  = b'\x89PNG\r\n\x1a\n'
    ihdr = chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0))
    raw  = b''.join(zlib.compress(r, 6) for r in rows)
    # Use single compress across all rows for smaller file
    combined = b''.join(rows)
    idat = chunk(b'IDAT', zlib.compress(combined))
    iend = chunk(b'IEND', b'')
    return sig + ihdr + idat + iend

with open('build/AppIcon.png', 'wb') as f:
    f.write(make_png(512))
print("  ▸ Placeholder icon generated")
PYEOF
fi

# Convert PNG → .icns (macOS iconutil)
step "Creating .icns icon…"
ICONSET="build/AppIcon.iconset"
mkdir -p "$ICONSET"

# Generate required sizes
for SIZE in 16 32 64 128 256 512; do
  sips -z $SIZE $SIZE "$ICON_SRC" --out "${ICONSET}/icon_${SIZE}x${SIZE}.png"      >/dev/null 2>&1 || true
  DOUBLE=$((SIZE*2))
  sips -z $DOUBLE $DOUBLE "$ICON_SRC" --out "${ICONSET}/icon_${SIZE}x${SIZE}@2x.png" >/dev/null 2>&1 || true
done

ICNS="build/AppIcon.icns"
iconutil -c icns "$ICONSET" -o "$ICNS" 2>/dev/null || {
  warn "iconutil failed — building without custom icon"
  ICNS=""
}
[[ -f "$ICNS" ]] && info "Icon created: $ICNS"

# ── Write PyInstaller spec ─────────────────────────────────────────────────────
step "Writing PyInstaller spec…"
SPEC_FILE="build/LeoHealth.spec"

cat > "$SPEC_FILE" <<SPEC
# -*- mode: python ; coding: utf-8 -*-
import os, sys
block_cipher = None

a = Analysis(
    ['${ENTRY}'],
    pathex=['.'],
    binaries=[],
    datas=[],
    hiddenimports=['sqlite3', 'http.server', 'socketserver', 'threading',
                   'webbrowser', 'tkinter', 'tkinter.ttk'],
    hookspath=[],
    runtime_hooks=[],
    excludes=['numpy', 'pandas', 'matplotlib', 'PIL', 'cv2'],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name='Leo Health',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,   # no terminal window
    disable_windowed_traceback=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe, a.binaries, a.zipfiles, a.datas,
    strip=False, upx=False,
    upx_exclude=[],
    name='Leo Health',
)
app = BUNDLE(
    coll,
    name='Leo Health.app',
    icon='${ICNS:-}',
    bundle_identifier='${BUNDLE_ID}',
    version='${VERSION}',
    info_plist={
        'NSHighResolutionCapable': True,
        'LSUIElement': False,
        'CFBundleDisplayName': 'Leo Health',
        'CFBundleShortVersionString': '${VERSION}',
        'NSHumanReadableCopyright': 'Leo Health — your data, your machine.',
        'NSRequiresAquaSystemAppearance': False,
    },
)
SPEC

info "Spec written: $SPEC_FILE"

# ── Build the .app ─────────────────────────────────────────────────────────────
step "Building .app bundle (this takes ~60 seconds)…"
rm -rf dist build/Leo\ Health build/__pycache__

pyinstaller "$SPEC_FILE" \
  --distpath dist \
  --workpath build/pyinstaller_work \
  --noconfirm \
  --log-level WARN

APP_PATH="dist/Leo Health.app"
[[ -d "$APP_PATH" ]] || die ".app not found after build — check PyInstaller output above"
info "App built: $APP_PATH"

# ── Create DMG ────────────────────────────────────────────────────────────────
step "Creating DMG…"
DMG_STAGE="/tmp/leo_health_dmg_$$"
rm -rf "$DMG_STAGE"
mkdir -p "$DMG_STAGE"

cp -r "$APP_PATH" "$DMG_STAGE/"
ln -s /Applications "$DMG_STAGE/Applications"

rm -f "$DMG_OUT"
hdiutil create \
  -srcfolder "$DMG_STAGE" \
  -format UDZO \
  -volname "Leo Health" \
  -fs HFS+ \
  -ov \
  "$DMG_OUT" >/dev/null

rm -rf "$DMG_STAGE"

[[ -f "$DMG_OUT" ]] || die "DMG creation failed"

DMG_MB=$(du -m "$DMG_OUT" | awk '{print $1}')
info "DMG created: $DMG_OUT (${DMG_MB} MB)"

# ── Done ───────────────────────────────────────────────────────────────────────
echo
echo -e "  ${BOLD}${GREEN}✓  Build complete!${RESET}"
echo
echo -e "  📦  ${BOLD}dist/LeoHealth.dmg${RESET} (${DMG_MB} MB)"
echo
echo -e "  ${DIM}To install:${RESET}"
echo "     1. Open LeoHealth.dmg"
echo "     2. Drag 'Leo Health' → Applications"
echo "     3. Double-click Leo Health to launch"
echo
echo -e "  ${DIM}⚠  Gatekeeper note:${RESET}"
echo "     The app isn't code-signed, so macOS will show a security warning."
echo "     To open it the first time:"
echo "     Right-click the app → Open → click 'Open' in the dialog"
echo
echo -e "  ${DIM}Mom's data stays on her Mac — no internet, no cloud. 🔒${RESET}"
echo
