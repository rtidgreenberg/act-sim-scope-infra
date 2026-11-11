# Investigation report: C2-seeded runtime participant discovery

**Date:** 2026-11-07

**Status:** Isolated proof of concept complete; production qualification pending

**Implementation:** [C2 peer bootstrap spike](../../spikes/c2_peer_bootstrap/README.md)

**Retained evidence:** [Measured results](../../debug/test_reports/c2_peer_bootstrap.json)

## Executive summary

The proposed discovery path worked in an isolated Connext 7.7.0 experiment:

1. A client starts with **only C2** in its initial peer list.
2. C2 sends the client an application-level directory of platform peer descriptors.
3. The client calls `add_peer()` once for each newly learned descriptor.
4. The client verifies the expected platforms through discovered-participant data.
5. Direct application request/ack exchanges confirm communication.

Three repetitions with SPDP and three with SPDP2 passed: **6/6 protocol runs and
96/96 stage checks**. The associated helper regression tests passed **5/5**.

**Recommendation:** retain this as a viable bootstrap design and qualify it on the
actual WAN before adopting it in the router. Add each new locator once, track
successful additions locally, and monitor discovery separately. Do not interpret
successful `add_peer()` calls as acknowledgments from remote platforms.

## Questions and answers

| Question | Finding |
|---|---|
| Can C2 be the client's only initial peer? | Yes, in the tested topology. The platform directory then supplies direct discovery destinations. |
| Does knowing C2 automatically discover C2's platforms? | Not in this ordinary participant setup. No platform discovery occurred during the three-second pre-directory observation window. |
| Does `add_peer()` success prove the platform exists? | No. The nonexistent Missing descriptor was accepted but never discovered. |
| Can discovered-participant APIs verify arrival? | Yes. Resolve handles to participant data and match the expected logical identity and current GUID, not just the participant count. |
| Must the application repeatedly add the same peer? | No repeated additions were needed in these runs, including late startup and graceful restart. |
| Does QoS `initial_peers` show runtime additions? | No. It remained C2-only after four successful runtime additions. |
| Is one-sided addition always enough? | No. The platform rejecting unknown peers required a reciprocal Client entry. |
| Is C2 required after direct discovery? | Existing communication and a platform's rediscovery after restart worked while C2 was absent. |

## Experimental method

The [runner](../../spikes/c2_peer_bootstrap/run.py) launched an owned, disposable
Docker container with `--network none`. Each logical participant ran in a separate
spawned Python process. This avoided interference with other experiments and did
not use or modify a live mesh.

| Participant | Fixed participant ID | Initial peers | Role |
|---|---|---|---|
| C2 | 0 | None | Sends the application directory |
| Client | 1 | C2 only | Receives the directory and adds platform peers |
| Open | 2 | C2 only | Accepts unknown peers |
| Late | 3 | C2 only | Starts after its descriptor has already been added |
| Strict | 4 | C2 only | Rejects unknown peers until Client is explicitly added |
| Missing | 6 | Not applicable | Directory entry with no corresponding process |

The directory contained Open, Late, Strict, and Missing. It was a fixed synthetic
directory transmitted over a real DDS topic, not an implementation of C2's
production directory-population mechanism.

All transport and discovery settings were authored in the spike's
[QoS XML](../../spikes/c2_peer_bootstrap/qos.xml):

- UDPv4 on private loopback; no multicast or shared-memory transport.
- Exact bracketed descriptors, such as `[2]@udpv4://127.0.0.1`, to avoid accidental
  discovery from broad participant-ID scanning.
- Private domains 210 and 211 for SPDP and SPDP2.
- Accelerated one-second participant liveliness assertions and a four-second
  participant lease.
- A one-second participant announcement period for SPDP2 only. The installed
  runtime rejected explicitly setting that field for SPDP; protocol-specific
  XML inheritance resolved the consistency error.
- Reliable application messages for directory delivery and request/ack probes.

These timing choices are test settings, not production QoS recommendations.

## Results

Each protocol run completed 16 recorded stages. The table groups related stages:

| Test group | SPDP | SPDP2 |
|---|---|---|
| Client and existing platforms independently discover C2 | 3/3 passed | 3/3 passed |
| Client does not discover platforms before receiving the directory | 3/3 passed | 3/3 passed |
| Real DDS directory delivery causes successful runtime additions | 3/3 passed | 3/3 passed |
| Open is mutually discovered and completes a request/ack | 3/3 passed | 3/3 passed |
| Late, Strict, and Missing remain undiscovered during an eight-second observation | 3/3 passed | 3/3 passed |
| Repeated directory samples are deduplicated | 3/3 passed | 3/3 passed |
| Late starts and completes discovery and a request/ack without another add | 3/3 passed | 3/3 passed |
| A reciprocal Client add lets Strict discover and communicate | 3/3 passed | 3/3 passed |
| Open continues communicating after C2 closes and disappears | 3/3 passed | 3/3 passed |
| Late gracefully restarts with a new GUID while C2 is absent and communicates without re-add | 3/3 passed | 3/3 passed |
| Final identity, add counts, and original initial-peer QoS match expectations | 3/3 passed | 3/3 passed |

Every run processed five directory samples while making **exactly one successful
Client add per descriptor**, including Missing. The repeated samples exercised
application-level deduplication; duplicate native `add_peer()` calls were not made.
This experiment therefore does not establish native duplicate-add idempotence.

### Observed discovery timing

| Discovery check | SPDP range | SPDP2 range |
|---|---|---|
| Late startup after its descriptor was already added | 0.809-0.810 s | 1.012-1.013 s |
| Graceful restart with new GUID, C2 absent, no re-add | 0.405-1.013 s | 0.810-1.013 s |

These durations begin at the first discovery poll **after worker creation**.
They exclude process/participant construction and use accelerated XML settings.
They are neither production latency guarantees nor evidence of a meaningful
performance advantage for either discovery protocol.

## Verification semantics

The implementation should distinguish four states:

```text
locator configured
    -> expected participant discovered
        -> required endpoints ready
            -> application communication verified
```

**Configured:** `add_peer()` returns without an exception. Record the successful
addition in application state. There was no runtime peer-list getter exposed by
the inspected Python binding; the unchanged QoS initial list is not that registry.

**Discovered:** call `discovered_participants()`, then
`discovered_participant_data(handle)` for the returned handles. The spike matches
the platform name, requires `partial_configuration == false`, and verifies the
new physical GUID after restart. A lookup can race with participant departure;
the spike explicitly counts invalid-handle lookup errors.

**Endpoints ready:** participant discovery does not itself establish that a
particular required writer and reader have matched with compatible QoS.

**Communicating:** the spike separately checks sequence-matched application
request/ack exchanges. This confirms the synthetic application path, not a
production router route.

The names in this test are unique labels, not authenticated identities. A
production directory needs an explicit logical-node identity model that accounts
for changing physical GUIDs.

## Recommended integration behavior

1. Keep the bootstrap C2 destination in external XML `initial_peers`.
2. Obtain a versioned directory containing logical node identities and reachable
   DDS peer descriptors.
3. Deduplicate against a registry scoped to the current local participant.
   Record an entry only after `add_peer()` succeeds; surface failures explicitly.
4. Add new locators once and monitor discovery with bounded deadlines/backoff.
   Report pending/unreachable platforms rather than blindly repeating adds.
5. Ensure reciprocal acceptance: platforms must either accept unknown peers or
   receive an explicit Client descriptor themselves.
6. Track discovery, endpoint readiness, and application health separately.
   Refresh GUID mappings when a platform restarts.
7. Reapply the current directory when the Client participant is recreated;
   successful-add flags from its old participant are not proof of configuration
   in the new one.
8. Define locator update, removal, expiration, and directory reconciliation
   semantics before production integration. The spike raises an explicit error
   for changed locators rather than silently retaining stale state.

C2 is a **bootstrap directory service**, not a discovery relay or a data relay.
The client and platforms must have direct connectivity for discovery and payloads.

## Routing Service processor follow-up

**2026-11-07: API access verified by compilation and linking, not runtime execution.**
The [processor API investigation](../../spikes/routing_service_peer_api/README.md)
checked whether this bootstrap design could operate on Routing Service's own WAN
participant rather than a separately created participant.

The actual service-owned participant is accessible through a configured WAN DDS
port:

```cpp
auto reader = route.input<DynamicData>("wan_input").dds_data_reader();
auto wan = reader.subscriber().participant();
```

The equivalent output path is `writer.publisher().participant()`. Select the WAN
port explicitly; traversing a LAN port or selecting only by domain ID does not
identify the intended connection.

| Operation on the service-owned participant | Verification |
|---|---|
| `wan->add_peer(peer)` | Compiled and linked from a processor callback |
| `wan->remove_peer(peer)` | Compiled and linked from a processor callback |
| `dds::domain::discovered_participants(wan)` | Compiled and linked from processor callbacks |
| `dds::domain::discovered_participant_data(wan, handle)` | Compiled and linked from processor callbacks |

The [C++ probe](../../spikes/routing_service_peer_api/processor_api_probe.cxx)
derived from `NoOpProcessor`, exercised both endpoint traversal paths in
`on_start`, and compiled discovery inspection in `on_periodic_action`. The library
linked against the installed 7.7.0 SDK with `--no-undefined`; `ldd -r` reported no
unresolved symbols. No Routing Service was started, no plugin was registered,
and none of these DDS operations was executed.

Integration must preserve service ownership:

- Use endpoint handles only while their associated ports remain enabled;
  invalidate before disable and reacquire after recreation.
- Do not close or delete service-owned entities.
- Give peer reconciliation one participant-scoped owner because multiple routes
  can share the same WAN participant.
- Prefer a stable infrastructure route for directory consumption and monitoring,
  so bootstrap functionality does not depend on auto-created application routes.
- Keep the successful-add registry separate from discovered-participant state.

Direct processor-driven peer mutation is therefore **API-feasible**, not yet a
runtime-qualified integration or a vendor support guarantee. Routing Service's
connection-level Remote Administration `add_peer`/`remove_peer` operations offer
a separately documented management path.

The next processor-specific gate is a loaded-plugin test covering C2-only WAN
bootstrap, directory-driven adds, expected GUID discovery, remove/re-add effects,
route disable/re-enable, and shared-connection ownership. The ordinary-participant
results above must not be presented as already proving that integration.
See also the [Routing Service replacement investigation](routing-service-replacement-investigation.md).

## Limitations and next qualification

The [RTI SPDP/SPDP2 performance guidance review](participant-discovery-performance-guidance.md)
adds a discovery-bandwidth comparison framework and vendor questions about
unreachable peers, locator fanout, recovery, and benchmark provenance. It does
not turn this small functional spike into a performance qualification.

The experiment establishes a functional discovery path, not production readiness:

- Private loopback only; no EMANE impairment, RF loss, NAT, authentication, or WAN
  locator reachability test.
- At most five concurrent participants and a static directory; no scale or
  discovery-bandwidth measurement.
- Graceful platform restart only; no crash, kill, or sustained communication
  blackout. Departure was observed immediately, so crash lease-expiration timing
  is not established.
- No Client restart test or directory update/removal implementation.
- Accelerated timing, not the production XML profile.

The next gate should compare this path against a fixed all-peers baseline using
actual per-node WAN descriptors and production timing. Measure convergence and
discovery traffic under nominal connectivity, loss, late startup, platform crash,
C2 outage/recovery, Client restart, and directory changes. Continue to require
expected participant identities and application delivery evidence.

## Reproduction and provenance

```bash
PYTHONPYCACHEPREFIX=/tmp/c2-peer-bootstrap-pycache \
  python3 -m unittest discover -s spikes/c2_peer_bootstrap -p 'test_*.py' -v

python3 spikes/c2_peer_bootstrap/run.py --repetitions 3
```

Prerequisites and runner options are documented in the
[spike README](../../spikes/c2_peer_bootstrap/README.md).
The runner verifies the installed `rti.connext` version, pins the container image,
stages executed sources under `/tmp`, cleans up its container, and retains a
successful evidence report. A failed attempt exits nonzero.

The inspected evidence records:

- Runtime: **Connext 7.7.0**.
- Image ID: `sha256:cbabafb1dab80c66483f092e1770249a6196d974b01592fe3a52979613f5f7a0`.
- Executed `spike.py` SHA-256: `b69f62c8b034f42b02497d2370f310904917233be9433860253ea293a3a46058`.
- Executed `qos.xml` SHA-256: `3cf46791a25df6332f55e9fc322d478c0ac1934e55db8943bc57d3cf6c0ad351`.

The JSON evidence is a generated, locally retained artifact; this report preserves
the conclusions and provenance independently of that artifact's retention.

## Documentation references

The original investigation checked API guidance against the installed binding
and empirical runs. Official references for the underlying mechanisms:

- [Peer descriptor format](https://community.rti.com/static/documentation/connext-dds/7.7.0.1/doc/manuals/connext_dds_professional/users_manual/users_manual/Peer_Descriptor_Format.htm).
- [DISCOVERY QoS: adding/removing peers and accepting unknown peers](https://community.rti.com/static/documentation/connext-dds/7.7.0.1/doc/manuals/connext_dds_professional/users_manual/users_manual/DISCOVERY_Qos.htm).
- [Configuring the peers list](https://community.rti.com/static/documentation/connext-dds/7.7.0.1/doc/manuals/connext_dds_professional/users_manual/users_manual/ConfigPeersListUsed_inDiscov.htm).
