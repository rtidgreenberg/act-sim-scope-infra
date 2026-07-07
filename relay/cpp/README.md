# relay/cpp/ — Modern C++ ISC relay (with Network Capture)

A **Modern C++ (C++11) port of the Python ISC relay** ([../isc_relay.py](../isc_relay.py)).
Same job, same QoS, same mirroring rule — plus the one thing the Python API can't do:
**RTI Network Capture**.

## Why a C++ version?

The RTI **Python API (`rti.connextdds`) does not expose Network Capture**. The
programmatic capture API (`rti::util::network_capture`) ships only in the C / C++ / Java
bindings. This port exists so we can record the relay's DDS traffic — **including
shared-memory traffic**, which OS-level `tcpdump`/Wireshark can't see — straight to
`.pcap` for offline analysis, without changing the relay's behaviour.

Everything else is a faithful port of the Python relay:

```
reader sample valid           →  writer.write(sample)              # forward, key preserved 1:1
reader NOT_ALIVE_DISPOSED      →  writer.dispose_instance(handle)
reader NOT_ALIVE_NO_WRITERS    →  writer.unregister_instance(handle)
```

Two `DomainParticipant`s (one per leg), a `ReadCondition` + `WaitSet` pump, and
`wait_for_historical_data()` on startup — mirroring [../isc_relay.py](../isc_relay.py).

## Files

| File | What |
|---|---|
| [ActState.idl](ActState.idl) | The keyed type — field-for-field match of the Python `@idl.struct ActState` (wire-compatible, so C++ and Python endpoints interop) |
| [isc_relay.cxx](isc_relay.cxx) | The relay: ISC `DataReader` (leg 1) + ISC `DataWriter` (leg 2); mirrors write / dispose / unregister; optional `--capture` |
| [CMakeLists.txt](CMakeLists.txt) | Build; runs `rtiddsgen` on the IDL at build time (no committed generated code) |

**QoS is shared with the Python relay** — both load `ActIscLibrary::ActIscProfile` from
[../qos_isc.xml](../qos_isc.xml), so the two relays are QoS-identical. (The XML token is
`RECOVER_INSTANCE_STATE_CONSISTENCY`; the Python binding spells the same enum
`RECOVER_STATE`.)

## Build

```bash
export NDDSHOME=/home/rti/rti_connext_dds-7.6.0
cmake -B build -DCONNEXTDDS_ARCH=x64Linux4gcc8.5.0
cmake --build build
```

Produces `build/isc_relay`. Verified against Connext **7.6.0**, `x64Linux4gcc8.5.0`,
gcc 9.4.

## Run

```bash
export NDDSHOME=/home/rti/rti_connext_dds-7.6.0
export RTI_LICENSE_FILE=$NDDSHOME/rti_license.dat
export NDDS_QOS_PROFILES=$PWD/../qos_isc.xml     # so the default QosProvider finds the profile

# plain relay (behaviour-identical to the Python one)
./build/isc_relay --upstream-domain 11 --downstream-domain 12 --topic ActState -v

# same, but capture all DDS traffic to <name>_GUID-*.pcap (one file per participant)
./build/isc_relay --upstream-domain 11 --downstream-domain 12 --capture relaycap -v
```

Ctrl-C stops cleanly (stops capture, closes participants, disables capture in the
required order). Open the resulting `.pcap` in Wireshark, or `tshark -2 -r relaycap_*.pcap`
(the `-2` two-pass flag lets discovery info from later frames decode earlier RTPS frames).

Instead of `--qos-file`/`NDDS_QOS_PROFILES` you can point at the XML directly:

```bash
./build/isc_relay --upstream-domain 11 --downstream-domain 12 \
    --qos-file ../qos_isc.xml --profile ActIscLibrary::ActIscProfile
```

## Options

| Flag | Default | Meaning |
|---|---|---|
| `--upstream-domain N` | *(required)* | leg-1 reader domain |
| `--downstream-domain M` | *(required)* | leg-2 writer domain |
| `--topic NAME` | `ActState` | topic name (both legs) |
| `--qos-file PATH` | *(env / `USER_QOS_PROFILES.xml`)* | QoS XML; empty → default `QosProvider` |
| `--profile LIB::PROFILE` | `ActIscLibrary::ActIscProfile` | QoS profile to load |
| `--capture NAME` | *(off)* | enable Network Capture → `NAME_GUID-*.pcap` |
| `-v`, `--verbose` | off | log each forward / dispose / unregister |

## Network Capture lifecycle (why the ordering in `main` matters)

`rti::util::network_capture` has strict ordering rules, enforced in
[isc_relay.cxx](isc_relay.cxx):

1. `enable()` **before** any `DomainParticipant` is created,
2. `start(name)` after participants exist (captures **all** of them),
3. `stop()` before participants are deleted,
4. `disable()` **last**, after participants are gone.

With two participants you get two files (`NAME_GUID-<id>.pcap`) — one per leg.

## Port notes / differences from the Python relay

- **Key recovery.** The Python relay keeps a `handle → key_id` cache as a fallback; this
  port relies on `reader.key_value()` instead. The shared QoS is tuned to make that
  succeed — `serialize_key_with_dispose` carries the key on dispose-only samples, and
  `keep_minimum_state_for_instances` retains the mapping for instances seen alive. It
  returns "unrecoverable" (and skips) only for the case DDS genuinely can't recover: a
  `NOT_ALIVE_NO_WRITERS` for an instance this reader never saw alive — same outcome as the
  Python `resolve_key_id` returning `None`.
- **Single-threaded.** The pump runs on the main thread (blocking `WaitSet`), where Python
  used a daemon thread; for a standalone one-purpose process that's simpler and equivalent.
- Carries the same deferred-to-Phase-8 limitations as the Python relay (metadata not
  preserved end-to-end, concrete keyed type rather than DynamicData). See
  [../README.md](../README.md).
