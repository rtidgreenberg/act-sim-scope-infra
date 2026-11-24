# Routing Service + LBED spike

## Objective

Use the installed Connext 7.7 Routing Service DDS adapter to forward a fixed
binary envelope across Event / State / Periodic with static WAN endpoint
discovery. Keep LAN discovery dynamic. Do not modify production configuration,
types, binaries or the owned mesh.

## Gates

1. Compare SPDP2 + SEDP, classic SPDP + LBED, and SPDP2 + LBED.
2. Run two actual Routing Service processes, each owning one WAN writer/reader
   per family and a separate LAN participant.
3. Check both directions with exact keyed metadata and opaque payload equality,
   including all octet values and an empty payload.
4. Require participant traffic and application forwarding in every positive
   capture. Require SEDP traffic in the baseline and no SEDP exchanges in LBED.
5. Restart one gateway and check fresh traffic; do not claim VOLATILE replay.
6. Omit one static remote writer and prove that its path fails while other
   families still forward. A successful process start is not sufficient.
7. Record source/image hashes, bounded captures, logs and explicit failures.
   Clean only owned child processes/container state.

## Boundaries

This tests opaque-envelope forwarding by Routing Service, not integration of a
binary codec into the ACT processor. LAN endpoints already use the fixed
envelope; original application deserialization is covered by the separate
serialized-envelope spike. No production auto-QoS, adaptive tuning, fleet-load,
WAN impairment, dynamic CFT-update or mission acceptance is implied.

## Dynamic CFT follow-up

1. Keep the forwarding matrix and its evidence separate; add `run.sh --cft`.
2. Change the actual Routing Service-owned WAN Event input through remote
   administration on isolated domain 113. Require successful UPDATE replies
   and configuration GET readback.
3. Offer the same three selectors while expanding the filter from one to two
   to three topics, then narrowing to one. Compare SEDP, LBED with a static
   initial CFT description, and LBED without that description.
4. Measure WAN DATA independently of LAN deliveries; require a successful SEDP
   writer-filtering control and correct local selection in every variant.
5. Verify the receiving reader GUID remains unchanged across every phase,
   native input enable/update counts, zero capture drops and zero native errors.
6. Report lack of writer suppression explicitly with a nonzero feature gate,
   even when receiver filtering succeeds. Do not equate local CFT acceptance
   with WAN bandwidth savings.

Completed: local filtering passes all 12 phases; both LBED variants transmit
all offered samples. The SEDP control suppresses unwanted transmissions.
See the README for measured counts and the intentionally nonpassing feature gate.
