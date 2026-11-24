# Disable startup preemptive ACKNACKs: actual Routing Service spike

The later [startup optimization record](../../docs/startup-optimization.md)
documents the current source policy and its separate one-second response/launch
spreading qualification. Reproduce the seven-participant native comparison with
`bash spikes/preemptive_acknack/run_startup.sh --optimized`. Earlier counts below
retain their original configuration scope; discovery suppression remains
unresolved.

**Executed 2026-11-19: PASS. No deployment, rebuild, commit or live-mesh change.**

## Result

| Wire observation | Baseline | Disabled |
|---|---:|---:|
| A → B Event writer: initial base-zero ACKNACK before any heartbeat | 1 | 0 |
| A → B Event writer: all ACKNACKs | 3 | 2 |
| Both gateways/all user WAN writers, including priority-channel IDs: base-zero ACKNACKs | 9 | 0 |
| Both gateways/all user WAN writers: all ACKNACKs | 11 | 2 |
| Explicit requests for lost Event sequence 2 | 1 | 1 |
| Intentionally dropped original DATA_SESSION sequence 2 | 1 | 1 |
| Sequence 2 seen on wire, **after** its repair request | 1 | 1 |
| Original application commands received with exact keys/opcodes/bytes | 8/8 | 8/8 |

These are decoded protocol facts, not an inference from packet-count reduction.
The baseline initial ACKNACK has `bitmapBase=0`, `numBits=0`, flags `0x01`
(little-endian, Final=false), and precedes **every** captured heartbeat for that
writer/reader pair. The disabled case has none. Its ordinary repair ACKNACK
has `bitmapBase=2`, seven bitmap positions with only sequence 2 missing,
flags `0x01`, and follows a non-final HEARTBEAT_SESSION advertising sequences
1–8. The subsequent DATA_SESSION sequence 2 occurs after that request; the
final positive ACKNACK has base 9, empty bitmap, flags `0x03` (Final=true).
The decoder handles standard and RTI session DATA/HEARTBEAT forms and has four
focused passing tests, including big-endian ACKNACK and bitmap bit order.

**Scope:** this proves suppression of the base-zero startup/preemptive form
on the tested native reliable WAN readers, including priority-channel requests.
It does **not** disable all ACKNACKs, discovery acknowledgments, or prove that
every possible unsolicited/nonzero-base ACKNACK form is suppressed. It does
not qualify congestion, outage recovery time, late durable history, RF airtime,
or production behavior under every startup race.

## Exact external XML

The empirically working **DataReader** property in installed Connext 7.7.0 is:

```xml
<datareader_qos>
  <property>
    <value>
      <element>
        <name>dds.data_reader.protocol.disable_preemptive_nack</name>
        <value>true</value>
      </element>
    </value>
  </property>
</datareader_qos>
```

`qos.xml` supplies inherited profiles for Event, State and Periodic. Only the
three native WAN **reader** profile selections differ between baseline and
disabled XML; the runner asserts this mechanically. Effective QosProvider
readback records the property, RELIABLE reliability and
`disable_positive_acks=false`. It does not construct fixed QoS in Python/C++.
The production WAN profiles now enable suppression, so the spike baseline
explicitly overrides the property to `false` to preserve its before/after control.

### MCP discrepancies, checked against this installation

Before querying MCP, `git pull --ff-only origin main` in
`docs/connext-ai-issues` completed “Already up to date”, commit
`dd90b8b09d2a59f81c44eab5ed3c19060f5212cf`. The initial plain `git pull`
could not run because the existing local branch has no tracking branch.
No matching preemptive/ACKNACK correction existed.

Two generic `askConnext` queries were used; no project source or runtime data
was submitted:

1. MCP initially recommended `<disable_preemptive_acknack>` under
   `protocol/rtps_reliable_reader`. Installed QosProvider **rejects** that
   element: `Unexpected tag 'disable_preemptive_acknack'`.
2. After being shown the installed header symbol, MCP corrected the element
   advice but recommended
   `dds.participant.inter_participant_data_reader.protocol.disable_preemptive_nack`
   on a DataReader. QosProvider can read that string, but actual Routing
   Service reader creation **rejects** it as `Unexpected property`.
   The runner retains this expected native negative control.

The installed `include/ndds/property/property_propertyNames.h` instead defines
`PROPERTY_NAME_DDS_DATA_READER_PROTOCOL_DISABLE_PREEMPTIVE_NACK` as the short
`dds.data_reader...` name above. Its use is verified by actual before/after
traffic, not merely XML acceptance. The participant-prefixed property is a
different entity scope; this spike does not reject its validity at participant
scope. The discrepancies are now recorded locally in the shared correction log;
no correction-log commit, push or parent submodule re-pin was made.

MCP's reference links (context only, **not** empirical proof):
[preemptive messages KB](https://community.rti.com/kb/what-are-preemptive-acknack-and-preemptive-heartbeat-messages),
[7.7 reader protocol](https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/manuals/connext_dds_professional/users_manual/users_manual/DATA_READER_PROTOCOL_Qos.htm),
[7.7 properties by entity](https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/manuals/connext_dds_professional/properties_reference/property_entity.html).

## Controlled workload and caveats

Two actual Routing Services load the **persistent deployed** processor library.
The installed native config helper prepares one full-type `DeviceCommand`
binding using the repository's existing full-type qualification fixture.
Each gateway owns exactly three native WAN writer/reader pairs:
Event/State/Periodic. A builtin-only observer identifies their exact GUIDs;
it creates no application WAN endpoints. The Event writer exists first;
gateway B starts one second after A's discovery completes. Both WAN readers
and application routes must be ready before eight original LAN commands are
written at 30 ms intervals. WAN domain is 102; LAN domains are 103 and 105.

For a reproducible startup stimulus, **both** variants share external writer
XML disabling its preemptive heartbeat optimization and using 2-second
normal/fast/late-joiner heartbeat periods. Other production defaults,
including priority channels and asynchronous publishing, are inherited.
Thus this is a controlled comparison, **not** an unmodified production
default-traffic benchmark.

`drop.c` is a test-only `LD_PRELOAD` sendto/sendmsg interposer in gateway A.
After discovery selects its exact Event writer entity, it discards one
original DATA/DATA_SESSION with sequence 2 and reports a successful send
to DDS. It logs the drop once; later retransmissions pass normally.
This models one lost outbound datagram, not a radio outage. The packet
capture proves sequence 1, then 3–8, a heartbeat and request for 2, then the
retransmitted 2. Capturing alone cannot observe the discarded original;
the explicit loss log plus protocol ordering and final payload checks
provide that control.

## Reproduce

From the repository root:

```sh
bash spikes/preemptive_acknack/run.sh
```

Requires the existing `connext:7.7.0` image, Docker access through `sg docker`,
and the license-only directory
`/home/dgreenberg/rti_connext_dds-7.7.0`. No dependencies were installed.
The runner refuses mismatched persistent processor/helper hashes. It mounts
the checkout read-only at `/workspace` and the license read-only at `/shared`;
only private `/tmp` scratch and scoped evidence roots are writable.

Isolation is `--network none --ipc private`, loopback only, UDPv4 only. The
bounded `dumpcap` captures just domain-102 UDP ports 32900–33149, on the
private container loopback. `tshark` reads the resulting closed files.
No host DDS, host network capture, live lifecycle action or shared build
directory write is used. Disposable scratch uses the private container's
`/tmp` tmpfs, removed with the container. ExitStack shuts down owned services/observer/
capture on success or exception; `docker --rm` removes the container.
Each run overwrites this spike's own evidence.

## Evidence and identities

Compact evidence: `debug/test_reports/preemptive_acknack_20261119/`:

- `PASS`, `console.log`, `comparison.json`: executed assertions and counts.
- `{baseline,disabled}_decoded.json`: exact ACKNACK bases, bitmap contents,
  flags, count, writer/reader IDs, preceding-heartbeat count and timing;
  plus actual DATA/HEARTBEAT_SESSION rows.
- `{baseline,disabled}_manifest.json`: discovered endpoints, effective XML
  QoS readback, original application payloads, configuration/capture hashes.
- `{baseline,disabled}_{A,B}.xml`: exact prepared Routing Service configs;
  adjacent logs include the one intentional loss per variant.
- `mcp_key_A.log`, `mcp_key_rejection.json`,
  `invalid_element_rejection.json`: retained expected failures.
- `hashes.json`, `image_id.txt`, `installed_preemptive_properties.txt`,
  `cleanup.json` (no owned container, scratch directory or host DDS shared
  memory remained).

Raw owned captures:
`debug/pcap/preemptive_acknack_20261119/{baseline,disabled,mcp_key}.pcapng`.
Successful capture logs report zero capture/dumpcap drops.

SHA-256 identities:

```text
processor 022941caaef2604dd41657da494b9144f17e5bbb7fd1a6c7b816e4dfd007f94b
helper    406e734e0f713443fd4dd2a16bcbd6e4665d9eae583660211abb9fd1820ab6bf
RS app    120a5dca247851aee058dd47872dc962c471fdfd61a2c4aab277f2b4ef7bd96a
image     cbabafb1dab80c66483f092e1770249a6196d974b01592fe3a52979613f5f7a0
```

The runner rechecks all recorded source/binary hashes after qualification.
There is no processor build or production QoS/source edit. The live
six-platform mesh was neither accessed by DDS nor restarted.
Startup request counts vary with discovery scheduling: successful runs observed
9 or 11 base-zero requests; the final production-QoS rerun recorded above
observed 9. Every disabled run observed zero and retained sequence-2 repair.

## Participant-scoped follow-up (2026-11-20)

**Result: `NO_SUPPRESSION_OBSERVED`, not a suppression qualification PASS.**

Tested the exact participant property:

```xml
<domain_participant_qos>
  <property>
    <value>
      <element>
        <name>dds.participant.inter_participant_data_reader.protocol.disable_preemptive_nack</name>
        <value>true</value>
      </element>
    </value>
  </property>
</domain_participant_qos>
```

The installed property-name header and properties reference list this name at
DomainParticipant scope. Actual native participant creation accepted it, and
the created participants' own QoS readback retained the requested false/true
values. Acceptance did **not** establish the requested wire behavior.

Two repeated before/after Routing Service comparisons used the same persistent
processor/helper binaries, SPDP2 + SEDP discovery, and the same Event loss/repair
workload above. Application-reader suppression remained enabled in both
variants, isolating the participant-level change.

Counts below are startup/base-zero ACKNACKs between the two gateway participants,
excluding the discovery observer. **Each of both repetitions** produced:

| Builtin writer being acknowledged | Property false | Property true |
|---|---:|---:|
| SPDP2 participant configuration (`00010182`) | 2 | 2 |
| Service request (`00020082`) | 2 | 2 |
| SEDP publications (`000003c2`) | 2 | 2 |
| SEDP subscriptions (`000004c2`) | 2 | 2 |
| Total across those discovery writers | 8 | 8 |
| Application WAN startup requests | 0 | 0 |
| Complete commands received after intentional sequence-2 loss | 8/8 | 8/8 |

SPDP2 configuration still had one base-zero request preceding any captured
heartbeat for its pair, and still had ordinary positive acknowledgments and
repair requests. All four routed trials explicitly requested the missing Event
sequence 2 before its retransmission; all 32 original command payloads arrived.
This does not measure discovery bandwidth or outage recovery performance.

A separate direct native DDS control, without Routing Service or the processor,
created two participants for each false/true variant. It read back the property
from each **created** participant and reproduced the same 2-to-2 startup counts
for all four builtin categories. Thus the absence of observed suppression was
not confined to the Routing Service rendering path. The effective scope or
conditions under which this property suppresses traffic remain unresolved;
do not infer that it is invalid or ineffective for every discovery configuration.

### Measurement and configuration controls

- The decoder must include vendor-specific builtin entity kinds (`0x82`), not
  just standard builtin kinds (`0xc2`/`0xc3`). Otherwise SPDP2 is omitted. Five
  decoder tests now include this regression. Installed SDK
  `ndds/transport/transport_common.h` identifies `00010182` as the SPDP2
  configuration writer; `ndds/mig/mig_rtps.h` identifies `00020082` as service
  request, **not** participant liveliness.
- Routing Service's inline WAN flow-controller property sequence replaces the
  inherited participant property sequence in this configuration. The spike
  therefore copies the externally authored test property into that inline
  sequence and checks the effective merged participant QoS before startup.
  Checking the base profile alone was insufficient. Rendered before/after
  XML is compared after normalizing only this property and its profile selector.
- Six new captures (four routed trials, two direct native controls) report zero
  capture drops. Persistent binary and source hashes stayed unchanged during
  qualification. All owned containers and private scratch were removed; no
  host RTI/DDS shared-memory files remained. Evidence totals about 1.1 MiB.
- The original application-reader experiment was rerun on 2026-11-20:
  startup requests again fell **9 to 0**, normal Event repair succeeded, both
  variants delivered 8/8 commands, and both expected invalid-setting controls
  were rejected. Its evidence paths below were regenerated after cleanup.

### Reproduce and inspect

```sh
bash spikes/preemptive_acknack/run_participant.sh
```

Uses the same isolated read-only-checkout container pattern as the original
experiment. **Exit status 1 is intentional for the observed negative result:**
ordinary protocol checks passed, but the suppression requirement did not.
Operational exceptions are recorded separately as `FAIL`.

Current retained evidence:

- `debug/test_reports/participant_preemptive_acknack_20261120/report.json`:
  both comparisons, exact builtin counts, native controls, hashes and outcome.
- The same directory's `r{1,2}_participant_{baseline,disabled}_manifest.json`:
  exact discovered endpoints, effective participant properties for both
  gateways, command equality and capture hashes; adjacent XML, builtin decode
  files and logs preserve the executed configurations and packet observations.
- `native_participant_{baseline,disabled}.json`: native participant readback,
  exact prefixes and detailed discovery ACKNACKs.
- `debug/pcap/participant_preemptive_acknack_20261120/`: six scoped captures.
- `debug/test_reports/preemptive_acknack_20261119/` and
  `debug/pcap/preemptive_acknack_20261119/`: original application-reader
  experiment regenerated on 2026-11-20.

No participant suppression was added to production profiles. No persistent
binary was rebuilt, no deployment was performed, and the mesh remains stopped
following the requested debug cleanup.

## Independent reader/participant matrix (2026-11-20)

The subsequent request explicitly tested the two settings independently **and
together**, counting SEDP separately from SPDP2:

- Application readers:
  `dds.data_reader.protocol.disable_preemptive_nack`.
- Participant QoS:
  `dds.participant.inter_participant_data_reader.protocol.disable_preemptive_nack`.

Two repetitions of all four combinations produced these startup/base-zero
ACKNACK counts per trial:

| Reader setting | Participant setting | Application WAN | SEDP publications | SEDP subscriptions |
|---|---|---:|---:|---:|
| Off | Off | 9 / 9 | 2 / 2 | 2 / 2 |
| On | Off | 0 / 0 | 2 / 2 | 2 / 2 |
| Off | On | 11 / 9 | 2 / 2 | 2 / 2 |
| On | On | 0 / 0 | 2 / 2 | 2 / 2 |

Each cell's two counts are repetition 1 / repetition 2, not percentages.
Both SEDP categories retained a startup request before any captured heartbeat
for its pair in every routed trial. SPDP2 configuration and service request
also each retained two startup requests in every trial.

**Application-reader suppression worked; participant-level SEDP suppression was
not observed, including with both settings enabled.** All eight routed trials
delivered 8/8 complete commands (64 total), with one intentional Event sequence-2
drop, an explicit repair request and the subsequent retransmission in each trial.
SEDP positive acknowledgments remained present. Two additional direct native
participant controls again retained both SEDP categories' startup requests.

The effective inline participant properties and selected application-reader
profiles are recorded. Prepared configurations are checked to differ only in
the two tested settings/profile selectors. Five decoder tests passed; all ten
captures reported zero drops; source and persistent binary hashes were unchanged
during the run.

Connext AI's follow-up answer recommended the same participant property for
SEDP, in contrast to its earlier SPDP2-specific description. That claimed SEDP
effect is **not supported by this measured matrix**. No alternative SEDP property
or production configuration change was adopted.

Reproduce:

```sh
bash spikes/preemptive_acknack/run_participant.sh --matrix
```

The retained report is
`debug/test_reports/participant_preemptive_acknack_matrix_20261120/report.json`;
adjacent `r{1,2}_matrix_{none,reader,participant,both}_*` files retain per-trial
manifests, prepared XML, packet decoding and logs. Ten private-loopback captures
are under `debug/pcap/participant_preemptive_acknack_matrix_20261120/`.
The result is explicitly `SEDP_SUPPRESSION_NOT_OBSERVED`, exit status 1,
with `protocol_checks_passed=true`; it is not a successful SEDP suppression gate.
The mesh remains stopped and nothing was deployed.

## Minimal Python discovery comparison (2026-11-20)

To isolate the discovery-protocol question, [startup.py](startup.py) creates
just two Python DomainParticipants. There is no Routing Service, processor,
application topic, writer or reader. [startup_qos.xml](startup_qos.xml) is a
self-contained UDPv4/loopback configuration, independent of production profiles.

Each variant starts A, waits one second, then starts B. Both must discover
exactly one other participant. The test reads the property, discovery-plugin
mask and transport back from each created participant. Eight-second private
captures cover startup; each combination is repeated twice.

| Discovery configuration | Setting false | Setting true |
|---|---:|---:|
| Classic SPDP + SEDP: SEDP publication startup ACKNACKs | 2 | 2 |
| Classic SPDP + SEDP: SEDP subscription startup ACKNACKs | 2 | 2 |
| SPDP2 + SEDP: SEDP publication startup ACKNACKs | 2 | 2 |
| SPDP2 + SEDP: SEDP subscription startup ACKNACKs | 2 | 2 |
| SPDP2 + SEDP: participant-configuration startup ACKNACKs | 2 | 2 |

Both repetitions produced identical startup counts. Service request also
retained two startup ACKNACKs in all variants. Normal positive acknowledgments
remained present for each listed reliable builtin category.

**Switching from SPDP2 to classic SPDP did not make the participant property
suppress SEDP startup requests in this test.** Thus SPDP2 alone does not explain
the earlier negative result. This does not establish a universal unsupported
property claim or qualify application delivery/repair: this experiment has no
application endpoints.

Decoded DATA writer IDs independently confirmed the configured protocol:
classic SPDP `000100c2`, versus SPDP2 bootstrap `00010082` and configuration
`00010182`. The native plugin masks were respectively `00011` and `10010`.
All eight mutual-discovery checks passed, all captures reported zero drops,
and the five existing decoder tests passed. No production binary/configuration
was modified and no owned container or private scratch was retained.

Reproduce:

```sh
bash spikes/preemptive_acknack/run_startup.sh
```

Evidence: `debug/test_reports/discovery_startup_20261120/report.json`, adjacent
per-trial decoded ACKNACKs and capture logs, and eight raw captures under
`debug/pcap/discovery_startup_20261120/`. Report status `COMPLETE` means the
comparison executed and its assertions passed, **not** that suppression worked.
