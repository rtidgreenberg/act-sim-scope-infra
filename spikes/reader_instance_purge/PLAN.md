# Reader instance purge verification

## Question

Can Connext Professional 7.7.0 retain reader keys for a finite nonzero interval
after NO_WRITERS or DISPOSED, allowing a router to translate and relay the
notification without a duplicate key registry?

## Method

1. Load fixed QoS from XML; test default, zero, 2 seconds, 5 seconds, and infinite
   instance delays. Include finite sample-delay acceptance as a separate control.
2. Record whether failure occurs at provider loading, profile extraction, or
   enabled reader construction; do not mistake XML parsing for runtime support.
3. Use real Modern C++ DynamicData readers/writers on two independent UDPv4 legs.
4. Compare immediate `take()` with `read()`/key extraction followed by `take()`.
5. Query the native key registry during the take loan, after loan destruction,
   and at 100, 1000, 2500, and 5500 ms after take. Add zero-delay cases that hold
   the loan for 2100 ms and check the key before returning it.
6. When the registry supplies the key during input processing, use the existing
   destination writer's native lookup and dispose/unregister operation. Verify
   the destination's native instance state, then valid-data revival.
7. Retain structured outcomes, errors, timings, and binary/QoS hashes.

## Safety and scope

Run in one disposable `--network none` container with private IPC, UDPv4 only,
domains 210/211, read-only source/license mounts, and `/tmp` build state.
The host mesh remains untouched. No production QoS or adapter changes.
The runner owns and cleans its uniquely named container.

Acceptance means controls work and finite values are either explicitly rejected
or exhibit the requested measured retention interval. Rejection is an answer,
not a skipped test. This spike does not qualify pressure, concurrent consumers,
crash recovery, or every first-observed invalid-instance scenario.
