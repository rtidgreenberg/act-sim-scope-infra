# DDS isolation strategies: layers, costs, and trade-offs

**Date:** 2026-11-07

**Scope:** Connext 7.7.0 squad/team discovery and data isolation. This is a
comparison report, not a new architecture decision or runtime qualification.

**Presentation:** [Connext Edge Mesh Overview PDF](../slides/connext-edge-mesh-overview.pdf) |
[Editable Marp source](../slides/connext-edge-mesh-overview.md).

## Executive distinction

Isolation can mean three different requirements:

1. **Discovery isolation:** limit who learns participant/endpoint metadata.
2. **Data isolation:** limit which endpoints match or which samples cross a route.
3. **Security isolation:** authenticate identities and authorize access.

These mechanisms are complementary, not interchangeable. A participant can see
another platform's endpoints without receiving its team data. Conversely, suppressing
ordinary discovery is not an authorization or packet-confidentiality boundary.

**DP** below means DomainParticipant. Costs count **WAN DPs per platform**, excluding
its separate LAN DP. Creating another DP means another DDS entity, not necessarily
another process, host, or physical network.

## Strategy comparison

| Strategy | Primary layer | What it isolates | WAN DP cost | Pros | Cons / boundary |
|---|---|---|---|---|---|
| Publisher/Subscriber partitions | Endpoint matching and data audiences | Writer/reader matches, such as `TEAM_ALPHA` versus `TEAM_BRAVO` | One WAN DP can support many audiences | Lightweight mutable membership; different topics can have global, team, and direct-peer audiences; preserves global health/navigation | Participant and endpoint metadata can remain globally visible; not discovery isolation or authorization |
| DomainParticipant partitions | Participant matching / endpoint discovery | Unmatched participant scopes do not proceed to endpoint discovery | One DP for one membership set; commonly two for simultaneous global and team-discovery scopes | Suppresses out-of-scope endpoint metadata; supports team membership and direct scopes without a separate DDS domain | Scope applies to all endpoints on that DP; common/wildcard membership broadens visibility; reassignment requires rematch; bootstrap traffic can still exist |
| C2-issued exact peer directory with strict acceptance | Participant discovery topology | Which configured remote participants are accepted for ordinary discovery | One DP for one allowed-peer topology | Central policy; sparse explicit teammate lists; ordinary-participant add-once path tested; no second WAN DP inherently required | Reciprocal updates needed with `accept_unknown_peers=false`; all topics on the DP share its allowed topology; C2 bootstrap dependency; remove/re-add and membership revocation remain unqualified |
| Separate DDS domains | DDS discovery/data namespace | Direct DDS matching across domain IDs | Additional DP for each domain used concurrently | Clear structural separation; useful for independent LAN/WAN or mission systems | Cross-domain communication needs an intentional bridge; dynamic squads cause domain/DP/configuration churn; packets still share the physical network unless separately segmented |
| DDS domain tags | Participant matching within a domain ID | Participants with different domain tags do not match | Additional DP for simultaneously participating in distinct tags | Extra logical grouping without allocating another domain ID | Participant-scoped, not per-topic; does not by itself eliminate physical-network bootstrap exposure or enforce authorization |
| Content filters / destination fields | Sample selection | Which samples a reader selects or a route forwards | No extra WAN DP inherently needed | Precise addressed commands and payload selection; combines with team partitions | Does not hide participant/endpoint metadata; sender-side bandwidth savings depend on filter execution and configuration; destination conventions alone are not access control |
| Route enable/disable / on-demand endpoints | Router forwarding / endpoint lifecycle | Whether a path forwards and, if endpoints are absent, whether that route's metadata exists | Usually unchanged; reduces endpoint count rather than DP count | Avoids unnecessary forwarding; lazy routes can remove dormant endpoint metadata | Pausing forwarding may leave endpoints discoverable; enabling can incur discovery/type/match latency; does not isolate other routes sharing the DP |
| Network segmentation / firewall reachability | Network transport | Which addresses/ports/interfaces can exchange traffic | Depends on connectivity/bridge design, not inherently another DP | Can constrain actual network reachability; useful with domains and DDS policy | Must preserve discovery, data, and repair paths; DDS advertised locators/NAT require care; not a substitute for DDS identity/topic authorization |
| DDS Security authentication, access control, and protection | Identity / authorization / cryptographic protection | Who may join, publish, or subscribe according to governance and permissions; protection according to configured policy | No extra DP inherently required | Appropriate DDS security boundary; policy-based permissions and configured confidentiality/integrity | Certificate/key and permission lifecycle, deployment compatibility, processing/wire overhead; dynamic team policy needs explicit permission-update/revocation design |

## Who controls the policy, and what changes on the wire?

**Policy ownership and enforcement are separate axes.** Application logic can
choose a team or peer and then ask DDS to enforce that choice. Calling a DDS API
from an application or Routing Service processor does not make the underlying
mechanism an application-only filter.

| Strategy | Who chooses / applies the policy? | Enforcement layer | Discovery impact | User-data impact |
|---|---|---|---|---|
| Application topic allowlists and route configuration | Application/operator selects topic names and permitted forwarding paths | Router/processor forwarding and DDS endpoint lifecycle | Pausing forwarding does not inherently remove endpoint metadata. Avoiding or destroying endpoints removes their metadata contribution, not unrelated participant discovery. | Topics with no forwarding path do not cross that router. Existing direct DDS paths elsewhere are unaffected. |
| C2 directory plus `add_peer` / `remove_peer` | Application/C2 chooses allowed platforms; application or processor invokes peer APIs | DDS participant discovery | `add_peer` changes discovery destinations, not a per-topic audience; strict acceptance and reciprocal configuration constrain the intended peer topology. Removal effects need runtime qualification. | Adding a peer does not guarantee payload delivery; endpoint topic/type/QoS/partition matching is still required. This peer topology applies to all endpoints on the participant. Removal is not yet proven to revoke established paths. |
| DomainParticipant partitions | XML configuration or application/admin membership updates | DDS participant matching / discovery | Unmatched participant scopes do not exchange endpoint discovery; bootstrap observation is not necessarily eliminated. | Endpoints cannot match across unmatched participant scopes, regardless of their own topic audiences. |
| Publisher/Subscriber partitions | XML configuration or application/admin audience updates | DDS endpoint matching | Participants can still exchange endpoint metadata; partition changes also produce endpoint-discovery updates. | Only matching endpoint audiences exchange payloads. A single DP can host global and team-specific endpoint audiences. |
| DDS ContentFilteredTopics | Application/operator configures a reader's filter | DDS sample selection | No participant or endpoint-metadata isolation. | Selects matching samples for that reader; execution location and sender-side traffic savings depend on configuration. |
| Destination fields / application sample filters | Application/processor evaluates payload fields | Application sample selection / forwarding | No discovery isolation. | Drops or forwards selected samples at that application boundary; a destination field alone does not prevent another reader from receiving the original sample. |
| DDS domains / domain tags | Deployment/application selects participant scope | DDS participant namespace / matching | No direct cross-scope DDS matching; tags do not guarantee absence of bootstrap packets on the shared network. | No direct cross-scope exchange; an intentional bridge can forward selected topics across scopes. |
| Network segmentation / firewall rules | Network operator/controller configures reachability | Network transport | Discovery packets cannot traverse a blocked path; cached DDS state can persist until lifecycle/timeouts process the loss. | Affected payload and repair traffic is also blocked; remaining permitted paths are independent. |
| DDS Security | Security authority issues governance/permissions and identity material | DDS security plugins | Authentication and discovery protection follow configured policy, not a universal claim that all bootstrap presence is hidden. | Enforces configured read/write authorization and payload protection; operational team assignment is not automatically a security permission update. |

For the squad example, **C2 deciding "Alpha talks to Alpha" is application-level
policy**. Applying that list through `add_peer` is **DDS-level discovery control**.
Choosing which topic routes to create or forward is application/router-level
control; participant and endpoint partitions are DDS matching mechanisms.

The same logical C2 policy can therefore drive several different enforcement
layers. Reports and UI should not label a peer-list change as a topic filter, nor
label endpoint partitioning as suppression of all discovery traffic.

## Does participant partitioning require another DP?

**Not inherently.** An existing participant has a mutable participant-partition
membership set and can change that set when its team changes.

Another DP is relevant when a platform needs **simultaneous, different discovery
audiences** for different traffic classes:

| Requirement | Typical arrangement | Consequence |
|---|---|---|
| All platform traffic is restricted to C2 and current teammates | One WAN DP with a team participant scope or strict peer topology | Cross-team direct navigation/health is unavailable unless intentionally provided through another path |
| Global metadata is acceptable; only team payloads must be isolated | One shared WAN DP; global/team Publisher/Subscriber partitions | Simple global presence/navigation with endpoint-level team data isolation |
| Global navigation/health plus team-only application metadata discovery | Separate global WAN DP and team WAN DP | Each DP has its own discovery scope; extra participants add discovery/lifecycle/resource overhead |
| Distinct concurrent domains/tags are required | A DP for each required domain/tag scope | Explicit cross-scope routing and ownership are needed |

Partition lists are **alternative matching memberships**, not an intersection of
restrictions. If every platform includes the same participant partition `SHARED`,
that common entry makes platforms eligible to discover one another even if their
team entries differ. Adding `TEAM_ALPHA` beside `SHARED` does not restore team-only
endpoint discovery.

A C2 participant with intentionally broad matching scope is conceptually different
from giving every platform a shared matching entry. Ordinary participant discovery
does not automatically relay team membership or peer lists through C2.

## Practical combinations

| Design | Discovery policy | Data policy | Main benefit | Main trade-off / gate |
|---|---|---|---|---|
| Current shared-WAN baseline | Common participant scope, global metadata | Per-topic Publisher/Subscriber audiences | One WAN DP; global health/navigation with isolated team data | Accepts broader metadata visibility |
| C2-directed sparse teams | Exact peers supplied by C2; strict reciprocal acceptance | Endpoint partitions/filters still select topics and payloads | Centralizes who should talk to whom without inherently adding a DP | Restricts every topic on that DP; qualification needed for remove/re-add, outages, and team reassignment |
| Separate global and team scopes | Global DP plus team-partitioned DP | Independent endpoint audiences on each DP | Keeps global services while suppressing team metadata outside the team | Additional DP discovery/resource overhead and lifecycle ownership |
| Security-enforced deployment | Chosen topology/partition strategy plus DDS Security | Authorized endpoint/topic/partition access | Separates operational audience selection from actual authorization | Security lifecycle and dynamic permission changes need their own design and tests |

## Evidence and limits

| Finding | Evidence level |
|---|---|
| Shared participant scope plus endpoint team partitions preserves team payload audiences while exposing broader metadata | Repository integration and deployed acceptance documented in [Single WAN Participant Decision](single-wan-participant.md) |
| Participant-scoped team matching, removal, and direct-peer membership are separate from endpoint-scoped matching | Existing [team partition e2e cases](../../router/test_e2e/test_team_partition.py), with both modes parameterized |
| C2-only initial bootstrap, directory-driven additions, late startup, graceful restart, and strict reciprocal acceptance | [C2 discovery report](c2-peer-bootstrap-report.md): 6/6 isolated protocol runs; not lossy-WAN qualification |
| A Routing Service processor can reach its actual WAN participant and compile/link peer/discovery APIs | [Processor API findings](../../spikes/routing_service_peer_api/README.md): compile/link only; no loaded-plugin execution |
| Domains, tags, participant partitions, endpoint partitions, and security represent different isolation layers | Installed Connext 7.7.0 manual/API documentation; no new comparative runtime experiment in this report |

No strategy is declared a universal bandwidth winner. Historical synthetic one-DP
comparisons used broad peer probing and nonproduction health timing. The
[trade-off spike's assurance notes](../../spikes/wan_dp_route_tradeoffs/README.md)
also limit retrospective certification of older packet captures. Those results
must not be projected onto sparse C2 directories, production RF conditions, or
security-enabled traffic.

Participant partitions gate matching and endpoint-discovery exchange, not the
visibility of every packet on the network. In particular, SPDP2 bootstrap contains
information needed to decide whether participants can match. Even if an unmatched
participant is absent from a normal discovered-participant roster, that does not
establish that its bootstrap presence/locators cannot be observed on the wire.

Similarly, accepted `add_peer` state is not discovered state. Removal from a
desired-peer directory has not been demonstrated as complete runtime revocation;
the application must not report a peer as disconnected or unauthorized merely
because it removed an entry from its local desired set.

## Recommendation for the squad scenario

Choose the required boundary before choosing the implementation:

- **Only team data needs isolation:** retain a shared WAN participant and use
  endpoint partitions/filters. This preserves global health/navigation.
- **Team endpoint metadata must also be isolated:** evaluate participant-scoped
  discovery or the strict C2 peer directory. Use another scope/DP if global direct
  platform services must remain concurrently available.
- **C2 should decide the allowed communication graph:** the directory is a
  control-plane mechanism and can complement either partition strategy.
- **Untrusted or unauthorized nodes must be excluded:** add an actual security
  design; discovery lists and QoS alone do not provide that guarantee.

The next comparative test should preserve identical application audiences, health
timing, endpoint activation, and RF conditions across alternatives. Verify both
metadata visibility and exact application recipients through team moves, direct
taps, C2 outages, restart, and removal. Measure traffic rather than assuming
another DP or fewer peers necessarily increases or decreases total bandwidth.

## Documentation references

The investigation consulted Connext AI, synchronized and checked the shared
[known-wrong-answer log](../connext-ai-issues/connext-ai-issues.md), and checked the
partition/matching claims against installed manuals and repository evidence.
No unverified API advice is needed to implement this documentation.

- [Isolating DomainParticipants and Endpoints](https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/manuals/connext_dds_professional/users_manual/users_manual/Creating_ParticipantPartitions.htm).
- [PARTITION QoS](https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/manuals/connext_dds_professional/users_manual/users_manual/PARTITION_QosPolicy.htm).
- [DISCOVERY QoS](https://community.rti.com/static/documentation/connext-dds/7.7.0.1/doc/manuals/connext_dds_professional/users_manual/users_manual/DISCOVERY_Qos.htm).
- [DDS Security Access Control](https://community.rti.com/static/documentation/connext-dds/current/doc/manuals/connext_dds_secure/users_manual/p2_core/access_control.html).
