# qos_alias spike

Validates Phase 7a (D60): resolving the named QoS aliases in `control-platform.yaml` from the
**real** production `qos_libraries` and applying them to entities/participants. See
[PLAN.md](PLAN.md) for the question, result, and the three concrete findings (env-var
requirement, the `WAN_TIMEOUT_SEC > 30` liveliness constraint, and the broken
`lan_status_1hz` alias).

## Run

From the repo root:

```bash
python3 spikes/qos_alias/qos_alias_spike.py [base_domain_id]
```

- `base_domain_id` defaults to `71`; parts 3 and 4 use `base+1` / `base+2`.
- Exit `0` = the 7a mechanism works; nonzero = a mechanism failure. Detected config defects
  (e.g. the broken `lan_status_1hz` alias) are reported but do not fail the run.
- UDPv4-only; leaves no `/dev/shm` segments.

The script sets the 14 QoS-lib env vars to loopback/test values so it is self-contained
(`WAN_TIMEOUT_SEC=100`, > the hardcoded 30 s assert period — see PLAN.md). A real deployment
supplies these via the ACT harness.

## Requirements

`rti.connextdds` (Connext 7.7) and the ACT QoS libs at `harness/act/config/qos/` +
`relay/qos_isc.xml` (present in-repo / via the `harness/act` submodule).
