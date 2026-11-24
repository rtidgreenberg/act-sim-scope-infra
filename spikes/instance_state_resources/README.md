# Native instance-state resource controls

These Connext 7.7.0 controls characterize bounded reader replacement and the
UMAA-style ten-second disposed-sample delay. They complement, but do not
replace, the actual routed WAN tests in
[contact_disposal_timing](../contact_disposal_timing/README.md).

Findings and interpretation:
[Instance State Through DDIL](../../docs/instance-state-through-ddil.md).

```bash
bash spikes/instance_state_resources/run.sh
```

The runner uses private network/IPC namespaces, UDPv4 domain 218, read-only
source, disposable container `/tmp`, and the supported `connext:7.7.0` image.
Only the owned results directory is writable. It does not use or change the
running mesh.

## Nine native controls

- Two churn controls each write, take, dispose and take 2,000 distinct contacts,
  checking the complete payload, invalid notifications and both key fields.
  Reader capacity is four attached instances. Minimum-state retention compares
  an eight-entry attached-plus-detached bound against disabled detached state.
  An alive sentinel must survive.
- Infinite sample retention preserves an unread disposed contact past eleven
  seconds when a third key pressures the two-instance reader.
- Ten-second sample expiry preserves that contact when pressure occurs at
  8.5 seconds, but allows its unread data/disposal to expire under pressure
  after eleven seconds.
- Infinite and ten-second controls also isolate an unread invalid disposal:
  the initial valid sample has already been taken.
- Read-without-take and outstanding-read-loan controls distinguish
  FULLY_PROCESSED replacement eligibility from an empty queue.
- Every slow-reader control fills the final two slots with alive contacts,
  attempts another key, verifies neither old key is evicted, and requires a
  native sample-lost increment for the failed admission.

The companion writer has a separate finite 4,096-instance allowance so this
is a **reader resource test**, not a low-capacity writer-admission benchmark.
Acknowledgments are batched during churn. Cache snapshots taken after draining
samples measure retained state, not peak queue occupancy or allocated bytes.

Negative controls intentionally produce sample loss: a reader with no eligible
instance slot cannot admit another key. Finite expiry intentionally demonstrates
that a slow consumer can lose a disposal before observing it. A passing control
means those exact expectations were checked; it does not mean the policies
provide lossless delivery.

## Output

[report.json](../../debug/test_reports/instance_state_resources_20261119/report.json)
contains each native result and source hashes. The accompanying
`inputs-and-logs.tar.gz` retains the effective merged XML, build output and
per-case native logs. Runtime files and DDS state disappear with the owned
container.
