# Team Control Topic — Design Plan

**Date:** 2026-07-29  
**Status:** Draft  
**Depends on:** D103 (team_wan retired; platform_wan is team_scoped)

## Problem

Team assignment today is imperative: an external script publishes `RouterCommand` /
`ADD_PARTICIPANT_PARTITION` on `control_lan` (domain 20), addressed to a specific
`(target_node, target_router)`. This works but has no persistence, no single source of
truth for "which platform is on which team", and no path from the browser GUI to the
platforms without a sidecar script.

## Goal

A **declarative, data-driven team assignment** flow:

1. The operator assigns teams in the mesh dashboard (browser).
2. The browser writes a `TeamAssignment` sample through WIS.
3. That sample flows through the existing router mesh to each platform.
4. Each platform's router reacts to the received data by applying the partition change
   to `platform_wan` — the same `set_qos` mechanism D83/D103 already validates.

## New Type

Added to `RouterAdminTypes.idl`:

```idl
struct TeamAssignment {
    @key
    string platform_node;       // "Platform_30" — which platform this assignment targets
    string team_name;           // non-empty = assign to team; "" = remove from team
};
```

- **Keyed by `platform_node`** — one instance per platform, last-writer-wins.
- **QoS:** RELIABLE + VOLATILE (reuses `wan_event` profile unchanged). No durability
  needed — a restarted platform comes up with no team assignment (clean partition set,
  just its own identity entry per D83/D103), and must go through explicit operator
  re-assignment before rejoining a mission. There is no "resume where I left off"
  semantic; the operator sees the node reappear in the dashboard without a team ring and
  reassigns it — that's a fresh write, not a replay.
- **Topic name:** `TeamAssignment` — same name on all domains it appears on.

## Data Flow

```
┌─────────────┐     WIS REST/WS      ┌──────────────────────┐
│   Browser   │ ──────────────────▶   │  WIS DataWriter      │
│  (mesh GUI) │                       │  domain 20           │
└─────────────┘                       │  (control_lan)       │
                                      └──────────┬───────────┘
                                                 │
                                                 ▼
                              ┌───────────────────────────────────┐
                              │  C2 Router — forwarding route      │
                              │  TeamAssignment                    │
                              │  input:  control_lan  (domain 20)  │
                              │  output: control_wan  (domain 200) │
                              └──────────────────┬────────────────┘
                                                 │
                              ┌───────────────────────────────────┐
                              │         domain 200 (WAN)           │
                              │  control_wan publishes             │
                              │  platform_wan subscribes           │
                              │  (partition match via control_wan  │
                              │   wildcard "*", D103)              │
                              └──────────────────┬────────────────┘
                                                 │
                          ┌──────────────────────┼──────────────────────┐
                          ▼                      ▼                      ▼
                 ┌─────────────────┐   ┌─────────────────┐   ┌─────────────────┐
                 │ Platform_30     │   │ Platform_31     │   │ Platform_32     │
                 │ router route:   │   │ router route:   │   │ router route:   │
                 │ platform_wan    │   │ platform_wan    │   │ platform_wan    │
                 │  → platform_lan │   │  → platform_lan │   │  → platform_lan │
                 └────────┬────────┘   └────────┬────────┘   └────────┬────────┘
                          │                      │                      │
                          ▼                      ▼                      ▼
                 ┌─────────────────┐   ┌─────────────────┐   ┌─────────────────┐
                 │ Platform ctrl   │   │ Platform ctrl   │   │ Platform ctrl   │
                 │ process         │   │ process         │   │ process         │
                 │ (platform_lan)  │   │ (platform_lan)  │   │ (platform_lan)  │
                 │ reads TeamAssign│   │ reads TeamAssign│   │ reads TeamAssign│
                 │ writes RouterCmd│   │ writes RouterCmd│   │ writes RouterCmd│
                 └─────────────────┘   └─────────────────┘   └─────────────────┘
```

## Components

### 1. WIS — Writer (browser → domain 20)

Add to `wis_config.xml.template`:

- `register_type name="TeamAssignment"` (same IDL, same `rtiddsgen -convertToXml` output)
- `topic name="ActTeamAssignment"` on `ControlLanDomain` (domain 20) — topic name uses
  the `Act*` prefix to avoid colliding with the struct name in WIS's XML namespace
- A `<publisher><data_writer name="TeamAssignmentWriter">` in the existing
  `MeshDashboardApp` / `MeshParticipant`
- QoS: RELIABLE + VOLATILE — matches the route's `wan_event` profile. No durability
  needed (see §New Type rationale).

The browser `PUT`s/`POST`s a JSON sample via WIS's REST API:
```
PUT /dds/rest1/applications/MeshDashboardApp/domain_participants/MeshParticipant/publishers/MeshPublisher/data_writers/TeamAssignmentWriter
Content-Type: application/dds-web+json

{ "platform_node": "Platform_30", "team_name": "Alpha" }
```

### 2. Router Config — New `control_event` route (unfiltered, control → platform)

The existing `control_command` route is per-node filtered (CFT on
`msg.destination = ${node.name}`), so it can't broadcast to all platforms.
`TeamAssignment` is a broadcast topic — C2 publishes one sample per platform and every
platform receives every sample (the platform control process filters locally).

Add a new **`control_event`** route — same control→platform shape as `control_command`,
same `wan_event` QoS (RELIABLE), but **no CFT filter**:

```yaml
  - name: control_event
    enabled: true
    forwarding_mode: dynamic_data
    source: control
    destination: platform
    topics:
      - name: ActTeamAssignment
    source_side:
      input:
        participant: control_lan
      output:
        participant: control_wan
        writer_qos: wan_event
        publisher_partition: CONTROL
    destination_side:
      input:
        participant: platform_wan
        reader_qos: wan_event
        subscriber_partition: CONTROL
      output:
        participant: platform_lan
```

This mirrors `control_command` exactly except:
- **No `filter:` block** on the destination-side input — all platforms receive all samples.
- **`TeamAssignment`** (and future broadcast control topics) live here; per-node
  `ControlCommand` stays on `control_command` with its existing CFT.

The `CONTROL` partition on the WAN legs matches via `control_wan`'s wildcard (`"*"`,
D103) on the source side, and every `platform_wan` subscriber carries `CONTROL` on this
route's reader.

### 3. Platform Control Process — TeamAssignment Subscriber (platform_lan)

A **separate platform control process** on each platform subscribes to `TeamAssignment`
on `platform_lan` (the route's destination-side output delivers it there) and translates
received assignments into `RouterCommand` / `ADD_PARTICIPANT_PARTITION` /
`REMOVE_PARTICIPANT_PARTITION` commands published on the same `platform_lan` domain — the
same admin command channel the router already listens on.

This keeps the router's command interface the single entry point for partition changes,
and the platform control process is a thin, stateless translator:

```
Platform Control Process (per platform):
  - Subscribes: TeamAssignment on platform_lan (domain <platform_id>)
  - Publishes:  RouterCommand on platform_lan (domain <platform_id>)
  - Targets:    (target_node=Platform_XX, target_router=platform-XX-control-platform)
```

No WAN participant needed — everything is on the platform's own LAN domain. The
`control_command` route already did the WAN bridging.

**Reaction logic:**

```
on_data_available(TeamAssignment sample):
    current_team = tracked_team   // local state: last team applied
    if sample.team_name == current_team:
        return  // no-op
    if current_team != "":
        publish RouterCommand {
            kind: REMOVE_PARTICIPANT_PARTITION,
            participant_name: "platform_wan",
            partition_name: current_team
        }
    if sample.team_name != "":
        publish RouterCommand {
            kind: ADD_PARTICIPANT_PARTITION,
            participant_name: "platform_wan",
            partition_name: sample.team_name
        }
    tracked_team = sample.team_name
```

**Why a separate process (not built into the router):**
- The router's internal command path is already validated end-to-end (D83/D103); reusing
  it via the external command topic avoids adding a second internal mutation path.
- The platform control process can be written in Python (quick iteration, leverages the
  existing `harness_v2` Python infrastructure) or C++ — it's a ~50-line DDS app.
- Decoupled lifecycle: the control process can restart independently without disturbing
  the router's forwarding state.
- Mirrors the production deployment model where an orchestrator/agent process on the
  platform host manages the router via commands.

### 4. Browser GUI (mesh_graph.js)

The dashboard already renders team rings and team-filter chips from
`RouterHealth.team_partition`. To assign teams:

- Add a UI interaction (e.g., right-click a node → "Assign to team" → text input or
  dropdown of known teams).
- On confirm: `PUT` a `TeamAssignment` sample to WIS (see §1 above).
- The partition change propagates through the mesh; within ~1–2 heartbeat cycles the
  node's `RouterHealth.team_partition` updates in `ActRouterMeshStatus`, and the existing
  rendering logic recolours the ring automatically — no new subscription needed.

## Partition Matching on Domain 200

Why `control_wan` → `platform_wan` delivery works without explicit peer configuration:

| Participant | Partition set | Matches with |
|---|---|---|
| `control_wan` (C2) | `["*"]` (protected wildcard, D103) | everything |
| `platform_wan` (Platform_30) | `["Platform_30"]` (+ team if assigned) | `control_wan` (via wildcard) |
| `platform_wan` (Platform_31) | `["Platform_31"]` | `control_wan` (via wildcard) |

The route's output writer lives on `control_wan` → it partition-matches every
`platform_wan` subscriber. Cross-platform isolation is preserved: two platforms with
disjoint partitions still don't see each other's non-TeamAssignment traffic (heartbeats,
status, etc.) unless they share a team name.

## QoS Summary

All VOLATILE + RELIABLE — reuses the existing `wan_event` QoS profile unchanged across
the entire chain. No durability anywhere.

**Rationale:** A restarted platform is operationally removed from its team and mission.
It comes up with a clean partition set (own identity only, D83/D103 default) and must go
through explicit operator re-assignment in the dashboard before rejoining. There is no
"resume previous team" semantic, so there is no late-joiner replay problem to solve.

| Location | QoS | Notes |
|---|---|---|
| WIS writer (domain 20) | RELIABLE + VOLATILE | Fresh write per operator action |
| Route WAN legs | `wan_event` (RELIABLE + VOLATILE) | Unchanged existing profile |
| Platform control process reader | RELIABLE + VOLATILE | Starts with `tracked_team=""` (matches clean router state) |

## Implementation Order

1. **IDL:** Add `TeamAssignment` to `RouterAdminTypes.idl`. Regenerate.
2. **Route config:** Add `control_event` route to `control-platform.yaml` — same shape
   as `control_command` (control→platform, `wan_event` QoS, `CONTROL` partition) but
   **no CFT filter**. `TeamAssignment` as its first topic.
3. **Platform control process:** A small Python DDS app that subscribes to
   `TeamAssignment` on `platform_lan` and publishes `RouterCommand` on `platform_lan`.
   Launched alongside each platform router by `run_mesh.sh`.
4. **WIS config:** Add writer to `wis_config.xml.template`; regenerate.
5. **Browser UI:** Team assignment interaction in `mesh_graph.js`.
6. **E2E test:** Publish a `TeamAssignment` on domain 20 → verify platform's
   `RouterHealth.team_partition` updates within a few heartbeats.

## Open Questions

- **Multi-team:** Should a platform support being on multiple teams simultaneously? The
  current `team_name` is a single string. Could extend to `sequence<string> teams` later,
  but single-team is simpler for the initial implementation and matches the existing
  "one team per platform" mental model.
- **Authority:** Who is allowed to write `TeamAssignment`? Today: anyone on domain 20.
  Future: DDS Security permissions could restrict the writer to WIS / the C2 operator
  process.
- **Ack / confirmation:** The platform could publish a `TeamAssignmentAck` back. For now,
  the existing `RouterHealth.team_partition` field (which already reflects the live
  partition set) serves as implicit confirmation — the dashboard sees the change reflected
  within 1–2 seconds.
