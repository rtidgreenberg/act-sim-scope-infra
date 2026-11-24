# Binary WAN compression qualification

Use actual Connext 7.7.0 Routing Service processes, the fixed serialized WAN
envelope and genuine application CDR bytes. Keep production and the mesh unchanged.

## Gates

1. Compare uncompressed, LZ4, zlib, bzip2 and an 8192-byte LZ4 threshold.
   Author fixed policies in external XML; retain Event/State/Periodic contracts.
2. Exercise small nested status, medium/large structured binary records,
   high-entropy bytes and already-compressed bytes, with two repetitions.
3. Require exact envelope bytes and complete original DynamicData equality
   after automatic native decompression and original CDR deserialization.
4. Count actual WAN frames and compression IDs; require writer-side metadata
   exclusions. Change a compressed Event reader's interest through native
   administration and check sender suppression after each update.
5. Measure application command/echo RTT and gateway-process CPU separately from
   fixture preparation and exhaustive validation. Do not claim RF performance.
6. Exercise a reader that does not accept the writer's compression algorithm.
   Record all native errors and reject problematic variants explicitly.
7. Retain compact reports, bounded captures and exact generated XML. Reap only
   owned processes and remove the private container/shared-memory state.
