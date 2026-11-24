# Startup optimization

Date: 2026-11-20.

**Implemented and isolated-tested in source; not deployed. The mesh remains
stopped.** No persistent router binary was rebuilt or replaced.

## Key takeaways

1. Suppress unnecessary **application-reader** startup ACKNACKs while retaining
   ordinary reliable repair.
2. Do not equate application endpoints with **SEDP endpoint discovery**.
   Discovery acknowledgments remain necessary and were not suppressed by the
   participant property in our tests.
3. Spread container gateway launches over **one full second**, rather than
   releasing every gateway at once.
4. Configure **0–1 second discovery heartbeat-response delays**, not a fixed
   one-second pause. This spreads responses but can increase discovery latency.
5. These controls do not evenly pace every packet, increase link capacity, or
   establish a fleet/radio bandwidth saving.

## What sends the acknowledgments?

ACKNACKs are sent by reliable readers, including internal discovery readers.
There is no separate participant-wide acknowledgment stream.

| Reader or discovery phase | Reliability | Finding |
|---|---|---|
| Application WAN readers | Reliable where configured | Startup/base-zero ACKNACKs suppressed; ordinary repair retained |
| SEDP publication/subscription discovery | Reliable | Startup requests still observed |
| Classic SPDP participant discovery | Best effort | No reliable ACKNACK exchange |
| SPDP2 bootstrap | Best effort | No reliable ACKNACK exchange |
| SPDP2 participant configuration | Reliable | Startup requests still observed |

Connext AI gave conflicting descriptions of the participant property's scope.
Installed 7.7 manuals and actual packet captures are the basis for these
conclusions, not the presence of a property in a QoS readback alone.

## Proven suppression and negative controls

The working application DataReader property is:

```xml
<datareader_qos>
  <property><value><element>
    <name>dds.data_reader.protocol.disable_preemptive_nack</name>
    <value>true</value>
  </element></value></property>
</datareader_qos>
```

It is already in the current WAN reader profile source and remains enabled.
Native inheritance tests cover Event/State/Periodic and Routing Service aliases;
LAN profiles remain unchanged. Normal positive acknowledgments are not disabled.

Earlier repeated four-way controls produced:

| Application-reader setting | Participant setting | Application startup requests | SEDP startup requests |
|---|---|---:|---:|
| Off | Off | 9 | 4 |
| On | Off | 0 | 4 |
| Off | On | 9–11 | 4 |
| On | On | 0 | 4 |

The participant control was
`dds.participant.inter_participant_data_reader.protocol.disable_preemptive_nack`.
Two SEDP categories each contributed two requests. All 64 commands arrived
across eight trials, including a deliberately lost sequence repaired in every
trial. A separate minimal Python comparison of classic SPDP + SEDP against
SPDP2 + SEDP retained the same SEDP requests with this property false or true.
SPDP2 alone therefore did not explain its lack of observed effect.

No new participant-suppression property is added by this change. Existing
properties inherited from the compact-discovery snippet are preserved; that
snippet already supplies participant-level preemptive flags. Their presence
must not be presented as proof of suppression.

See the [detailed spike record](../spikes/preemptive_acknack/README.md).

## Current WAN QoS: discovery response spreading

The common WAN participant base in
[act_qos_profiles.xml](../harness_v2/qos/act_qos_profiles.xml) now sets:

| `discovery_config` reader | Minimum heartbeat response | Maximum heartbeat response |
|---|---:|---:|
| `publication_reader` (SEDP) | 0 seconds | 1 second |
| `subscription_reader` (SEDP) | 0 seconds | 1 second |
| `participant_configuration_reader` (SPDP2) | 0 seconds | 1 second |

The shape, repeated for each of those reader blocks, is:

```xml
<max_heartbeat_response_delay>
  <sec>1</sec>
  <nanosec>0</nanosec>
</max_heartbeat_response_delay>
```

The corresponding minimum is explicitly zero. This applies through the common
WAN profile inheritance to legacy, two-topic and three-topic participants,
including EMANE variants. The Python binding does not expose a
`participant_configuration_reader` attribute, so tests inspect the **native
effective QoS serialization**, with defaults included, for all three readers.

This changes heartbeat-triggered responses, not every startup transmission.
It does not suppress SPDP2 bootstrap, initial/preemptive requests, TypeLookup,
service-request traffic, or all discovery DATA packets. No uniform statistical
distribution or fleet-wide rate limit is promised. Application-reader response
timing, discovery heartbeat periods/retry budgets, the 300-second timeout,
delivery contracts and WAN flow-controller pacing are unchanged.

## Gateway launches: one-second slots

The owned container lifecycle assigns launch delays using:

```text
delay = gateway slot / number of platforms
```

Control uses slot zero. Platforms use ordered slots 1 through N. For six
platforms:

| Gateway | Delay after its startup gate |
|---|---:|
| Control_20 | 0.000 seconds |
| Platform_30 | 0.167 seconds |
| Platform_31 | 0.333 seconds |
| Platform_32 | 0.500 seconds |
| Platform_33 | 0.667 seconds |
| Platform_34 | 0.833 seconds |
| Platform_35 | 1.000 seconds |

[run_container_baseline.sh](../harness_v2/scripts/run_container_baseline.sh)
generates `WAN_STARTUP_DELAY_SEC` for each service, using actual topology order
rather than assuming contiguous IDs.
[node-entrypoint.sh](../docker/connext-7.7/node-entrypoint.sh) validates the
0–1 second input and sleeps **after RF readiness, before either gateway backend
starts**. The first gateway does not incur a one-second wait.

Container scheduling, readiness polling and native initialization add timing
variation; the slots do not synchronize clocks or guarantee exact packet times.
Standalone entrypoint invocations without a generated delay retain zero delay.
The host-process lifecycle is unchanged.

## Fresh qualification

### Seven-participant native WAN capture

Two private Python-only trials used seven participants and effective WAN QoS:

| Observation | Immediate control | Optimized |
|---|---:|---:|
| First-to-last participant creation | 0.128 seconds | 0.995 seconds |
| Smallest observed heartbeat-to-ACKNACK gap | 0.005 ms | 5.589 ms |
| Largest observed heartbeat-to-ACKNACK gap | 0.632 ms | 996.331 ms |
| Occupied 100 ms gap bins within 0–1 second | 1 of 10 | 10 of 10 |
| Mutual discovery | All seven | All seven |
| Capture drops | 0 | 0 |

The control explicitly overrides just the three response intervals to zero
and starts participants without slots. The optimized case uses the current
WAN participant profile and seven evenly spaced launch targets.
The gap is measured from the latest preceding matching heartbeat; it is an
observed wire interval, not a proof of causal timer attribution for every packet
or a general distribution guarantee. Closing/disposal traffic is excluded.
This is not a full EMANE mesh or application-throughput test.

### Regression gates

- **10 targeted native-QoS/lifecycle tests passed**, including native
  0–1 second profile inheritance, one- and six-platform slot generation,
  and actual entrypoint launch timing using fake gateways without DDS.
- **5 packet-decoder tests passed**, including vendor-specific SPDP2 IDs.
- The actual persistent Routing Service processor rerun retained **9 to 0**
  application startup suppression and delivered **8/8 commands in each variant**,
  including intentional loss and ordinary repair.
- Both expected invalid-configuration controls were rejected.
- Response jitter exposed an existing observer-fixture race: the first gateway
  was recorded after its writers appeared but before its readers appeared.
  The fixture now waits for all three writers **and** all three readers.

Reproduce without a live mesh:

```sh
# Seven native participants, zero-delay control versus optimized WAN QoS
bash spikes/preemptive_acknack/run_startup.sh --optimized

# Actual processor application suppression and loss-repair regression
bash spikes/preemptive_acknack/run.sh
```

Run the targeted unit modules in the established Connext container:
`python3 -m unittest -v harness_v2.qos.test_wan_policy router.routing_service.test_mesh_launcher`.

Retained local evidence:

- `debug/test_reports/startup_optimization_20261120/report.json` and effective
  native QoS XML; captures under `debug/pcap/startup_optimization_20261120/`.
- The regenerated processor evidence under
  `debug/test_reports/preemptive_acknack_20261119/` and its matching capture root.

No host DDS, live mesh restart, generated live-config editing, or persistent
binary replacement was performed. A future deployment must use the owned
coordinated lifecycle and regenerate Routing Service XML from the updated
profiles. Rendered deployments do not reload profile source changes by themselves.

## Deliverables and references

- [Startup optimization slides](slides/startup-optimization.md),
  [PDF](slides/startup-optimization.pdf),
  [PowerPoint](slides/startup-optimization.pptx).
- [Discovery reader policy, Connext 7.7](https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/manuals/connext_dds_professional/users_manual/users_manual/DISCOVERY_CONFIG_Qos.htm).
- [Reader response timing, Connext 7.7](https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/manuals/connext_dds_professional/users_manual/users_manual/DATA_READER_PROTOCOL_Qos.htm).
- [SPDP2 phases, Connext 7.7](https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/manuals/connext_dds_professional/users_manual/users_manual/Simple_Participant_Discovery_2.htm).
