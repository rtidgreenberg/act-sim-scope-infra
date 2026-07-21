# Spike: wis_mesh_dashboard — RouterHealth over Web Integration Service

## Question

[gui-visualization.md](../../docs/cpp_router/gui-visualization.md) proposes a browser node
graph (routers as nodes, WAN presence edges from `RouterHealth.peers_seen`) fed by RTI Web
Integration Service (WIS), installed but unused elsewhere in this repo
(`$NDDSHOME/bin/rtiwebintegrationservice`). Two things must be proven before any front-end
work starts:

1. **Type registration.** WIS's XML config only accepts DDS-XML `<types>` declarations (no
   compiled type-plugin loading — confirmed via `ask_connext_question`, 2026-07-21;
   [docs/connext-ai-issues/connext-ai-issues.md](../../docs/connext-ai-issues/connext-ai-issues.md)
   has no contradicting entry). `router/admin/RouterAdminTypes.idl` is the canonical source
   and must not be hand-transcribed into a second, drift-prone XML copy.
   `rtiddsgen -convertToXml` (verified working locally, 2026-07-21 — produces a clean
   `<dds><types>...</types></dds>` document for the full IDL, `RouterHealth` included) is
   the bridge: regenerate the XML from the IDL as a build step, splice its `<types>` block
   into the WIS config, never maintain it by hand.
2. **End-to-end visibility.** A real `router_main` mesh's `RouterHealth` heartbeats
   (published on the shared WAN domain, D75/D79) must actually show up over WIS's REST API
   (`GET /dds/rest1/applications/.../data_readers/RouterHealthReader`) and WebSocket API
   (`ws://.../dds/v1/<connection>` bound to the same DataReader resource) — endpoint shapes
   per `ask_connext_question` (2026-07-21), **unverified against the real service** until
   this spike runs it.

## Approach

Reuse the existing Phase 8 e2e fixture shape (`router/config/e2e_presence.yaml`,
`router/test_e2e/test_presence_roster.py`) rather than inventing a new mesh: two
`router_main` processes (control + platform roles) heartbeating `RouterHealth` on a shared
WAN domain is already a proven, working setup. This spike copies that YAML with **fixed**
domain ids (not the pytest harness's per-test-random ones) so a WIS config can point at a
known domain without any templating step at spike-run time.

- `config/e2e_presence_fixed_domains.yaml` — `e2e_presence.yaml` with
  `__DOMAIN_CONTROL_LAN__`/`__DOMAIN_WAN__`/`__DOMAIN_PLATFORM_LAN__` replaced by fixed,
  out-of-pytest-range ids (low thousands, per the repo's domain-id ceiling guidance).
- `config/wis_config.xml.template` — the WIS `<web_integration_service>` block (one
  `domain_participant` on the WAN domain, one `data_reader` on topic `RouterHealth`), with a
  `__ROUTER_ADMIN_TYPES__` placeholder for the spliced-in `<types>` block.
- `run_spike.sh` — the single entry point:
  1. `rtiddsgen -convertToXml` on `router/admin/RouterAdminTypes.idl` into a **local** tmp
     working dir (never the vboxsf share — runtime/generated artifacts rule).
  2. Splice its `<types>...</types>` into the WIS config template, write the finished config
     to the same local tmp dir.
  3. Start the two `router_main` processes (cwd = repo root, per `router/README.md`) against
     the fixed-domain YAML.
  4. Start `rtiwebintegrationservice -cfgFile <generated> -cfgName mesh_dashboard`, logs to
     the local tmp dir.
  5. `curl` the REST endpoint for the `RouterHealth` reader and assert at least one sample
     with a real router identity (`"<node>/<router>"`) and a non-empty `peers_seen` comes
     back; open a short-lived WebSocket bind and assert a push arrives after a heartbeat
     tick.
  6. Clean up: kill WIS, kill both router processes (`pkill -x router_main`), report
     pass/fail.

Everything transport-wise stays UDPv4-only / same-host (per the repo's DDS hygiene rule);
no SIGKILL testing here (that's the presence spike's job), so no `/dev/shm` residue is
expected, but the runner should still check for stray `RTI*` segments before/after as a
sanity habit.

## Success criteria

- WIS starts clean against the spliced config (no schema-validation failure — cross-check
  with `validate_xml_code` before the first real run).
- The REST GET returns real `RouterHealth` samples (not an empty list) from the live mesh,
  each carrying a decodable `peers_seen` sequence — i.e., cheap confirmation the whole chain
  (IDL → generated XML → WIS dynamic-data instantiation → wire deserialization → JSON) holds
  together, not just that WIS started.
- A WebSocket bind receives at least one asynchronous push within a couple of heartbeat
  periods (~2-3 s) after binding — confirms the live-update path a real node-graph page
  would use, not just the REST poll path.
- Any place the real behavior contradicts an MCP claim used above (REST path shape,
  WebSocket bind semantics, `-convertToXml` output shape) gets logged to
  `docs/connext-ai-issues/connext-ai-issues.md` per the repo rule, and this PLAN.md /
  [gui-visualization.md](../../docs/cpp_router/gui-visualization.md) get corrected to match.

## Out of scope for this spike

- Any front-end/graph-rendering code (library choice is still an open question in
  gui-visualization.md).
- `RouterLinkStats`/`RouterStatus` (LAN-only topics) — this spike is Option A only
  (mesh-wide presence via `RouterHealth`, WAN-only).
- Long-term WIS hosting/deployment questions (still open in gui-visualization.md).
