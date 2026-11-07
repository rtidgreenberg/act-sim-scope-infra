# Single WAN participant decision and migration

Accepted 2026-11-07. See [D122](design-decisions.md#d122--single-wan-participant-with-endpoint-scoped-team-membership-2026-11-07-accepted).

## Evidence captured

The [comparison report](../../debug/test_reports/wan_dp_squad32_health30_participant_vs_pubsub_20261107.md)
and [measurement data](../../debug/test_reports/wan_dp_squad32_health30_participant_vs_pubsub_20261107.json)
retain eight accepted isolated Connext 7.7 cases: 32 platforms, teams of four/eight,
two repetitions, 150-second steady windows, fourteen synthetic application types,
and 592 reader/writer endpoints. They verify exact endpoint-metadata ownership,
application recipients, capture accounting, source/image hashes and logical
liveliness assertion cadence.

| Teams | Two-DP discovery kb/s | Single-DP discovery kb/s | Two-DP remote endpoint records/platform | Single-DP records/platform |
|---:|---:|---:|---:|---:|
| 4 | 377.88 | 267.39 | 142 | 574 |
| 8 | 384.85 | 264.24 | 206 | 574 |

One DP lowered total steady discovery by 29.2-31.3%, despite broader metadata
exposure. Two DPs lowered startup/assignment cost and liveliness-message fanout,
but their additional discovery outweighed those savings with the identical broad
0-95 initial-peer probing used in this experiment. The startup difference amortized
after approximately 108 s / 32 s; this is not a population crossover.

The experiment used **30 s health leases**. The user explicitly chose to **retain
production's 3 s automatic health leases**, 2 s deadline and 1 Hz heartbeat.
Do not claim these measured rates or percentages for the deployed router:
production schemas, endpoint activation, health audiences, RF conditions and lease
settings differ. The [isolated lease comparison](../../debug/test_reports/health_liveliness_3_vs_30_20261107.md)
also records the slower crash detection of a 30 s lease.

## Active configuration

[control-platform.yaml](../../router/config/control-platform.yaml) now creates:

- Control: `control_lan` plus one `control_wan`.
- Platform: `platform_lan` plus one `platform_wan`; `location_wan` is removed.
- Both WAN roles use protected participant partition `SHARED`: metadata discovery
  is global, not a team-isolation boundary.
- Global navigation routes use publisher/subscriber partition `NAVIGATION` on
  `platform_wan`. `CONTROL` and `PLATFORM` audiences remain independent of assignment.
- Health stays on the existing WAN participant with unchanged XML QoS. All globally
  matched routers can see its unpartitioned heartbeat endpoints and form global
  presence/RTT views; team membership does not restrict heartbeat visibility.

## Team membership contract

`team_scoped: true` with `team_partitions_on_endpoints: true` opts a participant
into endpoint-scoped membership. Omitted route endpoint partitions inherit its
private/team/direct-peer set. Explicit endpoint partitions override inheritance.
The private set starts with the node identity, so unassigned platforms cannot
exchange team data.

Existing `ADD_PARTICIPANT_PARTITION` / `REMOVE_PARTICIPANT_PARTITION` commands
remain compatible with the team controller and direct-peer operators. For this
explicit mode they update route Publisher/Subscriber partitions, **not**
DomainParticipant QoS. Multiple teams/taps remain supported, identity removal is
rejected, duplicate adds are idempotent, and the set stays bounded at 16 entries.

Live parents change in place without tearing down healthy route entities. Newly
created/re-enabled endpoints resolve the current membership. A failed update
rolls back membership and all affected live parents; a rollback failure stops the
uncertain route and exposes an error rather than acknowledging success.

`RouterParticipantStatus.participant_partition` continues to report actual
participant discovery partitions. The new `team_partition` field reports endpoint
membership separately. `RouterHealth.team_partition` retains its existing shape
and dashboard semantics, including the private identity entry.

The opt-in defaults off: older participant-scoped configs keep their existing
discovery-gating behavior. LAN participants and additional physical WAN networks
are not merged. DDS fixed QoS remains in external XML.

## Validation and deployment

- Rebuilt C++ router and generated types: all five C++ unit gates passed.
- Six isolated real-DDS integration cases passed: legacy/new team isolation,
  removal/direct-peer taps, global endpoint metadata before assignment, legacy/new
  health and LAN mesh membership, production full-config delivery, and addressed
  control-command filtering.
- The health test's second LAN uses free domain 201 instead of an invalid
  domain above 232. Tests run in a network-isolated container with temporary logs;
  they do not discover the live mesh.
- Production QoS SHA-256 remains
  `c505615ed129657ee2b58070000984eb1da1320111a687e732b25710ee7efd46`.
- The committed type XML was regenerated from canonical IDL. The admin status
  type changes, so deploy with a coordinated owned-lifecycle restart, not a
  mixed-version rolling change.

The user authorized restarting the existing six-platform EMANE mesh after these
gates. Before restart all six platforms were alive, all teams unassigned, and
the controller reported no active impairments. Post-restart acceptance must
confirm one WAN participant per router, all peers rediscovered, navigation/control
delivery, team isolation, health membership and the rendered dashboard.

### Deployed acceptance

Run `container-20261107T130854-3184695` passed:

- All seven routers logged exactly one LAN and one WAN participant.
- All six platforms were `PRESENCE_ALIVE` / `ROUTER_OK`.
- Every node received health from all six other nodes. Control received navigation
  from all six platforms; each platform received navigation from the other five.
- The owned command/status audit verified 10,028 / 10,028 expected deliveries.
- Browser assignments placed 30/31 in Alpha and 32/33 in Bravo, leaving 34/35
  unassigned. PlatformData arrived only at the paired same-team receivers:
  13/11 samples for Alpha and 10/10 for Bravo, with no cross-team or unassigned
  recipients. These counts establish isolation, not a sequence-matched delivery rate.
- The real dashboard rendered seven nodes / 42 edges over a live WebSocket with
  no browser console errors. All platforms were restored to their original
  unassigned state; experiment impairments were empty.
- Two bridge tests additionally cover global health audiences, legacy health
  compatibility and symmetric direct-peer taps. New container manifests explicitly
  declare the full-mesh health audience; older manifests keep their original model.

The [deployment record](../../debug/test_reports/single_wan_dp_migration_20261107.json)
retains hashes, test counts and observed recipients.
[Final dashboard capture](../../debug/test_reports/single_wan_dp_mesh_20261107.png)
and [temporary team-test capture](../../debug/test_reports/single_wan_dp_teams_20261107.png)
are retained alongside it. The mesh remains running on the new configuration.
