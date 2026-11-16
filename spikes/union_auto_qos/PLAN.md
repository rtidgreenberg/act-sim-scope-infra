# Typed unions plus native LAN auto-QoS spike

## Scope and acceptance

Prove one integrated data path, without changing the production router or live mesh:

1. Discover original application types on an isolated LAN, export a frozen catalog,
   generate typed WAN unions, and load the deployed schema before Routing Service starts.
2. Let **native Routing Service** infer LAN endpoint QoS with
   `ON_DOMAIN_MATCH` and `auto_configure_qos=true`; compare with false controls.
3. Use independent per-topic LAN routes, with one shared WAN endpoint owner inside
   the processor plugin. A missing LAN topic must not block available topics.
4. Discover exactly one writer and reader for each physical WAN name:
   **Event, State, Periodic**. Do not equate native per-route ports with shared endpoints.
5. Preserve whole commands and nested status structures, including keys, timestamps,
   audit sequences, variable-length sequences and fixed arrays.
6. Configure six priorities, asynchronous publishing, one shared flow controller,
   and two High/Normal MultiChannel bands per family in XML.
7. Test a late missing-topic activation without changing any WAN endpoint identity,
   and demonstrate a filter that can inspect a typed union payload.

## Non-goals

No live deployment, production configuration edits, migration/cutover, arbitrary
schema exporter, compatibility across schema revisions, restart/lifecycle ownership,
load/fairness benchmark, or complete auto-QoS policy qualification.

## Execution and evidence

`sg docker -c 'bash spikes/union_auto_qos/run.sh'`

The runner creates a private-network/private-IPC `connext:7.7.0` container,
mounts source and license read-only, builds fresh in container-owned `/work`,
and removes that container on success or failure. It does not use host networking,
live DDS domains or host build products. Only compact ignored evidence is retained
in `debug/test_reports/union_auto_qos.txt`.

The final gate passes six automatic/false-control cases, three exact exported-type
comparisons, 39 automatic unique full-payload comparisons, 18 control comparisons,
three typed-filter exclusion scenarios and three independent late-topic activations.
Every case measures the WAN endpoint inventory.
