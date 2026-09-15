# EMANE Pathloss Scale and GUI Control

Date: 2026-09-15

This note captures the validated behavior of the dashboard EMANE Experiment controller and the observed RF Pipe pathloss scale in the current container mesh.

## Current RF Pipe Configuration

The active mesh uses EMANE RF Pipe on the WAN leg:

- MAC datarate: `1M` bps
- MAC delay/jitter: `0`
- PHY tx power: `0 dBm`
- PHY antenna gains: `0 dB`
- PHY bandwidth: `1 MHz`
- PHY system noise figure: `4 dB`
- PHY propagation model: `precomputed`
- PHY noise mode: `outofband`
- RF Pipe PCR curve: `/usr/share/emane/xml/models/mac/rfpipe/rfpipepcr.xml`
- PCR curve table has `pktsize="0"`, so packet size is ignored when computing probability of reception.

The live EMANE tables under a `90 dB` bidirectional impairment between Control_20 and Platform_30 showed:

- Control_20 receive table for NEM 2: pathloss `90.0`, rx power `-90.0 dBm`
- Platform_30 receive table for NEM 1: pathloss `90.0`, rx power `-90.0 dBm`
- Application delivery remained `100%` in both directions.

## Why 90 dB Can Look Fine and 100 dB Can Degrade

EMANE physical-layer receive power is:

```text
rxPower = txPower + txAntennaGain + rxAntennaGain - pathloss
```

With this mesh's current values, that reduces to:

```text
rxPower = 0 + 0 + 0 - pathloss
```

Receiver sensitivity is:

```text
rxSensitivity = -174 + systemNoiseFigure + 10 * log10(bandwidth)
```

For `systemNoiseFigure=4` and `bandwidth=1,000,000`, sensitivity is:

```text
-174 + 4 + 60 = -110 dBm
```

So the expected receive/SINR scale is approximately:

| Pathloss | Rx power | Approx SINR margin | RF Pipe PCR POR |
| ---: | ---: | ---: | ---: |
| `0 dB` | `0 dBm` | `110 dB` | `100%` |
| `60 dB` | `-60 dBm` | `50 dB` | `100%` |
| `90 dB` | `-90 dBm` | `20 dB` | `100%` |
| `100 dB` | `-100 dBm` | `10 dB` | `50%` |
| `110 dB` | `-110 dBm` | `0 dB` | `0%` |

The packaged RF Pipe PCR curve maps SINR linearly from `0 dB -> 0%` to `20 dB -> 100%`, with rows every `0.5 dB`. That makes the application-visible behavior threshold-like: values below roughly `90 dB` may not reduce packet completion, while `100 dB` is already in the lossy region.

## Message Size and Rate

The current dashboard traffic is far below the configured RF Pipe datarate. A representative live sample under `90 dB` showed about `99` Domain 200 packets and `15.9 KB` aggregate over `1.13 s` across the observers. This is not saturating a `1 Mbps` RF Pipe link.

Since the PCR curve uses `pktsize="0"`, message size does not change the probability-of-reception mapping for this RF Pipe configuration. Packet size/rate may still matter for queues, DDS reliability, and application behavior, but it is not why `90 dB` stayed at `100%` delivery in the observed run.

## Validated GUI Command Forms

The dashboard should use pair-specific target/reference commands for selected links:

```text
emaneevent-pathloss -i eth0 -g 224.1.2.8 -p 45702 <tx_nem> <db> -t <rx_nem> -r <tx_nem>
```

The same command with `0` resets that direction.

For bidirectional GUI impairment, send the pair-specific command twice:

```text
<source_nem> <db> -t <destination_nem> -r <source_nem>
<destination_nem> <db> -t <source_nem> -r <destination_nem>
```

Do not use a broad range such as `1:4` for selected-pair GUI impairment. EMANE's range syntax creates a receiver/transmitter matrix for every NEM pair in that contiguous range, so wide ranges affect unrelated pairs.

The harness nominal setup uses the ascending range form for initial bidirectional RF matrix setup:

```text
emaneevent-pathloss -i eth0 -g 224.1.2.8 -p 45702 <low_nem>:<high_nem> 0
```

Reversed ranges such as `2:1` are invalid.

## Validation Checklist

After any GUI impairment or reset, validate all of the following before trusting the result:

- `/api/emane_controller` records the expected source, destination, direction, dB value, and timestamp.
- `/api/emane_stats` shows fresh samples from all expected contributors.
- `/api/traffic_stats` still shows Domain 200 discovery and data traffic.
- `/api/delivery_stats` shows the expected route-level percentage change after the delivery window rolls forward.
- `/api/mesh_status` shows every expected platform as `PRESENCE_ALIVE` and `ROUTER_OK`.
- Unrelated peers stay alive during the impairment.

A good live GUI check is:

1. Apply `100 dB` to one direction on a selected pair.
2. Observe RF drops and a delivery drop on the selected route.
3. Reset that same direction to `0 dB`.
4. Wait for the delivery window to roll forward.
5. Confirm the selected route returns to `100%` and unrelated peers remain alive.

## Better Ways to Get a Smooth Gradient

Raw RF Pipe pathloss is a physical/SINR input. It is not a smooth application-delivery control in this configuration because the PCR curve maps SINR to probability of reception and DDS/router behavior can absorb losses.

Better options for an operator-facing gradient:

1. **Tune or replace the RF Pipe PCR curve.** A custom curve can move the transition region away from the narrow `90-110 dB` band or make it less abrupt. This preserves RF Pipe semantics but requires validating the curve against desired packet completion behavior.

2. **Use CommEffect for precise impairment scenarios.** Existing planning docs already identify CommEffect as the better mechanism for exact per-link loss, latency, jitter, and bandwidth directives. Use RF Pipe pathloss for physical/range-style behavior; use CommEffect when the operator needs a predictable `10%`, `30%`, `60%` degradation slider.

3. **Expose calibrated presets instead of raw dB.** For this current RF Pipe configuration, useful presets are closer to:
   - `Nominal`: `0 dB`
   - `Near threshold`: `90 dB`
   - `Degraded`: `95-105 dB`
   - `Cut`: `110 dB+`

4. **Use closed-loop calibration.** If the UI must show a gradient while still using pathloss, run short calibration probes per topology and map slider values to dB settings that produce observed delivery bands. This should be treated as empirical, not as a fixed physical guarantee.

## CommEffect Integration Notes

CommEffect is now the preferred dashboard control for predictable latency, loss, and bitrate experiments. It is an EMANE shim, so the generated NEM must include a shim layer in addition to the existing virtual transport, RF Pipe MAC, and PHY:

```xml
<nem name="... RF Pipe NEM">
   <transport definition="transvirtual.xml"/>
   <shim definition="commeffectshim.xml"/>
   <mac definition="rfpipemac.xml"/>
   <phy>...</phy>
</nem>
```

The validated shim file for this installed EMANE version is:

```xml
<!DOCTYPE shim SYSTEM "file:///usr/share/emane/dtd/shim.dtd">
<shim name="CommEffect shim" library="commeffectshim">
   <param name="defaultconnectivitymode" value="on"/>
   <param name="enablepromiscuousmode" value="off"/>
</shim>
```

Use `defaultconnectivitymode=on` so nominal RF Pipe traffic flows before any CommEffect event arrives. With it set to `off`, the shim accepted events but dropped ordinary packets as `No Profile`, which made startup discovery fragile.

The dashboard uses pair-specific event commands in the same source/destination direction model as pathloss:

```text
emaneevent-commeffect -i eth0 -g 224.1.2.8 -p 45702 <tx_nem> latency=<sec> jitter=<sec> loss=<pct> duplicate=<pct> unicast=<bps> broadcast=<bps> -t <rx_nem> -r <tx_nem>
```

For reset, send all values as zero. `unicast=0` and `broadcast=0` mean no bitrate limit.

Important: after a receiver processes a CommEffect event, omitted transmitter profiles can be dropped as `No Profile`. The dashboard controller avoids that by first seeding zero-effect CommEffect profiles for every known transmitter on each affected receiver, then overlaying the selected transmitter's requested latency/loss/bitrate values. Do not send only the selected pair unless you also confirm unrelated peers stay alive and the receiver's `shim0 *DropTable0` does not gain `No Profile` drops for the omitted transmitters.

CommEffect state is directional. A GUI bidirectional impairment between Control_20 and Platform_30 corresponds to two active profiles: `Control_20 -> Platform_30` and `Platform_30 -> Control_20`. Reset must clear both profiles. If a later one-way reset only clears one direction, the reverse direction can remain impaired even though the dashboard appears to show a zeroed latest command. That can make reliable DDS command delivery look inexplicably stalled, because ACKNACK/NACK and heartbeat repair traffic also needs the reverse WAN direction.

Delivery dashboard rows are sequence-matched app-level audit records from `debug/*_debug/events.jsonl`, not RF packet counters. The key is `(run_id, topic, source_node, audit_sequence)` plus the receiver node. `Sent` is samples observed in the rolling window, `Received` is matching destination receives observed in the rolling window, and `Lost` is only samples older than the topic grace period that still have no matching receive. `ControlCommand` and `PlatformCommandAck` use a 15 s grace period so reliable repairs can arrive before the dashboard marks a sample lost; periodic status/report topics use 2 s.

Observed on 2026-09-15 in the 3-platform mesh: under clean one-way `Control_20 -> Platform_30` CommEffect loss, `ControlCommand` did not collapse to all lost; at 50% loss it recovered to `6/6` received within the 30 s delivery window. Under bidirectional 50-60% loss, recent command receives could drop to zero because both data and reverse repair/control traffic were impaired. After clearing both directional profiles, command delivery recovered to `30/30` in the dashboard window.

Validated on 2026-09-15 in an isolated one-platform mesh:

- `/api/mesh_status` showed Control_20 and Platform_30 peers alive after startup with default connectivity enabled.
- Applying `latency=0.1`, `jitter=0.005`, `unicast=50000`, `broadcast=50000` through `/api/emane_controller` recorded the requested CommEffect state.
- Reset through `/api/emane_controller` recorded zeroed CommEffect state.
- `emanesh localhost get table <nem> all` showed `shim0 EventReceptionTable` event `103` count `2` on both NEM 1 and NEM 2 for apply plus reset.
- `shim0 BroadcastPacketDropTable0` showed no `No Profile` drops after switching `defaultconnectivitymode` to `on`.
- In a 3-platform mesh, a pair-only CommEffect event to Control_20/Platform_30 caused Control_20 and Platform_30 to drop Platform_31/Platform_32 traffic as `No Profile`. Seeding zero-effect profiles for all known NEMs before the pair-specific overlay kept the dashboard at `3/3 direct alive` through GUI apply/reset.

## References

- EMANE Physical Layer guide: receive power, receiver sensitivity, pathloss processing, and receive/discard behavior.
- EMANE RF Pipe Radio Model guide: Packet Completion Rate curves, datarate, delay, jitter, and RF Pipe limitations.
- EMANE Events guide: PathlossEvent and `emaneevent-pathloss` target/reference semantics.
- EMANE Comm Effect Utility Model guide: CommEffectEvent, shim NEM structure, latency/jitter/loss/duplicate/unicast/broadcast controls, and event/stat tables.
- Local live config checked from `/tmp/emane/nem.xml` and `/tmp/emane/rfpipemac.xml` inside `control-20`.
