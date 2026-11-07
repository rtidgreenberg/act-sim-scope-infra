# C2-seeded runtime discovery spike

## Question

Can a participant bootstrap with only C2, receive a platform directory over DDS,
add each platform with `add_peer`, and verify direct discovery by logical identity?
Does each locator need repeated application calls?

## Controlled topology

One network-isolated Connext 7.7.0 container; independent spawned Python processes
for C2, Client, Open, Late, and Strict. UDPv4 on loopback, no multicast or SHMEM.
XML fixes participant IDs and exact `[id]@udpv4://127.0.0.1` peers, avoiding
accidental default participant-ID scanning. Domains 210/211 are private to the
container network namespace. Repeat for SPDP and SPDP2.

C2 has no initial peers. Every other participant starts with only C2.
Strict rejects unknown peers; the others accept them. C2 sends an application
directory with Open, Late, Strict, and a nonexistent Missing locator.

## Assertions

1. C2 discovery works but does not give Client transitive platform discovery
   during a controlled pre-directory observation window.
2. A real DDS directory sample causes exactly one successful add per locator.
3. Open is discovered mutually, with full participant configuration and a
   sequence-matched application request/ack roundtrip.
4. An accepted absent locator is not reported as discovered.
5. Repeated directory messages do not repeat adds.
6. Late starts after eight seconds of absence and discovers without re-adding.
7. Strict requires a reciprocal client peer add before mutual discovery works.
8. Direct platform roundtrips continue after C2 closes and expires.
9. Late restarts with a new GUID, with C2 gone, and rediscovers using the original
   add; assert current GUID and another application roundtrip.
10. QoS `initial_peers` stays C2-only despite runtime peer additions.

## Evidence and boundaries

Keep compact snapshots, GUIDs, add counts, timings, image ID, and source hashes
in `debug/test_reports/c2_peer_bootstrap.json`. Use owned lifecycle cleanup and
disposable `/tmp` staging. No live mesh changes. This is a discovery-path proof,
not a WAN/EMANE loss, security, NAT, locator-change, or scalability qualification.
The one-second announcement and four-second participant lease are accelerated
test settings authored in XML, not production recommendations.
