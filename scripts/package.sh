#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
VERSION=$(python3 -c "import json; print(json.load(open('$PROJECT_DIR/metadata.json'))['versions'][0]['version'])")
OUTDIR="$PROJECT_DIR/dist"
ZIPNAME="arena-kicad-library-sync-${VERSION}.zip"

echo "=== Packaging Arena KiCad Library Sync v${VERSION} ==="

mkdir -p "$OUTDIR"

# Create KiCad PCM-compatible ZIP
# Structure: plugins/com.emporiaenergy.arena-kicad-library-sync/
TMPDIR=$(mktemp -d)
PLUGIN_DIR="$TMPDIR/plugins/com.emporiaenergy.arena-kicad-library-sync"
mkdir -p "$PLUGIN_DIR"

# Copy plugin files (exclude server, docker, tests, .github)
cp -r "$PROJECT_DIR/arena_kicad_library_sync/"*.py "$PLUGIN_DIR/"
cp -r "$PROJECT_DIR/arena_kicad_library_sync/ui" "$PLUGIN_DIR/"
mkdir -p "$PLUGIN_DIR/resources"
if [ -f "$PROJECT_DIR/arena_kicad_library_sync/resources/icon.png" ]; then
    cp "$PROJECT_DIR/arena_kicad_library_sync/resources/icon.png" "$PLUGIN_DIR/resources/"
fi

# Copy metadata and icon to root
cp "$PROJECT_DIR/metadata.json" "$TMPDIR/"
if [ -f "$PROJECT_DIR/arena_kicad_library_sync/resources/icon.png" ]; then
    cp "$PROJECT_DIR/arena_kicad_library_sync/resources/icon.png" "$TMPDIR/"
fi

# Create ZIP
cd "$TMPDIR"
zip -r "$OUTDIR/$ZIPNAME" . -x "*.pyc" -x "__pycache__/*"

# Compute SHA256
if command -v sha256sum &>/dev/null; then
    SHA=$(sha256sum "$OUTDIR/$ZIPNAME" | cut -d' ' -f1)
elif command -v shasum &>/dev/null; then
    SHA=$(shasum -a 256 "$OUTDIR/$ZIPNAME" | cut -d' ' -f1)
else
    echo "WARNING: No sha256sum available"
    SHA="UNKNOWN"
fi

SIZE=$(wc -c < "$OUTDIR/$ZIPNAME" | tr -d ' ')

echo ""
echo "Package: $OUTDIR/$ZIPNAME"
echo "SHA256:  $SHA"
echo "Size:    $SIZE bytes"

# Update metadata.json with SHA and size
python3 -c "
import json
with open('$PROJECT_DIR/metadata.json', 'r') as f:
    meta = json.load(f)
meta['versions'][0]['download_sha256'] = '$SHA'
meta['versions'][0]['download_size'] = $SIZE
with open('$PROJECT_DIR/metadata.json', 'w') as f:
    json.dump(meta, f, indent=2)
print('Updated metadata.json with SHA256 and size')
"

# Cleanup
rm -rf "$TMPDIR"

echo "Done!"
