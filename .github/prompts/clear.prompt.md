---
name: "clear"
description: "Clear ACT debug logs and packet captures"
agent: "agent"
---
Clear generated ACT debug artifacts from the repository while preserving scripts and
directory marker files.

Run from the repository root:

```bash
find debug/logs -type f ! -name .gitkeep -delete
find debug/pcap -type f ! -name .gitkeep -delete
find debug -maxdepth 1 -type d -name '*_debug' -exec find {} -mindepth 1 -type f ! -name .gitkeep -delete \;
find debug/logs -mindepth 1 -type d -empty -delete
find debug/pcap -mindepth 1 -type d -empty -delete
find debug -maxdepth 1 -type d -name '*_debug' -empty -delete
```

Do not stop or kill running processes. Clear artifacts only after the associated mesh,
test, or capture process has stopped. Retain `debug/test_reports/<test_id>.json` and
`debug/test_reports/<test_id>.html`; they are compact latest-per-test summaries, not raw
artifacts. Report the number of files removed and verify that `debug/logs/.gitkeep` and
`debug/pcap/.gitkeep` remain.