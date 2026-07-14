# matched_endpoints spike

Validates the D64 create-and-observe matching authority: DDS's own
`matched_publications()` / `matched_subscriptions()` on the created route entities are the
connectivity truth, so a partition mismatch yields zero matches (the D61 false-green is
dissolved) and a created-but-unmatched entity is an observable zero. See [PLAN.md](PLAN.md).

## Run

From the repo root:

```bash
python3 spikes/matched_endpoints/matched_endpoints_spike.py [base_domain_id]
```

- `base_domain_id` defaults to `61`; each of the three parts uses its own domain (`base+0/1/2`).
- Exit `0` = all parts pass; nonzero = a failed assertion.
- UDPv4-only; leaves no `/dev/shm` segments.

## What each part proves

- **A** — `matched_publications`/`matched_subscriptions` reflect a real match, both directions.
- **B** — a CFT route reader on partition `PLATFORM` never matches a `CONTROL` writer (held
  zero) but does match a `PLATFORM` writer (partition + CFT together; the false-green claim).
- **C** — created-but-unmatched reports zero, then observes the peer when it appears.

## Requirements

`rti.connextdds` (Connext 7.7) and `harness/act/node_sim/datamodel/act_types.xml`.
