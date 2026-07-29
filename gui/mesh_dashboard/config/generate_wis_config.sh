#!/usr/bin/env bash
# generate_wis_config.sh — regenerates the WIS config from ActTypes.idl.
#
# ActTypes.idl (harness_v2/datamodel/) is the single source of truth for all wire types;
# this script produces the DDS-XML type dialect WIS needs via `rtiddsgen -convertToXml`
# (proven by spikes/wis_mesh_dashboard/, PASSED 2026-07-21) — never hand-transcribed.
# Run this every time before starting rtiwebintegrationservice, or whenever ActTypes.idl
# changes.
#
# Usage: gui/mesh_dashboard/config/generate_wis_config.sh <output-dir>
#   Writes <output-dir>/ActTypes.xml and <output-dir>/wis_config.xml.
#
# Filesystem-safety rule (repo CLAUDE.md): <output-dir> must be a local filesystem, never
# this repo's vboxsf share, if the share is in use — generated config + WIS's own runtime
# state are runtime artifacts.
set -euo pipefail

if [[ $# -ne 1 ]]; then
    echo "usage: $0 <output-dir>" >&2
    exit 1
fi
OUT_DIR="$1"
mkdir -p "$OUT_DIR"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
DASHBOARD_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export NDDSHOME="${NDDSHOME:-/home/rti/rti_connext_dds-7.7.0}"
RTIDDSGEN="$NDDSHOME/bin/rtiddsgen"
TEMPLATE="$DASHBOARD_DIR/config/wis_config.xml.template"

TYPES_XML="$OUT_DIR/ActTypes.xml"
WIS_CONFIG="$OUT_DIR/wis_config.xml"

echo "[generate_wis_config] generating $TYPES_XML via rtiddsgen -convertToXml..."
"$RTIDDSGEN" -convertToXml -d "$OUT_DIR" "$REPO_ROOT/harness_v2/datamodel/ActTypes.idl" \
    > "$OUT_DIR/rtiddsgen.log" 2>&1
if [[ ! -f "$TYPES_XML" ]]; then
    echo "[generate_wis_config] FAIL: rtiddsgen did not produce $TYPES_XML; see $OUT_DIR/rtiddsgen.log" >&2
    exit 1
fi

echo "[generate_wis_config] splicing types + NDDSHOME into $WIS_CONFIG..."
python3 - "$TYPES_XML" "$TEMPLATE" "$WIS_CONFIG" "$NDDSHOME" <<'PYEOF'
import re
import sys
import xml.etree.ElementTree as ET

types_xml_path, template_path, out_path, nddshome = sys.argv[1:5]
types_xml = open(types_xml_path).read()
m = re.search(r"<types>.*?</types>", types_xml, re.S)
if not m:
    print("no <types>...</types> block found in generated XML", file=sys.stderr)
    sys.exit(1)

template = open(template_path).read()

types_placeholder = "__ROUTER_ADMIN_TYPES__"
if template.count(types_placeholder) != 1:
    print(f"expected exactly one {types_placeholder} placeholder, "
          f"found {template.count(types_placeholder)}", file=sys.stderr)
    sys.exit(1)
spliced = template.replace(types_placeholder, m.group(0))

nddshome_placeholder = "__NDDSHOME__"
if spliced.count(nddshome_placeholder) != 1:
    print(f"expected exactly one {nddshome_placeholder} placeholder, "
          f"found {spliced.count(nddshome_placeholder)}", file=sys.stderr)
    sys.exit(1)
spliced = spliced.replace(nddshome_placeholder, nddshome)

open(out_path, "w").write(spliced)

# Well-formedness only -- WIS's own startup is the schema-validity arbiter
# (repo's "build/run is the arbiter" rule); this just catches gross XML mistakes early.
try:
    ET.fromstring(spliced)
except ET.ParseError as e:
    print(f"spliced config is not well-formed XML: {e}", file=sys.stderr)
    sys.exit(1)
PYEOF

echo "[generate_wis_config] wrote $WIS_CONFIG"
