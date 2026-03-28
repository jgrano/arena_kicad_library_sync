#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
PLUGIN_NAME="arena_kicad_library_sync"

echo "=== Installing Arena KiCad Library Sync ==="

# Detect KiCad version and plugins directory
detect_plugins_dir() {
    local system
    system=$(uname -s)

    if [ "$system" = "Darwin" ]; then
        # macOS — check for KICAD_DOCUMENTS_HOME first
        if [ -n "${KICAD_DOCUMENTS_HOME:-}" ]; then
            echo "$KICAD_DOCUMENTS_HOME/scripting/plugins"
            return
        fi
        # Try common versions
        for ver in 10.0 9.0 8.0; do
            local dir="$HOME/Documents/KiCad/$ver/scripting/plugins"
            if [ -d "$(dirname "$dir")" ]; then
                echo "$dir"
                return
            fi
        done
        echo "$HOME/Documents/KiCad/9.0/scripting/plugins"

    elif [ "$system" = "Linux" ]; then
        for ver in 10.0 9.0 8.0; do
            local dir="$HOME/.local/share/kicad/$ver/scripting/plugins"
            if [ -d "$(dirname "$dir")" ]; then
                echo "$dir"
                return
            fi
        done
        echo "$HOME/.local/share/kicad/9.0/scripting/plugins"

    else
        # Windows (Git Bash / MSYS)
        echo "$USERPROFILE/Documents/KiCad/9.0/scripting/plugins"
    fi
}

PLUGINS_DIR=$(detect_plugins_dir)
DEST="$PLUGINS_DIR/$PLUGIN_NAME"

echo "Plugin directory: $PLUGINS_DIR"
echo "Install target:   $DEST"
echo ""

# Create plugins directory if needed
mkdir -p "$PLUGINS_DIR"

# Remove old installation
if [ -d "$DEST" ] || [ -L "$DEST" ]; then
    echo "Removing existing installation..."
    rm -rf "$DEST"
fi

# Copy plugin files
echo "Copying plugin files..."
mkdir -p "$DEST"
cp -r "$PROJECT_DIR/$PLUGIN_NAME/"* "$DEST/"

# Install Python dependencies into KiCad's Python
echo ""
echo "Installing Python dependencies..."
echo "(If this fails, you may need to install them manually)"

# Try to find KiCad's Python
KICAD_PYTHON=""
if [ "$(uname -s)" = "Darwin" ]; then
    for app in "/Applications/KiCad/KiCad.app" "/Applications/KiCad 9.0/KiCad.app" "/Applications/KiCad 10.0/KiCad.app"; do
        local_py="$app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3"
        if [ -f "$local_py" ]; then
            KICAD_PYTHON="$local_py"
            break
        fi
    done
fi

if [ -n "$KICAD_PYTHON" ]; then
    echo "Using KiCad Python: $KICAD_PYTHON"
    "$KICAD_PYTHON" -m pip install --user -r "$PROJECT_DIR/requirements.txt" || true
else
    echo "KiCad Python not found. Installing with system pip..."
    pip3 install --user -r "$PROJECT_DIR/requirements.txt" || true
fi

echo ""
echo "=== Installation complete! ==="
echo ""
echo "Next steps:"
echo "  1. Open KiCad"
echo "  2. Go to PCB Editor"
echo "  3. Click 'Refresh Plugins' in the toolbar"
echo "  4. Look for 'Arena PLM Library Sync' in External Plugins"
