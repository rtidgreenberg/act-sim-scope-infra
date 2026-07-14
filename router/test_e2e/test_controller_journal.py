"""End-to-end Phase 6 slice 6b: the controller journal (D55/D56).

Reuses the single-router admin fixture (config/e2e_admin_commands.yaml). A Python
DynamicData reader on ActRouterControllerJournal IS "debug mode" — the router's journal
writer (IControllerJournal seam, D55) produces no data traffic until this reader matches.

Asserts evidence item E5 (D56):
  - a processed command produces a JOURNAL COMMAND_RECEIVED record carrying the decision
    (accepted), command_id, affected route, and a real pre<post state_revision bump;
  - driving discovery (a source writer appears, route builds) produces discovery /
    entities-ready records, and the route still reaches ROUTE_ENABLED with the journal
    reader attached (behavior is unchanged by observation);
  - event_sequence is strictly increasing (one record per processed event);
  - the D49 backlog signal is WIRED but not force-produced: under this normal load the
    reader keeps up, so `journal_falling_behind` must be ABSENT from the router log
    (real backpressure verification is deferred to a stress phase, D56).

Run from the repo root (see router/test_e2e/README.md).
"""

import sys
import time
from pathlib import Path

import rti.connextdds as dds

sys.path.insert(0, str(Path(__file__).resolve().parent))
from conftest import start_router, render_config  # noqa: E402
from util.dds_probe import (  # noqa: E402
    AckCollector, JournalCollector, Probe, reader_qos, writer_qos, wait_for_route)

ROUTE = "admin_r1"
NODE = "Platform_30"
ROUTER = "admincmd"
SRC_TOPIC = "AdminCmdTopic"
JOURNAL_TOPIC = "ActRouterControllerJournal"
EXAMPLE_TYPES_XML = "router/config/example_types.xml"
EX_TYPE = "ExampleCommand"

KIND = {"ENABLE_ROUTE": 0, "DISABLE_ROUTE": 1,
        "UPDATE_ROUTE": 2, "SET_PARTICIPANT_PARTITION": 3}


def _command(cmd_type, kind, route_name, command_id, target_node, target_router):
    c = dds.DynamicData(cmd_type)
    c["target_node"] = target_node
    c["target_router"] = target_router
    c["command_id"] = command_id
    c["kind"] = KIND[kind]
    c["route_name"] = route_name
    return c


def _wait_publication(probe, topic_name, check_alive, timeout_s=20.0, poll_s=0.2):
    """Block until this probe has discovered a DataWriter on topic_name (builtin
    discovery). The journal writer is VOLATILE, so a record published before this
    reader/writer pair matches would be lost — wait for the match before driving events."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not check_alive():
            raise RuntimeError("router exited before its journal writer was discovered")
        if any(p["topic"] == topic_name for p in probe.discovered_publications()):
            return True
        time.sleep(poll_s)
    return False


def test_controller_journal_records_and_no_backlog(
        router_binary, admin_types_xml, e2e_tmp_dir, unique_domains):
    config_path = render_config("e2e_admin_commands.yaml", unique_domains, e2e_tmp_dir)
    router = start_router(router_binary, config_path, "platform", e2e_tmp_dir,
                          admin_participant="wan_in")
    admin_domain = unique_domains["control_lan"]

    provider = dds.QosProvider(str(admin_types_xml))
    cmd_type = provider.type("RouterCommand")
    ack_type = provider.type("RouterCommandAck")
    journal_type = provider.type("ControllerJournalRecord")
    status_type = provider.type("RouterStatus")
    ex_type = dds.QosProvider(EXAMPLE_TYPES_XML).type(EX_TYPE)

    probe = Probe(admin_domain)
    alive = lambda: router.is_alive()  # noqa: E731
    try:
        journal_reader = probe.reader(
            JOURNAL_TOPIC, "ControllerJournalRecord",
            qos=reader_qos(reliability="reliable"), dtype=journal_type)
        journal = JournalCollector(journal_reader)
        status_reader = probe.reader(
            "ActRouterStatus", "RouterStatus",
            qos=reader_qos(reliability="reliable", durability="transient_local"),
            dtype=status_type)
        cmd_writer = probe.writer(
            "ActRouterCommand", "RouterCommand",
            qos=writer_qos(reliability="reliable", durability="volatile"), dtype=cmd_type)
        ack_reader = probe.reader(
            "ActRouterCommandAck", "RouterCommandAck",
            qos=reader_qos(reliability="reliable", durability="volatile"), dtype=ack_type)
        acks = AckCollector(ack_reader)

        # The journal is VOLATILE — match the writer before driving events, else the first
        # records are published to no-one and lost.
        assert _wait_publication(probe, JOURNAL_TOPIC, alive), \
            f"journal writer {JOURNAL_TOPIC} never discovered; log {router.log_path}"
        time.sleep(0.5)  # let the reliable reader/writer handshake complete

        # (E5a) ENABLE_ROUTE -> a COMMAND_RECEIVED journal record with the decision + bump.
        cmd_writer.write(_command(cmd_type, "ENABLE_ROUTE", ROUTE, "enable-1", NODE, ROUTER))
        ack = acks.wait("enable-1", check_alive=alive)
        assert ack is not None and ack["accepted"], \
            f"ENABLE_ROUTE not accepted: {ack}; log {router.log_path}"

        cmd_recs = journal.wait_for(
            lambda r: r["event_kind"] == "COMMAND_RECEIVED" and r["command_id"] == "enable-1",
            check_alive=alive)
        assert cmd_recs, \
            f"no COMMAND_RECEIVED journal record for enable-1; log {router.log_path}"
        rec = cmd_recs[-1]
        assert rec["route"] == ROUTE, f"journal route {rec['route']!r}; log {router.log_path}"
        assert rec["decision"] == "accepted", \
            f"journal decision {rec['decision']!r}, expected accepted; log {router.log_path}"
        assert rec["state_changed"] is True, \
            f"COMMAND_RECEIVED not marked state_changed; {rec}; log {router.log_path}"
        assert rec["post"] > rec["pre"], \
            f"journal pre/post revision not a bump ({rec['pre']}->{rec['post']}); " \
            f"log {router.log_path}"

        # (E5b) source writer appears -> route builds -> discovery/entities-ready records;
        # the route still reaches ENABLED (behavior unchanged with the journal reader on).
        src_writer = probe.writer(SRC_TOPIC, EX_TYPE, dtype=ex_type)  # noqa: F841
        enabled = wait_for_route(
            status_reader, ROUTE,
            lambda f: f["state"] == "ROUTE_ENABLED", check_alive=alive)
        assert enabled is not None and enabled["state"] == "ROUTE_ENABLED", \
            f"route never reached ROUTE_ENABLED with journal attached; got {enabled}; " \
            f"log {router.log_path}"

        disc_recs = journal.wait_for(
            lambda r: r["event_kind"] in ("PUBLICATION_DISCOVERED", "TOPIC_ENTITIES_READY"),
            check_alive=alive)
        assert disc_recs, \
            f"no discovery/entities-ready journal records while the route built; " \
            f"log {router.log_path}"

        # One record per processed event: event_sequence strictly increasing in arrival
        # order (the journal writer runs on the single controller strand).
        seqs = [r["event_sequence"] for r in journal.records]
        assert seqs == sorted(seqs) and len(seqs) == len(set(seqs)), \
            f"journal event_sequence not strictly increasing: {seqs}; log {router.log_path}"

        # (E5c) D49 backlog signal wired but not forced: a keeping-up reader never trips it.
        assert "journal_falling_behind" not in router.log_path.read_text(), \
            f"journal_falling_behind logged under normal load; log {router.log_path}"
    finally:
        probe.close()
        router.stop()
