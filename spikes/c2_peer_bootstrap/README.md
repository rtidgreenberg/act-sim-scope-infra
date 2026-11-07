# C2 directory -> runtime peers -> direct discovery

**Result (2026-11-07): viable in this isolated Connext 7.7.0 test.**
Three repetitions each of SPDP and SPDP2 passed: **6/6 protocol runs,
96/96 stage checks**, plus **5/5 helper regression tests**.
No production router, datamodel, or live mesh configuration was changed.

See the [standalone investigation report](../../docs/cpp_router/c2-peer-bootstrap-report.md)
for the experimental method, evidence, and integration recommendation.

## Answer to the discovery question

Yes: start Client with C2 as its only initial peer, receive a directory from C2
over an application DDS topic, and call `add_peer` once per new locator.
Poll `discovered_participants()` and resolve each handle using
`discovered_participant_data(handle)` to positively confirm the **expected
platform identity**, not just a nonzero participant count.

There are distinct states:

| State | Evidence |
|---|---|
| Configured | `add_peer(peer)` returned without an exception; record this locally |
| Participant discovered | An expected name/GUID appears in discovered-participant data; this spike also requires `partial_configuration == false` |
| Endpoints ready | Required writer/reader endpoints match with compatible QoS |
| Communicating | A sequence-matched application request/ack completes |

`add_peer` is **not** a synchronous remote acknowledgment. Adding the nonexistent
Missing locator succeeded in all runs but never produced a discovered participant.
The Python binding exposes `add_peer`, `add_peers`, `remove_peer`, and `remove_peers`,
but no runtime peer-list getter was found by binding introspection.
Reading `participant.qos.discovery.initial_peers` still returned **only C2**
after all four runtime additions. Keep your own successful-add registry.

The spike observes full participant data and separately proves communication with
roundtrips. Participant discovery alone is not sufficient evidence that a particular
application route works. Names here are unique test labels, not authenticated identities;
a production directory should identify logical nodes and track their current GUIDs.
Handle lookup may race with departure; the spike counts explicit invalid-handle
lookup errors rather than interpreting them as successful discovery.

## What was actually tested

Each participant runs in a separate spawned OS process. The owned Docker container
uses `--network none`; all DDS communication is UDPv4 on its private loopback.
Multicast and SHMEM are disabled. Exact participant IDs and bracketed peer
descriptors prevent an implicit scan from accidentally discovering platforms.

| Scenario | SPDP | SPDP2 |
|---|---|---|
| Client, Open, and Strict independently discover C2 | 3/3 | 3/3 |
| C2 knowledge does not give Client transitive discovery before the directory | 3/3 | 3/3 |
| C2 sends a real DDS directory; Client adds four locators | 3/3 | 3/3 |
| Open accepts unknown peers: one-sided addition gives mutual discovery and a roundtrip | 3/3 | 3/3 |
| Late, Strict, and nonexistent Missing remain undiscovered for eight seconds | 3/3 | 3/3 |
| Repeated directory samples are deduplicated; exactly one add per locator | 3/3 | 3/3 |
| Late starts afterward: mutual discovery and a roundtrip, without another add | 3/3 | 3/3 |
| Strict rejects unknown peers: a reciprocal Client add restores discovery and a roundtrip | 3/3 | 3/3 |
| C2 closes/disappears: existing direct platform roundtrips still work | 3/3 | 3/3 |
| Late gracefully restarts with a new GUID while C2 is absent: discovery and roundtrip without re-add | 3/3 | 3/3 |
| Final GUID, successful-add counts, and unchanged initial-peer QoS match expectations | 3/3 | 3/3 |

Observed late-start discovery checks took 0.809-0.810 s for SPDP and
1.012-1.013 s for SPDP2. Restart discovery checks took 0.405-1.013 s
and 0.810-1.013 s respectively. These times start **after worker creation**,
at the first discovery poll, and use accelerated XML timing. They are not
production discovery latency guarantees.

## Recommended implementation

1. Keep C2 in XML `initial_peers`; C2 must already be reachable.
2. C2 supplies a versioned application directory of logical node IDs and reachable
   DDS peer descriptors. The spike supplies a fixed synthetic list; it does not
   implement C2's production directory-population mechanism.
3. Reconcile directory updates against a local successful-add registry. Call
   `add_peer` for new locators and record them **only after success**.
4. Add once, then monitor discovery with bounded deadlines/backoff and surface
   pending/unreachable platforms. Connext handled late startup and restart without
   repeated application adds in every tested run.
5. Ensure platforms accept the arriving participant, either with
   `accept_unknown_peers=true` or explicit reciprocal peer registration.
   One-sided addition alone failed for Strict.
6. Match expected identities, handle departure/restart, and require endpoint or
   application evidence before displaying a route as ready.
7. On Client recreation, rebuild its registry and reapply the current directory.
   Persisting the old process's successful-add flags would be wrong.

Duplicate **directory updates** are tested, with application-level deduplication.
Duplicate native `add_peer` calls are deliberately not made; the spike does not
claim native duplicate adds are idempotent or harmless. Changed locators raise
an explicit out-of-scope error rather than silently leaving stale configuration.
A production reconciler also needs deliberate update/removal/expiration handling.

C2 is a **directory/bootstrap service**, not a discovery relay or a data relay.
After bootstrapping, the participants must be able to exchange discovery and
application traffic directly.

## Reproduce

Prerequisites: accessible Docker daemon, the existing `connext:7.7.0` image, and a
readable license-only directory. The worker verifies the installed `rti.connext`
version at runtime; the runner records the pinned image ID and executed source hashes.

```bash
PYTHONPYCACHEPREFIX=/tmp/c2-peer-bootstrap-pycache \
  python3 -m unittest discover -s spikes/c2_peer_bootstrap -p 'test_*.py' -v

python3 spikes/c2_peer_bootstrap/run.py --repetitions 3
```

Options: `--image`, `--license-dir`, `--repetitions`, and `--report`.
The default license directory is `/home/dgreenberg/rti_connext_dds-7.7.0`.
The runner owns and cleans up its container and disposable `/tmp` source/results
mounts; it retains the successful evidence report at
[`debug/test_reports/c2_peer_bootstrap.json`](../../debug/test_reports/c2_peer_bootstrap.json).
Failures exit nonzero rather than emitting a successful report for that attempt.

Sources: [spike.py](spike.py), [qos.xml](qos.xml), [run.py](run.py),
[test_spike.py](test_spike.py), and [PLAN.md](PLAN.md).
The synthetic `BootstrapMessage` is local to this spike.

## Limits and next qualification

- One host/private loopback, five concurrent participants, a static directory.
  No EMANE, packet-loss impairment, NAT, security, locator churn, or scale test.
- The restart is **graceful**, not a crash or RF blackout. Participant removal
  was immediate in these runs; crash lease-expiration behavior is not established.
- XML sets one-second liveliness announcements and a four-second participant
  lease. SPDP2 additionally sets a one-second participant announcement period;
  7.7.0 rejects that explicit field for SPDP, so the XML uses protocol-specific
  inheritance. These are test settings, not suggested production QoS.
- Client restart and directory update/removal need further implementation/testing.
- Next useful qualification: reachable per-node WAN locators under EMANE loss,
  C2 outage/recovery, and production timing, measuring discovery traffic and
  convergence against a fixed all-peers baseline.

## API/documentation cross-check

Connext AI was consulted for API semantics, then checked against the installed
7.7.0 Python binding, the shared known-wrong-answer log, and the actual runs.
The recommendation above is based on measured results, not an unverified AI claim.
Official documentation also describes these mechanisms:

- [Peer descriptor format](https://community.rti.com/static/documentation/connext-dds/7.7.0.1/doc/manuals/connext_dds_professional/users_manual/users_manual/Peer_Descriptor_Format.htm):
  `[2]@...` targets only participant ID 2; `2@...` scans IDs 0 through 2.
- [DISCOVERY QoS, adding/removing peers and accepting unknown peers](https://community.rti.com/static/documentation/connext-dds/7.7.0.1/doc/manuals/connext_dds_professional/users_manual/users_manual/DISCOVERY_Qos.htm):
  potential peers need not exist, and ordinary participants do not exchange peer lists.
- [Configuring the peers list](https://community.rti.com/static/documentation/connext-dds/7.7.0.1/doc/manuals/connext_dds_professional/users_manual/users_manual/ConfigPeersListUsed_inDiscov.htm):
  `add_peer` adds discovery destinations after startup.
