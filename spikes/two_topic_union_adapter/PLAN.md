# Whole-structure union adapter spike

## Question

Can a Connext 7.7 Modern C++ DynamicData adapter select a declared union case
from a topic mapping, copy the entire existing structure, send only Event/Status
over the WAN, and reconstruct the original topic/type at the other end?

## Scope

- Reuse production payload IDL, qualification unions, and production XML QoS.
- Navigation, RouterHealth, ControlCommand, and PlatformCommandAck.
- Pattern selection, exact schema checks, reverse topic mapping, explicit errors.
- Compare entire structures and envelopes, including sequences and boundary values.
- Count one selected input -> one WAN DATA -> one original-topic output.
- Confirm rejected content/audience generates no additional pushed WAN DATA.
- Forward dispose and unregister using keys/handles, then revive disposed instances.
- Keep all generated XML/build/runtime files in a private container's `/tmp`.

Field mapping, production-router integration, fleet performance, restart/crash
recovery, command execution, and presence/RTT migration are out of scope.

## Architecture

Six real participants across three private UDPv4 domains (201, 203, 202):

`source application -> LAN input adapter -> WAN -> LAN output adapter -> destination application`

Exactly two WAN topic names. Status has separate application and health writers
to retain their XML QoS classes; Event has one reliable writer. Each LAN leg
retains its original type and exact topic name.

The spike adds `original_topic` envelope metadata for invertible wildcard mappings.
Its instance ID includes the variant, length-prefixed original topic, and original
local key. Multiple topic aliases therefore cannot overwrite one another.

## Completion criteria

Build with warnings as errors, execute all checks twice, retain a compact report,
document measured results and limitations, and update the migration roadmap.
