# type_discovery spike

Proves a router-like participant with **no local type library** can learn a **COMPLETE**
`DynamicType` purely from builtin discovery — from both a discovered writer and a discovered
reader — and use it for named `DynamicData` access and content filtering. See
[PLAN.md](PLAN.md) for the question, method, result, and design implications.

## Run

From the repo root (so `harness/act/node_sim/datamodel/act_types.xml` resolves):

```bash
python3 spikes/type_discovery/type_discovery_spike.py [base_domain_id]
```

- `base_domain_id` defaults to `91`; Part A uses it, Part B uses `base+1` (each part runs
  fresh participants on its own domain).
- Exit `0` = both parts passed; nonzero = a failed assertion (the failure names which part
  and, on a type-resolution failure, whether the endpoint was discovered at all).
- UDPv4-only; leaves no `/dev/shm` segments.

## Requirements

`rti.connextdds` (Python) against Connext 7.7 — the same binding the ACT sim uses. The
script sets `NDDSHOME`/`RTI_LICENSE_FILE` defaults matching `router/test_e2e/conftest.py`.

## Files

- `type_discovery_spike.py` — the runner. Part A: learn from a writer; Part B: learn from a
  reader; Part C: prove the type is INLINE in SEDP by disabling the TypeLookup channel and
  confirming it still resolves. No QoS XML: `request_types_filter` is not needed (small types
  propagate the COMPLETE TypeObject inline) and is C++-only on this install anyway (see
  PLAN.md, and `docs/connext-ai-issues/connext-ai-issues.md` for the MCP claims the build
  disproved).
