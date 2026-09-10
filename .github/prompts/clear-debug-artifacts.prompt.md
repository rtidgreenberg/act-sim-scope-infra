---
name: "clear-debug-artifacts"
description: "Clear ACT debug logs and packet captures"
agent: "agent"
---
Clear generated ACT debug artifacts from the repository while preserving scripts and
directory marker files.

Run from the repository root:

```bash
find debug/logs -type f ! -name .gitkeep -delete
find debug/pcap -type f ! -name .gitkeep -delete
find debug/logs -mindepth 1 -type d -empty -delete
find debug/pcap -mindepth 1 -type d -empty -delete
```

Do not stop or kill running processes. Clear artifacts only after the associated mesh,
test, or capture process has stopped. Report the number of files removed and verify that
`debug/logs/.gitkeep` and `debug/pcap/.gitkeep` remain.