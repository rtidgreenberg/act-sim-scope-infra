# Remaining Work: Debug Tooling + Missing E2E Tests

## 1. Type Propagation Debug Probe

### Problem this solves

During team-control-topic debugging, the `control_event` route was stuck in `TOPIC_IDLE`
with `input_matched=0`. Root cause: the WIS writer did not propagate inline TypeObjects
(an RTI WIS limitation), so the router's `DiscoveryDispatcher.maybe_learn_type()` never
fired `TypeResolved` for `ActTeamAssignment`. Diagnosing this required manually grepping
logs, cross-referencing GUIDs, and noticing the `type_not_inline` warning was deduplicated
per topic (so the WIS publication's missing TypeObject was silently swallowed).

### What to build

A standalone Python diagnostic script: `harness_v2/scripts/dds_type_probe.py`

**Usage:** `python3 dds_type_probe.py --domain 20 [--topic ActTeamAssignment] [--wait 5]`

**Output for each discovered endpoint on that domain:**
- Direction: publication or subscription
- Topic name, type name
- Participant GUID prefix (first 24 hex chars of instance handle)
- Participant name (EntityName.name, if set — identifies router participants)
- **Has inline TypeObject: yes/no** ← the key diagnostic

**Implementation notes:**
- Use `rti.connextdds` Python binding, UDPv4-only participant
- Read builtin `DCPSPublication` and `DCPSSubscription` data
- For each endpoint: `data.topic_name`, `data.type_name`, `data.participant_key`
- **TypeObject check:** `data.type()` returns `Optional<DynamicType>` — check `.is_set()`
  to determine if inline TypeObject is present (this is the exact check
  `DiscoveryDispatcher.maybe_learn_type()` does via `type.is_set()`)
- Optional `--topic` filter to show only endpoints for a specific topic
- Print a summary table, then exit

**Example output:**
```
Domain 20 — discovered endpoints (5s wait):

  PUB  ActTeamAssignment     TeamAssignment     010107de:7667c5be:e1127b2d  MeshDashboardApp  TypeObject: NO
  PUB  ActRouterMeshStatus   RouterMeshStatus   0101e09d:57f76784:75670aaf  Control_20/...    TypeObject: YES
  SUB  ActTeamAssignment     TeamAssignment     01018416:af1abcd6:307ad6ef  Platform_30/...   TypeObject: NO

⚠ ActTeamAssignment: 0 of 1 publications carry inline TypeObject
  → Router routes waiting on this type will stay TOPIC_IDLE
```

**Where to put it:** `harness_v2/scripts/dds_type_probe.py` (not test_e2e/util — this is a
standalone debugging tool, not a test utility)

**Also add to copilot-instructions.md** under the "Test harnesses" section as a fourth
bullet: "Type probe" with usage.

---

## 2. Missing E2E Tests

### 2a. `control_event` route test

**Gap:** No pytest-based e2e test exercises the `control_event` route (ActTeamAssignment,
unfiltered control→platform broadcast). The existing `test_team_assignment_e2e.py` is a
standalone argparse script using WIS, not a pytest test.

**Config:** `router/config/e2e_control_event.yaml` already exists (created this session).

**Test file:** `router/test_e2e/test_control_event_route.py`

**Pattern:** Same as `test_control_command_route.py` but:
- Uses the `admin_types_xml` fixture (TeamAssignment is in ActTypes.idl, not example_types)
- Type name: `TeamAssignment`, topic name: `ActTeamAssignment`
- Writer on `control_lan` domain, reader on `platform_lan` domain
- NO content filter — verify ALL samples arrive (unlike control_command which filters)
- Write two samples with different `platform_node` values; assert BOTH arrive at the
  platform reader (proves the route is unfiltered)
- Both WAN legs use CONTROL partition (publisher_partition/subscriber_partition), but the
  app probes are on the LAN legs which have no partition — this matches the production
  data flow

**Key code structure:**
```python
def test_control_event_broadcasts_to_all_platforms(router_pair, unique_domains, admin_types_xml):
    control_proc, platform_proc, _ = router_pair("e2e_control_event.yaml", unique_domains)

    provider = dds.QosProvider(str(admin_types_xml))
    ta_type = provider.type("TeamAssignment")

    control_app = Probe(unique_domains["control_lan"])
    platform_app = Probe(unique_domains["platform_lan"])
    try:
        writer = control_app.writer("ActTeamAssignment", "TeamAssignment", dtype=ta_type)
        reader = platform_app.reader("ActTeamAssignment", "TeamAssignment", dtype=ta_type)

        def write_both():
            s1 = dds.DynamicData(ta_type)
            s1["platform_node"] = "Platform_30"
            s1["team_name"] = "Alpha"
            writer.write(s1)
            s2 = dds.DynamicData(ta_type)
            s2["platform_node"] = "Platform_31"
            s2["team_name"] = "Bravo"
            writer.write(s2)

        buckets = write_until_seen(
            write_both, reader,
            classify=lambda d: d["platform_node"], stop_key="Platform_30",
            check_alive=lambda: control_proc.is_alive() and platform_proc.is_alive())

        # Both arrive (unfiltered broadcast — unlike control_command's CFT)
        assert buckets.get("Platform_30"), "Platform_30 sample never arrived"
        assert buckets.get("Platform_31"), "Platform_31 sample never arrived (route should be unfiltered)"
    finally:
        control_app.close()
        platform_app.close()
```

### 2b. `UPDATE_ROUTE` command rejection test

**Gap:** No test exercises `UPDATE_ROUTE` (RouterCommandKind ordinal 2).

**Finding:** `UPDATE_ROUTE` is explicitly unsupported — the router returns
`accepted=false` with message "UPDATE_ROUTE unsupported in this build"
(see `RouterController.cxx` line ~336).

**Test file:** `router/test_e2e/test_router_admin_commands.py` — add a new test function
(or add it to the existing `test_admin_command_control_loop` test, after E3).

**What to assert:**
```python
# Send UPDATE_ROUTE command
cmd = _command(cmd_type, "UPDATE_ROUTE", ROUTE, "update-1", NODE, ROUTER)
cmd_writer.write(cmd)
ack = acks.wait("update-1", check_alive=alive)
assert ack is not None, "no ack for UPDATE_ROUTE"
assert not ack["accepted"], "UPDATE_ROUTE should be rejected"
assert "unsupported" in ack["message"].lower()

# Verify state_revision did NOT bump (rejected command should not change state)
rev_after = read_status_revision(status_reader)
assert rev_after == rev_before, "rejected UPDATE_ROUTE should not bump state_revision"
```

**Alternative:** If adding to the existing test is too interleaved, create a small
standalone test function `test_update_route_rejected` in the same file, using the same
config and setup pattern.
