# Runtime Behavior

## Runtime Route Lifecycle

At startup:

1. Load YAML.
2. Load QoS provider files and type XML.
3. Select the local side of each role-aware route by comparing `node.role` to `source` and
  `destination`; keep concrete routes as-is. This is route-side selection, not eager
  reader/writer construction.
4. Create the participants referenced by selected route sides and command/status topics.
5. Create discovery listeners or built-in-topic readers for publications/subscriptions on
  those participants.
6. Create the admin participant, command reader, command-ack writer, status writer, and the
  router `AsyncWaitSet`.
7. Publish initial `RouterStatus` listing all selected routes, including disabled routes and
  routes waiting on discovery.

On discovery:

1. Match discovered publications/subscriptions against the selected route table by
  participant role, topic name, partition, and optional filter requirements.
2. When a route's input writer is discovered, resolve the registered type name and
  `DynamicType` from discovery metadata, TypeLookup, loaded XML, or generated type support.
3. Resolve endpoint QoS aliases. For `auto` LAN reader QoS, derive compatible reader QoS
  from the discovered writer. For `auto` LAN writer QoS, wait for a compatible discovered
  local reader or fail the route after the configured discovery deadline.
4. Create/register the input and output `Topic` objects, then create the route's DataReader
  and DataWriter.
5. Create the route `ReadCondition`, attach it to the `AsyncWaitSet`, mark the route
  `ROUTE_ENABLED`, and publish updated `RouterStatus`.
6. If an endpoint disappears, mark the route `ROUTE_DEGRADED` or
  `ROUTE_WAITING_FOR_DISCOVERY`, detach the read condition, close affected entities if
  needed, and wait for rediscovery.

On data:

1. `AsyncWaitSet` dispatches one or more route read conditions.
2. Route drains the ready topic reader.
3. For `serialized_cdr` routes, copy/pass the input sample's CDR buffer to the output
  DynamicData writer without materializing fields.
4. For `dynamic_data` or `generated_type` routes, materialize the sample and write through
  the selected route implementation.
5. Invalid samples are interpreted as lifecycle transitions.
6. If the route is marked `mirror_instance_state: true`, recover the key and call
   `dispose_instance` or `unregister_instance` on the output writer.

On router command:

1. Validate `target_node` and `target_router` match this router or wildcard.
2. Validate `route_name`, `route`, and any patch payload against known route and
  participant names.
3. Enqueue the mutation on the controller strand so it is serialized with discovery and
  route runtime events.
4. Publish `RouterCommandAck` with accepted/rejected command result.
5. If the command was accepted and changed route state, increment `state_revision` and
  publish one full `RouterStatus` containing all routes.
6. If the command changes participant state, such as a `SET_PARTICIPANT_PARTITION` update,
  include the updated participant names and participant-level partitions in the same
  `RouterStatus` sample.
7. If the command was `DESCRIBE`, publish `RouterStatus` without changing `state_revision`.

## Dynamic Readers And Writers

The router should maintain a route registry:

```text
RouteDefinition
  name
  enabled
  input participant, qos, subscriber partition, optional content filter
  output participant, qos, publisher partition
  topics[]
    name
    optional reader_qos / writer_qos override
  mirror_instance_state

RouteRuntime
  definition
  discovery state by topic
  TopicRouteRuntime[]
    topic objects by participant
    forwarding_mode
    DataReader/DataWriter implementation
    ReadCondition
    AsyncWaitSet attachment state
  key cache by reader instance handle
  counters
```

Role-aware route fields such as `source`, `destination`, `source_side`, and
`destination_side` exist only during config loading. Once the local role is known, each
matching route contributes one selected active side to the route registry. The registry can
contain disabled routes and routes waiting on discovery; DDS readers/writers are not created
until discovery provides enough type and QoS information.

Each topic entry under a route can get its own reader/writer pair after discovery, but it
shares the route's input participant, output participant, filter, endpoint
publisher/subscriber partitions, endpoint QoS, and forwarding mode unless overridden. The
activation sequence is intentionally simple: discover writer, resolve type, resolve QoS,
create reader/writer, create `ReadCondition`, attach it to the `AsyncWaitSet`, then start
forwarding. Recreate readers/writers on filter, endpoint partition, inherited
participant-partition, type, or QoS changes instead of trying to update every DDS entity in
place. This keeps failure handling legible.

## Instance State Handling

For normal bulk routes, forwarding valid samples is enough. For state-critical keyed
routes, the router needs the same mirroring rule proven in [relay/](../../relay/):

```text
reader sample valid        -> writer.write(sample)
reader NOT_ALIVE_DISPOSED  -> writer.dispose_instance(handle)
reader NOT_ALIVE_NO_WRITERS -> writer.unregister_instance(handle)
```

The route definition should opt in:

```yaml
mirror_instance_state: true
key_fields: ["msg.source"]
```

For the first C++ version, keep this conservative:

- keep lifecycle routes on `dynamic_data` or `generated_type` mode if key recovery requires
  field access;
- cache key fields from valid samples by reader instance handle when fields are available;
- use `reader.key_value()` when available and compatible with the chosen API path;
- skip lifecycle mirroring when the key is unrecoverable in a serialized pass-through path;
- log every skip with route name and instance handle.

The POC does not need every ACT topic to be keyed. It only needs to prove the mechanism on
one keyed state route and leave the rest as ordinary sample forwarding.
