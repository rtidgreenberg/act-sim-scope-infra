# Routing Service processor: service-owned WAN participant API access

**2026-11-07 result: compile/link PASS against Connext / Routing Service 7.7.0.**

The [probe](processor_api_probe.cxx) derives from `NoOpProcessor` and checks the
following calls inside processor callbacks:

```cpp
using dds::core::xtypes::DynamicData;

auto reader = route.input<DynamicData>("wan_input").dds_data_reader();
auto participant = reader.subscriber().participant();

participant->add_peer(peer);
participant->remove_peer(peer);

auto handles = dds::domain::discovered_participants(participant);
for (const auto& handle : handles) {
    auto data = dds::domain::discovered_participant_data(participant, handle);
}
```

An output provides the equivalent path:

```cpp
auto writer = route.output<DynamicData>("wan_output").dds_data_writer();
auto participant = writer.publisher().participant();
```

The installed endpoint accessors obtain the native DDS reader/writer from Routing
Service, then wrap it in a Modern C++ handle. Traversal therefore reaches the
**actual participant owning that configured port**, not a newly created or
domain-ID-selected participant.

## Verification boundary

- Compiled both input and output traversal, runtime peer mutators, discovered
  participant enumeration, and per-handle participant-data lookup.
- Compiled calls from `on_start` and discovery inspection from
  `on_periodic_action`.
- Linked a shared library with `--no-undefined`; `ldd -r` reported no unresolved
  symbols.
- Used the existing image
  `sha256:cbabafb1dab80c66483f092e1770249a6196d974b01592fe3a52979613f5f7a0`,
  SDK `/opt/rti.com/rti_connext_dds-7.7.0`, architecture `x64Linux4gcc8.5.0`.
- **No service was started and no DDS calls were executed.** This is an API
  probe, not a loadable, configured Routing Service ProcessorPlugin. Its exported
  factory forces the processor implementation into the compiled library; it is
  not Routing Service's plugin registration entry point.

The direct calls are API-feasible. This does not establish runtime lifecycle
correctness or a vendor support guarantee for processor-driven participant mutation.
Routing Service's connection-level Remote Administration `add_peer`/`remove_peer`
operations are a separately documented management path.

## Design constraints

1. Select a configured **WAN DDS port**. The opposite LAN port reaches a different
   participant; domain ID alone is insufficient when multiple connections use it.
2. Use handles only while the associated port is enabled. Installed headers
   require invalidating endpoint wrappers before disable; reacquire after
   recreation. Do not close service-owned entities.
3. Keep peer reconciliation participant-scoped and owned by one coordinator.
   Multiple routes can share that participant; avoid duplicate adds or competing
   removes from per-topic processors.
4. Prefer a stable, explicitly configured infrastructure route to an auto-created
   application route for directory consumption and discovery monitoring.
   Otherwise bootstrap functionality can disappear with the application route.
5. Keep configured-peer state separate from discovered-peer state, as established
   by the [C2 bootstrap experiment](../c2_peer_bootstrap/README.md).
6. Do not assume `remove_peer` means an application participant was deleted or
   every route was disabled. Test its concrete discovery/rematch effects separately.
7. The next gate is a real plugin/configuration test: C2-only WAN bootstrap,
   directory-driven add, expected GUID discovery, remove/re-add, route
   disable/re-enable, and shared-connection ownership.

## Reproduce

From the repository root, with the existing Docker image available:

```bash
docker run --rm --network none \
  -v "$PWD/spikes/routing_service_peer_api:/probe:ro" \
  -w /tmp connext:7.7.0 sh -lc '
    set -eu
    arch=x64Linux4gcc8.5.0
    g++ -std=c++11 -DRTI_UNIX -DRTI_LINUX -DRTI_64BIT \
      -fPIC -Wall -Wextra \
      -I"$NDDSHOME/include" -I"$NDDSHOME/include/ndds" \
      -I"$NDDSHOME/include/ndds/hpp" \
      -c /probe/processor_api_probe.cxx -o /tmp/processor_api_probe.o
    g++ -shared -Wl,--no-undefined /tmp/processor_api_probe.o \
      -L"$NDDSHOME/lib/$arch" -Wl,-rpath,"$NDDSHOME/lib/$arch" \
      -lrtiroutingservice -lrtirsinfrastructure \
      -lnddscpp2 -lnddsc -lnddscore -ldl -lpthread -lm \
      -o /tmp/libprocessor_api_probe.so
    ldd -r /tmp/libprocessor_api_probe.so
  '
```

This build creates no participants and requires no license mount. Build products
are disposable container-local files. No live mesh or router configuration changes
are necessary.

## Sources

The investigation consulted Connext AI, synchronized the shared
[known-wrong-answer log](../../docs/connext-ai-issues/connext-ai-issues.md), and
verified the relevant claims against installed headers and the compile/link result.
It did not adopt unverified claims about removal side effects.

- Installed `rti/routing/processor/Input.hpp` and `Output.hpp`: native DDS endpoint
  access and enabled-lifetime warnings.
- Installed `rti/routing/processor/Processor.hpp`: callback signatures.
- Installed `dds/domain/discovery.hpp` and
  `rti/domain/DomainParticipantImpl.hpp`: discovery lookup and peer APIs.
- [Routing Service Processor API](https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/api/routing_service/api_cpp/group__RTI__RoutingServiceProcessorModule.html).
- [Routing Service Remote Administration](https://community.rti.com/static/documentation/connext-dds/7.7.0/doc/manuals/connext_dds_professional/services/routing_service/remote_admin.html).
- [Original replacement investigation](../../docs/cpp_router/routing-service-replacement-investigation.md).
