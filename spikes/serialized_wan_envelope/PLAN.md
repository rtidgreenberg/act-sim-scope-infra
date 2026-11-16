# Serialized WAN envelope spike

1. Isolate Connext 7.7 in an owned no-network/private-IPC container. Do not
   modify the live mesh, existing sources, or active binaries.
2. Discover source publication and destination subscription DynamicTypes on
   separate LAN domains. Gate each topic independently.
3. Use supported Connext CDR serialization/deserialization, one fixed opaque
   envelope, and metadata-only native content filtering.
4. Verify complete command/nested-status samples with an independent application,
   add a third type without changing the envelope, and reject unknown/mismatched
   types and unsupported encodings. Observe keys and bound lifecycle support.
5. Record native push counts and limitations, clean up the owned container.

This is not a Routing Service processor or auto-QoS implementation. All fixed QoS
is external XML. No production runtime/configuration changes are in scope.
