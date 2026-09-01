#!/bin/sh
set -eu

ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
SOURCE="$ROOT/crestron/tsw-1060-launcher/src"
OUTPUT="$ROOT/crestron/tsw-1060-launcher/dist/WarehouseInventoryLauncher.ch5z"
TOKEN=${WAREHOUSE_PANEL_TOKEN:-}

case "$TOKEN" in
  *[!0-9A-Fa-f]*|'')
    echo "WAREHOUSE_PANEL_TOKEN must be a private 64-character hex value" >&2
    exit 1
    ;;
esac
[ "${#TOKEN}" -eq 64 ] || {
  echo "WAREHOUSE_PANEL_TOKEN must be a private 64-character hex value" >&2
  exit 1
}

BUILD_DIR=$(mktemp -d "${TMPDIR:-/tmp}/warehouse-ch5.XXXXXX")
case "$BUILD_DIR" in
  "${TMPDIR:-/tmp}"/warehouse-ch5.*) ;;
  *) echo "Unexpected temporary directory" >&2; exit 1 ;;
esac
trap 'rm -rf -- "$BUILD_DIR"' EXIT HUP INT TERM

mkdir -p "$BUILD_DIR/src" "$(dirname -- "$OUTPUT")"
cp -R "$SOURCE/." "$BUILD_DIR/src"
perl -0pi -e 's/__WAREHOUSE_PANEL_TOKEN__/$ENV{WAREHOUSE_PANEL_TOKEN}/g' "$BUILD_DIR/src/launcher.js"
grep -F "$TOKEN" "$BUILD_DIR/src/launcher.js" >/dev/null

(cd "$BUILD_DIR/src" && zip -0 -q -r "$BUILD_DIR/WarehouseInventoryLauncher.ch5" .)
SHA=$(shasum -a 256 "$BUILD_DIR/WarehouseInventoryLauncher.ch5" | awk '{print $1}')
MODIFIED=$(date -u +%Y-%m-%dT%H:%M:%S.000Z)
printf '{"projectname":"WarehouseInventoryLauncher.ch5","modifiedtime":"%s","sha-256":"%s","target":"TSW-1060","resolution":"1280x800"}\n' \
  "$MODIFIED" "$SHA" > "$BUILD_DIR/WarehouseInventoryLauncher_manifest.json"
(cd "$BUILD_DIR" && zip -q WarehouseInventoryLauncher.ch5z WarehouseInventoryLauncher.ch5 WarehouseInventoryLauncher_manifest.json)
cp "$BUILD_DIR/WarehouseInventoryLauncher.ch5z" "$OUTPUT"
unzip -t "$OUTPUT" >/dev/null
printf 'Built %s\n' "$OUTPUT"
