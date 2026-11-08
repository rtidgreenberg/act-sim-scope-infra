# Pre-two-topic router refactor baseline

Date: 2026-11-08

Baseline tag: `pre-two-topic-refactor-2026-11-08` (local, not pushed).

**No executed test failed. Browser/live-dashboard coverage remains incomplete.**
Production routing still uses the legacy WAN contract. The two-topic migration
is a [draft plan](two-topic-wan-migration-plan.md) plus isolated qualification and
adapter code, not a deployed router change.

## Checkpoint commits

| Commit | Contents |
|---|---|
| `4549e87` | Separate communication documentation, branded slide sources/assets, renderer, and rebuilt PDFs |
| `86576aa` | Two-topic migration plan, middleware comparisons, workload contract, and requested plain-language preference |
| `fc44d6b` | Connext union qualification and whole-structure DynamicData adapter spike |

This baseline document is committed after those checkpoints; the annotated tag
identifies the complete checkpoint including this record. The shared Connext
correction-log submodule is pinned at
`b5d13608b839e55503155ab3792f98264c083f8e`.

The standalone internal brand-guide PDF is deliberately untracked. No commits
or tags from this checkpoint were pushed.

## Verified environment

- Local image: `connext:7.7.0`,
  `sha256:cbabafb1dab80c66483f092e1770249a6196d974b01592fe3a52979613f5f7a0`.
- SDK: Connext 7.7.0, architecture `x64Linux4gcc8.5.0`.
- GNU C++ 11.4.0; CMake/CTest 3.22.1; importable `rti.connextdds`.
- Pytest 9.2.0 installed offline in disposable containers after the image's
  missing pytest dependency was detected.
- Clean cached yaml-cpp 0.8.0 source, supplied through
  `FETCHCONTENT_SOURCE_DIR_YAML-CPP`; no production dependency changes.
- Fresh router build from the current checkout, mounted at `/workspace`, rather
  than using the old host binaries. Writable build, C++ temporary-test paths,
  Python state, logs, and captures were placed in owned disposable `/tmp` storage.
- Final router DDS run used host networking and the suite's reserved domains.
  Union qualification/adapter runners used their private UDPv4 containers.

## Results

| Check | Result |
|---|---|
| Fresh router configure/build | Passed |
| C++ router tests | **5/5 passed**, zero failures; 0.06 seconds |
| Python router suite | **237 passed, 23 skipped**, plus **155 passed subtests**; 300.11 seconds |
| Union SDK qualification | **9/9 passed**, no skips/errors/failures; 36.33 seconds |
| Whole-structure adapter | **9 PASS groups**, 10 expected rejection cases, no failures |
| Slide renderer | All three decks rebuilt; 10, 10, and 6 pages passed text-bounds checks |
| Static checkpoint checks | Python syntax, SVG XML, Bash syntax, local documentation links, and whitespace passed |
| Teardown | No remaining mesh/router/test processes, owned containers, or RTI/dds shared-memory artifacts |

Pytest collected 260 top-level test items. Its JUnit report contains 415 entries
because it also records the 155 successful subtests; this is not 415 independent
top-level tests.

The router suite also passed an initial private-loopback run with the same
237/23/155 outcome before the final host-network confirmation.

### Explicit coverage gaps

- **18 skips:** Playwright was unavailable in the test image.
- **5 skips:** live mission browser tests require `MISSION_DASHBOARD_URL`.
- No live EMANE deployment or live GUI gate was launched for this checkpoint.
- PDF text-bounds checks do not establish visual readability or UI correctness.
- Existing tests passing does not resolve the migration's health XML/code timing
  discrepancy or qualify future shared-writer load, restart, and lifecycle behavior.

These gaps must not be reported as a completely exercised fleet/browser gate.

## Retained evidence and rerun commands

Compact local evidence, intentionally excluded from Git:

- [C++ summary](../debug/test_reports/two_topic_refactor_baseline_cpp.txt).
- [Router JUnit](../debug/test_reports/two_topic_refactor_baseline_router.xml).
- [Union qualification JUnit](../debug/test_reports/two_topic_union_qualification.xml).
- [Adapter summary](../debug/test_reports/two_topic_union_adapter.txt).

Raw build/test logs, generated code, captures, dependency wheels, and temporary
builds are discarded after verification. The committed counts and Git tag remain
the durable baseline even if local reports are later cleared or overwritten.

Inside the verified Connext container, with the checkout at `/workspace`, separate
writable build/temp/log overlays, and yaml-cpp 0.8.0 at `/deps/yaml`:

```bash
cmake -S router -B router/build \
  -DCONNEXTDDS_ARCH=x64Linux4gcc8.5.0 \
  -DFETCHCONTENT_SOURCE_DIR_YAML-CPP=/deps/yaml
cmake --build router/build -j2
bash router/run_tests.sh
python3 -m pytest -q -p no:cacheprovider router/test_e2e
```

The independent owned qualification runners:

```bash
bash router/qualification/run_checks.sh
bash spikes/two_topic_union_adapter/run.sh
python3 docs/slides/render.py
```

Before another DDS run, repeat the process/container/shared-memory checks. Use
the owned lifecycles and reserved domains, and do not run against a live mesh.

## Refactor boundary

Preserve existing LAN interfaces and whole payload structures. The router will
stamp team-scoped envelope metadata from applied internal team-control state and
update receive CFT interests. Periodic status uses eventual convergence rather
than a fleet-wide atomic barrier; required events retain their separate policy.
Field mapping and operation-specific command redesign remain deferred.

Use this tag to compare the implementation against the legacy behavior. Retain
matching binary/schema/config/image sets for operational rollback; restoring a
Git revision alone does not reconcile queued or already executed commands.
