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

## C++ call-surface compile check (D66)

`cpp_compile_check.cxx` confirms by compile (never linked or run) the Modern C++ surface
the D64/D66 refactor uses: `matched_publications`/`matched_subscriptions`, the matched
statuses, `SUBSCRIPTION_MATCHED`/`PUBLICATION_MATCHED` StatusConditions on the
`AsyncWaitSet`, `matched_publication_data`, and the wire-type read
(`PublicationBuiltinTopicData->type()` → `DynamicData` entities). Verified clean
2026-07-15 with the router's own compile flags:

```bash
c++ -std=gnu++11 -DRTI_64BIT -DRTI_LINUX -DRTI_STATIC -DRTI_UNIX \
    -isystem $NDDSHOME/include -isystem $NDDSHOME/include/ndds \
    -isystem $NDDSHOME/include/ndds/hpp \
    -fsyntax-only spikes/matched_endpoints/cpp_compile_check.cxx
```

## Requirements

`rti.connextdds` (Connext 7.7) and `harness/act/node_sim/datamodel/act_types.xml`.
