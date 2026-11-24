# Preemptive ACKNACK isolation spike

1. Consult Connext MCP, sync the shared correction log non-destructively, and
   check the installed 7.7 headers, schema and QosProvider.
2. Run two actual Routing Services with the persistent deployed processor,
   unchanged, inside `docker --network none --ipc private`. Use UDPv4 on
   domains 102, 103 and 105. Start the writer gateway before the reader gateway.
3. Compare identical external XML, differing only in the reader property.
   Capture bounded loopback RTPS, decode ACKNACK bases/bitmaps/flags and
   correlate exact writer/reader GUIDs with HEARTBEATs.
4. Drop one original Event DATA sequence at the sender system-call boundary,
   without changing the processor. Require a missing-sequence ACKNACK,
   retransmission and complete application delivery in both variants.
5. Retain compact reports/configuration/hashes and scoped raw captures. Remove
   owned processes, container and scratch state; do not touch the live mesh.

The MCP's first proposed XML element does not exist in the installed binding.
Its subsequent property-name advice is also subject to empirical verification.
Only measured base-zero startup suppression will be claimed, not removal of
ordinary reliable ACKNACKs or discovery traffic.
