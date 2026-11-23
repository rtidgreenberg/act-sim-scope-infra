#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."
mkdir -p debug/test_reports
report=$(mktemp "$PWD/debug/test_reports/wan_topic_discovery.XXXXXX.json")
image=connext:7.7.0
image_id=$(docker image inspect "$image" --format '{{.Id}}')
printf 'Report: %s\n' "$report" >&2
docker run --rm --network none --ipc private --cap-add NET_RAW \
  --read-only --tmpfs /tmp:exec \
  -v "$PWD:/workspace:ro" \
  -v "${CONNEXT_SHARED_DIR:-/home/dgreenberg/rti_connext_dds-7.7.0}:/shared:ro" \
  -w /workspace/spikes/wan_topic_discovery \
  -e PYTHONDONTWRITEBYTECODE=1 -e RTI_LICENSE_FILE=/shared/rti_license.dat \
  -e "SPIKE_IMAGE_ID=$image_id" \
  "$image_id" python3 measure.py "$@" > "$report"
cat "$report"
