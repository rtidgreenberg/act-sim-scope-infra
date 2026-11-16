# Serialized-payload WAN envelope — Connext 7.7.0

## Result (2026-11-16)

**The supported Connext CDR path works.** A fresh build and three independent
processes passed in an owned `connext:7.7.0` container with `--network none`,
private IPC, UDPv4-only participants, and domains **111/112/113**.
The existing seven live mesh containers/configurations/binaries were not changed.

```bash
bash spikes/serialized_wan_envelope/run.sh
```

The runner mounts this checkout read-only and the license directory
`/home/dgreenberg/rti_connext_dds-7.7.0:/shared:ro`. Builds, process logs and
coordination files exist only in the disposable container's `/work`, not on the
host or in the checkout. The owned container is removed on success/failure;
all child PIDs are reaped. The ignored compact report is
`debug/test_reports/serialized_wan_envelope.txt`.

### Executed checks

| Check | Measured result |
|---|---|
| Phase 1: command and nested status | 6/6 complete original samples equal on destination LAN |
| Phase 2: add a previously absent payload type after WAN endpoints already exist | 9/9 complete samples equal; same envelope type and same WAN endpoints |
| Samples exercised | Empty/short/full bounded sequences; nested structures; fixed arrays; booleans; negative integers; doubles; long timestamps; empty/max-length status string; all 256 octet values |
| Original keys | 6 distinct destination instance handles (2 per family); repeated writes and the second phase retain the same handle for each key |
| Native writer-side CFT, phase 1 | 29 WAN writes offered; **11 pushed**: 6 normal plus 5 deliberately invalid selected frames |
| Native writer-side CFT, phase 2 | 36 WAN writes offered; **9 pushed** |
| Unselected metadata | **45/45 suppressed at the writer**: wrong destination, wrong original topic, or wrong source; no extra restored LAN samples |
| Missing local subscription type | 1 explicit `unknown_local_type` rejection |
| Wrong schema | 2 explicit `schema_mismatch` rejections: altered identity; same type name with incompatible locally discovered structure |
| Unsupported encoding | 1 explicit rejection |
| Truncated encapsulation | 1 explicit rejection before deserialization |
| Source dispose | Actual `NOT_ALIVE_DISPOSED` observed and explicitly **not forwarded**; independent application verifies no destination lifecycle sample |
| Cleanup | All children reaped, private `/dev/shm` free of RTI/dds artifacts, owned container removed |

Final gate: **15/15 full-payload equality checks, 5/5 expected rejections,
45/45 native metadata exclusions, 2/2 phases passed; zero unexpected failures
or skipped cases.** These are spike checks, not the production router suite.

## What actually crosses the WAN

```text
application writer (LAN 111)
    -> source gateway: discover publication DynamicType and QoS
    -> DynamicData reader -> Connext CDR serialization
    -> OpaqueWan: SerializedWanEnvelope + octet sequence (WAN 112)
    -> metadata CFT, executed by the source WAN writer
    -> destination gateway: discover LOCAL subscription DynamicType and QoS
    -> verify schema identity, representation, operation and original key
    -> Connext CDR deserialization -> original DDS writer (LAN 113)
    -> independent application reader: full DynamicData equality
```

Only the application executable loads `application_types.xml`. The gateways
load **only** `envelope.xml` and `qos.xml`. WAN discovery is checked against the
fixed envelope DynamicType, which contains strings and an octet sequence,
**no union or typed application members**. Application type definitions are not
sent through a catalog, side channel, file rendezvous, or WAN discovery.
Rendezvous files carry only phase readiness/completion, never payloads/types.

`rti::topic::to_cdr_buffer(..., DataRepresentation::xcdr())` and
`rti::topic::from_cdr_buffer_no_alloc(existing_dynamic_sample, bytes)` are
the public Modern C++ APIs used. Their declarations and DynamicData support
were inspected in the installed 7.7 headers; the fresh build/runtime are the
evidence. The bytes include their Connext CDR encapsulation and are copied
unchanged into/out of the envelope. There is no JSON payload, manual payload
field map, original typed struct compiled into a gateway, or schema inference
from bytes. This is **serialization/deserialization of DynamicData**, not a
zero-copy reader `takecdr` / writer `writecdr` path or preservation of the
original received wire buffer.

Source/destination route labels are configured constants in this rig. WAN CFTs
inspect only `source`, `destination`, and `original_topic`, not the payload.
The source obtains one original top-level string key through DynamicType
reflection; this is identity metadata extraction, not application field mapping.
All original fields, including keys and payload timestamps, traverse the CDR
buffer. DDS SampleInfo identities/source timestamps are not transported.

## Discovery, schema identity and QoS limits

- Source publications and destination subscriptions must advertise a complete
  local DynamicType. XML enables `request_types_filter=*`; without that explicit
  setting this rig timed out learning local types. No schema is guessed.
- Each discovered topic gets its own reader/writer; absence of the third type
  does not prevent the first two forwarding. The third application's endpoints
  are created only in phase 2. This is not a fleet-scale discovery/churn test.
- Identity is `schema_name` plus **`rti77-idl-fnv1a64`** of Connext's rendered
  DynamicType IDL. Independently discovered matching types yield matching IDs,
  and the tested same-name structural mismatch is rejected. This is a
  **same-version spike fingerprint**, not a standardized XTypes identifier,
  cryptographic identity, authenticated schema, or cross-version canonical form.
  Hash collisions and untrusted senders are not resolved. Production needs a
  robust canonical schema identity/registry policy.
- Local discovery QoS is inspected and asserted reliable/volatile. Endpoint
  QoS comes from **explicit XML profiles**, not discovery-derived policy
  construction or native Routing Service auto-configure.
- No auto-QoS integration is claimed. The existing
  [native LAN auto-QoS qualification](../../router/routing_service/README.md#native-lan-auto-qos-qualification)
  remains the separate evidence: `ON_DOMAIN_MATCH` inferred selected policies,
  `IMMEDIATE` stalled, finite deadlines needed explicit profiles, and a shared
  route waited for absent inputs. An eventual processor integration still needs
  per-topic independence and those policy/startup gates.
- XCDR1 from this SDK, same-release round-trip only. XCDR2, big-endian producers,
  other vendors, appendable/mutable evolution, large/fragmented payloads, and
  malformed CDR bodies are not qualified. The truncation negative test checks
  the encapsulation length, not arbitrary malformed-body resilience.
- Key qualification is **one top-level string key**. Compound/numeric/nested or
  unkeyed types reject explicitly. Original string-key identity is preserved;
  source/topic scope additionally separates WAN instances. This is not proof
  of full instance-state consistency, restart recovery, multiple-producer
  ownership or authoritative source rewriting.
- Only `WRITE` is implemented. Dispose is deliberately diagnosed/dropped.
  Unregister/NO_WRITERS, key-only recovery, lifecycle propagation and resurrection
  remain unimplemented/unqualified. Volatile history does not preserve events
  across a late reader or gateway restart.
- The envelope payload bound is 65536 octets and instance-ID bound 256 characters.
  No production admission/backpressure, priority, health or retention policy is
  established by this small reliable/volatile KEEP_ALL test.

## Practical comparison with typed unions

| Area | Binary envelope: advantage | Cost / typed-union advantage |
|---|---|---|
| Deployment | One stable WAN schema; a third local payload type worked without rebuilding/replacing WAN endpoints | Both LANs still need matching schemas and codecs/discovery; unions carry their branch type graph to WAN readers |
| Discovery | WAN type graph contains no application branches | Missing destination-local schema is a hard rejection; unions support remote branch introspection |
| Filtering/tools | Native metadata-only writer CFT passed | WAN tools see opaque bytes; cannot filter/inspect application fields unless promoted into metadata or decoded externally |
| Compatibility | Explicit gate rejects tested unknown/wrong local schemas | Versioned schema identity and compatibility rules become gateway responsibilities; typed unions expose those schemas directly |
| Runtime work | No handcrafted whole-payload mapping or growing union catalog | DynamicData serialize, copy, deserialize, and republish are real work; no speed/CPU/memory advantage measured |
| WAN overhead | Fixed outer type independent of application families | Not free: 15 original CDR buffers total **2010 bytes**, their serialized envelopes total **4554 bytes** (**2544 extra**), excluding discovery/RTPS/repair. No typed-union wire comparison or bandwidth saving is claimed |
| QoS | The representation does not inherently prevent per-topic inferred LAN QoS | This spike uses explicit profiles; Routing Service inference/startup limitations remain a separate integration task |
| Keys/lifecycle | Tested string keys and repeated-instance handles survive writes | Generic keys, lifecycle/ownership/restart and required-event retention need more protocol work, as do production union adapters |

**Conclusion:** feasible for a stable, metadata-filtered WAN contract when each
LAN can supply the payload schemas. Prefer typed unions if WAN schema visibility
and application-field tooling/filtering outweigh the deployment coupling.
This result does not select a production architecture or deploy a replacement.

## Connext guidance cross-check

Connext guidance was consulted, then checked against installed headers and
executable results. The correction-log submodule was detached, so `pull` could
not merge; its upstream was fetched and the latest `origin/main` log inspected
without changing the worktree/pin. No commits/pushes were made.

The AI validator failed to resolve the generic CDR helpers and suggested a
DynamicData member serialization form. The installed headers and successful
fresh compilation/execution validate the generic free functions used here;
the suggested replacement was not adopted. Its claim that DynamicType has no
equality operator was also not relied on: the existing equality syntax builds
and the fixed WAN type equality assertion passes here. These are local evidence
notes, not unverified API assumptions.
