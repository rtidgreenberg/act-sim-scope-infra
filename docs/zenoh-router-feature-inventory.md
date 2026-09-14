# Eclipse Zenoh Router Feature Inventory

This document captures the implemented features and functionality of the upstream Eclipse Zenoh router, `zenohd`, reviewed against the `main` branch on 2026-09-14.

This is a review of the upstream Zenoh project, not the ACT C++ DDS router in this repository. The source of truth is the [Eclipse Zenoh repository](https://github.com/eclipse-zenoh/zenoh), especially [`zenohd/`](https://github.com/eclipse-zenoh/zenoh/tree/main/zenohd), the [`zenoh` crate](https://github.com/eclipse-zenoh/zenoh/tree/main/zenoh), and [`plugins/`](https://github.com/eclipse-zenoh/zenoh/tree/main/plugins).

## Executive Summary

`zenohd` is a daemon used to build Zenoh network infrastructure. The executable is intentionally thin: it parses command-line options, creates or modifies a Zenoh `Config`, opens the Zenoh runtime, and parks while the runtime and plugin manager perform the networking work.

The router provides or enables:

- Key-expression-based data routing between Zenoh sessions.
- Router, peer, and client deployment modes through Zenoh configuration, with router mode as the default when no mode is configured.
- Explicit listen and connect locators for controlled topology construction.
- Multicast scouting for peer and client discovery, with an option to disable it.
- Pub/sub data distribution, queryable/get request-reply, and storage/query flows through the Zenoh runtime.
- Multiple transport families, including TCP, UDP, TLS, QUIC, WebSocket, Unix sockets, and serial support when compiled with the corresponding features.
- Optional shared-memory transport support.
- HLC timestamp insertion for routed data unless disabled.
- A built-in admin space for runtime inspection and, when permitted, administration.
- A dynamic plugin system, including REST and storage-manager plugins shipped with `zenohd`.
- Extension through independently maintained plugins such as DDS, ROS 2 DDS, WebSocket/remote API, and web-server integrations.

## Runtime Architecture

### `zenohd` executable

The entrypoint is [`zenohd/src/main.rs`](https://github.com/eclipse-zenoh/zenoh/blob/main/zenohd/src/main.rs). Its main responsibilities are:

1. Initialize structured tracing/logging.
2. Parse command-line arguments with `clap`.
3. Load the default configuration, a JSON5/YAML file, or an inline JSON5 configuration.
4. Apply command-line overrides to the configuration.
5. Enable the admin space and plugin loading.
6. Open the Zenoh runtime with `zenoh::open(config).wait()`.
7. Keep the process alive while the runtime and plugins operate.

The routing, session, transport, discovery, storage, and query machinery is implemented primarily by the `zenoh` crate and its internal runtime components. `zenohd` is therefore a runtime host and plugin manager, not a separate forwarding engine with its own per-topic DDS-style route graph.

### Deployment modes

The Zenoh runtime supports three normal session modes:

- **Router:** infrastructure node that routes between connected sessions and networks.
- **Peer:** application-capable node that can communicate with other peers and routers.
- **Client:** application/session node that normally connects through routers or peers.

If the loaded configuration has no mode, `zenohd` sets the mode to `Router`. A configuration can override the mode when a different deployment role is required.

## Configuration and Startup

The router accepts JSON5 or YAML configuration files through `--config`. The configuration model is provided by the `zenoh-config` and `zenoh` crates; the commented configuration reference is linked from the [zenohd README](https://github.com/eclipse-zenoh/zenoh/blob/main/zenohd/README.md).

Configuration and command-line input can define:

- Runtime mode.
- Router ID.
- Listen endpoints.
- Connect endpoints.
- Scouting and multicast behavior.
- Transport and link settings.
- Timestamping behavior.
- Admin-space enablement and permissions.
- Plugin loading, plugin paths, and plugin-specific configuration.
- Startup declarations such as subscriptions.
- Storage-manager storages and backend settings.
- Metadata and user-defined configuration values.

The router ID can be supplied as a hexadecimal identifier. If omitted, a random unsigned 128-bit ID is generated. The ID must be unique within the deployment.

### Command-line overrides

The entrypoint supports:

- `-c, --config <PATH>` for a JSON5/YAML configuration file.
- `-l, --listen <ENDPOINT>` for one or more incoming-session locators.
- `-e, --connect <ENDPOINT>` for one or more peer/router locators.
- `-i, --id <ID>` for an explicit router identity.
- `--cfg KEY:VALUE` for arbitrary JSON5 configuration insertion.
- `--adminspace-permissions r|w|rw|none` for admin-space access control.

Inline configuration can replace the whole loaded configuration using an empty `--cfg` key. Otherwise, `--cfg` applies a JSON5 value at a configuration path.

## Routing and Data Distribution

The core Zenoh runtime routes data using hierarchical key expressions rather than DDS topics. A publisher writes to a key expression, subscribers declare matching expressions, and routers forward data across sessions according to matching declarations and network topology.

The data plane supports:

- Publish/subscribe data distribution.
- Wildcard and hierarchical key-expression matching.
- Queryables and `get` request/reply.
- Distributed queries across connected infrastructure.
- Data storage and retrieval when a storage plugin/backend is configured.
- Application metadata and timestamps associated with routed data.
- Routing across multiple sessions and transports.

`zenohd` does not define a static route table in its command-line interface. Topology is established through listen/connect configuration, discovery/scouting, session declarations, and the runtime's routing tables.

### Timestamping

By default, `zenohd` adds a Hybrid Logical Clock timestamp to routed data when the data does not already contain one. The behavior can be disabled with `--no-timestamp` or the corresponding configuration setting.

This gives routed data an ordering/time context without requiring every publisher to attach its own timestamp.

## Discovery and Connectivity

Zenoh supports explicit and discovered connectivity.

### Explicit connectivity

The `--listen` option creates one or more listener endpoints. The `--connect` option supplies one or more peer locators to which the router attempts to connect. The same settings can be expressed in configuration for repeatable deployments.

### Multicast scouting

By default, `zenohd` responds to multicast scouting messages so that peers and clients can discover infrastructure. `--no-multicast-scouting` disables the default response behavior.

Scouting is separate from the data transport itself: it helps locate sessions, after which the configured transport establishes the actual connection.

### Multiple connections

The runtime supports multiple connections in client mode and multiple listen/connect locators. This allows a deployment to provide redundant or multi-homed paths, subject to the selected transport and configuration.

## Transports

The `zenohd` default feature set in [`zenohd/Cargo.toml`](https://github.com/eclipse-zenoh/zenoh/blob/main/zenohd/Cargo.toml) enables the runtime's standard transport capabilities. The reviewed entrypoint's feature test lists support for:

- TCP.
- UDP.
- TLS.
- QUIC.
- WebSocket.
- Unix socket streams.
- Serial transport.
- Transport multilink.
- Authentication features for public-key and username/password authentication.
- Optional shared memory when built with the `shared-memory` feature.

Exact availability depends on the binary's compile-time feature set and platform packaging. A deployment should verify the built binary and configuration rather than assume every transport is present in every package.

## Admin Space and Runtime Introspection

`zenohd` enables the admin space in its startup configuration. The admin space exposes runtime information and administration through Zenoh resources, subject to configured permissions.

The CLI supports:

- Read-only permissions: `r`.
- Write-only permissions: `w`.
- Read/write permissions: `rw`.
- Disabled read/write access: `none`.

Read-only is the default for the explicit permission option. The admin-space configuration can also be supplied through `--cfg`, including metadata and enablement settings.

The admin space is a Zenoh runtime facility, not a separate HTTP management server. HTTP management/data access is provided by the REST plugin described below.

## Plugin System

The plugin manager is enabled by `zenohd` at startup. Plugins can be loaded by name or by an explicit shared-library path:

```text
zenohd --plugin rest
zenohd --plugin storage_manager
zenohd --plugin rest:/path/to/libzenoh_plugin_rest.so
```

Search paths can be added with `--plugin-search-dir`. A plugin marked as required causes startup to fail if it cannot be loaded.

The plugin API is described by [`zenoh-plugin-trait`](https://github.com/eclipse-zenoh/zenoh/tree/main/plugins/zenoh-plugin-trait). The plugin system supports lifecycle operations and plugin status through the runtime/plugin manager.

### ABI and compatibility constraint

Zenoh plugins are Rust dynamic libraries. The upstream README explicitly warns that plugins must be built with the same Rust version, Zenoh version/commit, and compatible feature set as `zenohd`. Rust does not provide a stable ABI for arbitrary cross-version dynamic linking. A mismatched plugin can be rejected or can cause memory-layout incompatibilities and process failure.

## Shipped Plugins

The Zenoh repository includes two plugins delivered with `zenohd`:

### REST plugin

[`zenoh-plugin-rest`](https://github.com/eclipse-zenoh/zenoh/tree/main/plugins/zenoh-plugin-rest) exposes an HTTP REST API for interacting with Zenoh resources. `--rest-http-port` enables the plugin and configures its bind address/port; the option also marks the plugin as required.

This provides HTTP access to Zenoh data/query functionality, but it is optional and separate from the core router data plane.

### Storage manager

[`zenoh-plugin-storage-manager`](https://github.com/eclipse-zenoh/zenoh/tree/main/plugins/zenoh-plugin-storage-manager) manages storage declarations and backend plugins. It enables Zenoh to connect key expressions to persistent or in-memory storage backends and serve data through Zenoh storage/query semantics.

The storage manager is itself extensible through the storage backend API. The repository includes [`zenoh-backend-traits`](https://github.com/eclipse-zenoh/zenoh/tree/main/plugins/zenoh-backend-traits) and an example backend.

## External Plugin Ecosystem

The upstream `zenohd` documentation identifies additional plugins maintained in independent repositories:

- [`zenoh-plugin-dds`](https://github.com/eclipse-zenoh/zenoh-plugin-dds/) bridges DDS and Zenoh.
- [`zenoh-plugin-ros2dds`](https://github.com/eclipse-zenoh/zenoh-plugin-ros2dds/) bridges ROS 2 DDS communications through Zenoh.
- [`zenoh-plugin-webserver`](https://github.com/eclipse-zenoh/zenoh-plugin-webserver/) maps HTTP server URLs to Zenoh key expressions.
- The remote API/WebSocket integration in [`zenoh-ts`](https://github.com/eclipse-zenoh/zenoh-ts/) provides a WebSocket server for TypeScript clients.

These are not all part of the `zenohd` core binary. Their availability, release cadence, and licensing should be checked in their individual repositories.

## Logging and Operations

The entrypoint initializes `tracing` and `tracing-subscriber` with:

- Environment-controlled filtering.
- Thread IDs and thread names.
- Log levels.
- Log targets.

The default filter is configured for Zenoh informational output when no environment filter is supplied. Startup failures loading configuration, parsing arguments, opening the runtime, or loading required plugins terminate the process with an error.

The process remains alive by parking the main thread after the runtime is opened. Runtime workers and plugin components own the ongoing network activity.

## Security and Isolation Considerations

The implementation exposes several security-relevant controls:

- Explicit router identity, which must be unique.
- Authentication transport features such as public-key and username/password support when compiled/configured.
- TLS transport support.
- Admin-space read/write permissions.
- Required-plugin loading, preventing silent loss of required services.
- Explicit endpoint and scouting controls.

The REST API, admin-space writes, storage backends, and external bridge plugins expand the operational attack surface and should be enabled only with appropriate authentication, network binding, and authorization configuration.

## Extensibility Boundary

The primary extension boundary is the Zenoh plugin API. The core runtime provides the session, routing, transport, discovery, query, and storage integration primitives; plugins add protocol adapters, HTTP APIs, storage backends, and application-facing services.

There is no evidence in the reviewed `zenohd` entrypoint of a separate application-specific route controller, route state machine, DDS endpoint factory, or generated-type resolver. Those concepts belong to other middleware or to plugins built on Zenoh.

## Limitations and Operational Caveats

The code and upstream README imply these boundaries:

- `zenohd` is a runtime host; detailed routing behavior lives in the `zenoh` crate and is not fully visible in the small `zenohd` entrypoint.
- Plugin ABI compatibility is strict because plugins are Rust dynamic libraries.
- A router ID omitted from configuration is randomly generated; persistent identity requires explicitly managing the ID.
- Multicast scouting is enabled by default unless disabled or overridden in configuration.
- Timestamping is enabled by default unless disabled.
- REST, storage, DDS, ROS 2, and WebSocket capabilities are plugin-dependent rather than guaranteed core-router features.
- Exact transport availability depends on compile-time features and the released package.
- Admin-space write access changes the control surface and should not be exposed without suitable authorization.
- This inventory follows upstream `main`; production deployments should pin and review a specific Zenoh release/tag.

## Source Review Map

| Area | Upstream implementation/documentation |
|---|---|
| Router process entrypoint and CLI | [`zenohd/src/main.rs`](https://github.com/eclipse-zenoh/zenoh/blob/main/zenohd/src/main.rs) |
| Router package features and dependencies | [`zenohd/Cargo.toml`](https://github.com/eclipse-zenoh/zenoh/blob/main/zenohd/Cargo.toml) |
| Router CLI and plugin behavior | [`zenohd/README.md`](https://github.com/eclipse-zenoh/zenoh/blob/main/zenohd/README.md) |
| Core Zenoh runtime | [`zenoh/`](https://github.com/eclipse-zenoh/zenoh/tree/main/zenoh) |
| Plugin API and lifecycle | [`plugins/zenoh-plugin-trait/`](https://github.com/eclipse-zenoh/zenoh/tree/main/plugins/zenoh-plugin-trait) |
| REST API plugin | [`plugins/zenoh-plugin-rest/`](https://github.com/eclipse-zenoh/zenoh/tree/main/plugins/zenoh-plugin-rest) |
| Storage manager and backends | [`plugins/zenoh-plugin-storage-manager/`](https://github.com/eclipse-zenoh/zenoh/tree/main/plugins/zenoh-plugin-storage-manager) |

## Licensing

The Zenoh project uses dual licensing under the Eclipse Public License 2.0 or Apache License 2.0, as indicated by the source headers and package metadata. Individual external plugins may have their own license files and should be checked independently.
