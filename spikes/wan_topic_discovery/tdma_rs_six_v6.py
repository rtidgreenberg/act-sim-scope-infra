"""Drive and verify real six_topic_union Routing Service gateways over IPv6 EMANE TDMA."""

import argparse
import json
import time
from pathlib import Path

import yaml


def build_config(out_path: Path, role: str) -> None:
    base = yaml.safe_load(Path("/workspace/router/config/control-platform.yaml").read_text())
    base["router"]["wan_contract"] = "six_topic_union"
    base["router"]["admin_participant"] = "control_lan" if role == "control" else "platform_lan"
    base["types"]["xml"] = "/workspace/harness_v2/datamodel/gen/ActTypes.xml"
    base["qos_libraries"] = ["/workspace/harness_v2/qos/act_qos_profiles.xml"]
    base["qos_profiles"].update(
        {
            "wan_participant": "ACT_QOS_LIB::rs_six_topic_participant_emane_v6",
            "wan_event": "ACT_QOS_LIB::wan_three_topic_event",
            "wan_contacts": "ACT_QOS_LIB::wan_union_contacts",
            "wan_periodic": "ACT_QOS_LIB::wan_union_periodic",
        }
    )
    base["routing_service"] = {
        "receive_policy": "static",
        "topic_priorities": {"PlatformDetailStatus": "low"},
    }
    out_path.write_text(yaml.safe_dump(base))


def run_tx(count: int) -> None:
    import rti.connextdds as dds

    provider = dds.QosProvider(
        dds.QosProviderParams(
            url_profile=[
                "/workspace/harness_v2/qos/act_qos_profiles.xml",
                "/workspace/harness_v2/datamodel/gen/ActTypes.xml",
            ]
        )
    )
    with dds.DomainParticipant(20, provider.participant_qos_from_profile("ACT_QOS_LIB::lan_control_router_participant")) as p:
        mission_t = dds.DynamicData.Topic(p, "Mission", provider.type("control_command"))
        w_mission = dds.DynamicData.DataWriter(
            p.implicit_publisher, mission_t, provider.datawriter_qos_from_profile("ACT_QOS_LIB::lan_event")
        )
        deadline = time.monotonic() + 20
        while len(w_mission.matched_subscriptions) < 1:
            if time.monotonic() > deadline:
                raise RuntimeError("control LAN Mission writer discovery timed out")
            time.sleep(0.05)
        time.sleep(3.0)
        for seq in range(count):
            s = dds.DynamicData(provider.type("control_command"))
            s["source"] = "Control_20"
            s["destination"] = "Platform_30"
            s["command_type"] = 1
            s["audit_sequence"] = seq + 1
            s["sent_at_ns"] = time.monotonic_ns()
            w_mission.write(s)
            time.sleep(0.02)
        time.sleep(2.0)


def run_platform_tx(count: int) -> None:
    import rti.connextdds as dds

    provider = dds.QosProvider(
        dds.QosProviderParams(
            url_profile=[
                "/workspace/harness_v2/qos/act_qos_profiles.xml",
                "/workspace/harness_v2/datamodel/gen/ActTypes.xml",
            ]
        )
    )
    with dds.DomainParticipant(30, provider.participant_qos_from_profile("ACT_QOS_LIB::lan_router_participant")) as p:
        t_ack = dds.DynamicData.Topic(p, "PlatformCommandAck", provider.type("control_command_ack"))
        t_contact = dds.DynamicData.Topic(p, "ContactReport", provider.type("contact_report"))
        t_status = dds.DynamicData.Topic(p, "PlatformStatus", provider.type("platform_init_status"))
        t_detail = dds.DynamicData.Topic(p, "PlatformDetailStatus", provider.type("platform_detail_status"))
        w_ack = dds.DynamicData.DataWriter(
            p.implicit_publisher, t_ack, provider.datawriter_qos_from_profile("ACT_QOS_LIB::lan_event")
        )
        w_contact = dds.DynamicData.DataWriter(
            p.implicit_publisher, t_contact, provider.datawriter_qos_from_profile("ACT_QOS_LIB::lan_event")
        )
        w_status = dds.DynamicData.DataWriter(
            p.implicit_publisher, t_status, provider.datawriter_qos_from_profile("ACT_QOS_LIB::lan_status")
        )
        w_detail = dds.DynamicData.DataWriter(
            p.implicit_publisher, t_detail, provider.datawriter_qos_from_profile("ACT_QOS_LIB::lan_status")
        )
        writers = (w_ack, w_contact, w_status, w_detail)
        deadline = time.monotonic() + 20
        while not all(len(w.matched_subscriptions) >= 1 for w in writers):
            if time.monotonic() > deadline:
                raise RuntimeError("platform LAN writer discovery timed out")
            time.sleep(0.05)
        # Warmup so ON_DOMAIN_MATCH completes before the unpaced burst.
        for _ in range(15):
            sa = dds.DynamicData(provider.type("control_command_ack"))
            sa["source"] = "Platform_30"
            sa["destination"] = "Control_20"
            sa["command_id"] = "warmup"
            sa["audit_sequence"] = 0
            w_ack.write(sa)

            sd = dds.DynamicData(provider.type("platform_detail_status"))
            sd["source"] = "Platform_30"
            sd["audit_sequence"] = 0
            w_detail.write(sd)

            ss = dds.DynamicData(provider.type("platform_init_status"))
            ss["source"] = "Platform_30"
            ss["audit_sequence"] = 0
            w_status.write(ss)

            sc = dds.DynamicData(provider.type("contact_report"))
            sc["source"] = "Platform_30"
            sc["contact_id"] = "warmup"
            sc["audit_sequence"] = 0
            w_contact.write(sc)
            time.sleep(0.2)

        # Unpaced burst: lowest priority (P3) first in each round.
        for seq in range(1, count + 1):
            sd = dds.DynamicData(provider.type("platform_detail_status"))
            sd["source"] = "Platform_30"
            sd["run_id"] = "burst"
            sd["source_node"] = "Platform_30"
            sd["audit_sequence"] = seq
            sd["sent_at_ns"] = time.monotonic_ns()
            w_detail.write(sd)

            ss = dds.DynamicData(provider.type("platform_init_status"))
            ss["source"] = "Platform_30"
            ss["run_id"] = "burst"
            ss["source_node"] = "Platform_30"
            ss["mission_id"] = "X" * 60
            ss["audit_sequence"] = seq
            ss["sent_at_ns"] = time.monotonic_ns()
            w_status.write(ss)

            sc = dds.DynamicData(provider.type("contact_report"))
            sc["source"] = "Platform_30"
            sc["run_id"] = "burst"
            sc["source_node"] = "Platform_30"
            sc["contact_id"] = f"c-{seq}"
            sc["audit_sequence"] = seq
            sc["sent_at_ns"] = time.monotonic_ns()
            w_contact.write(sc)

            sa = dds.DynamicData(provider.type("control_command_ack"))
            sa["source"] = "Platform_30"
            sa["destination"] = "Control_20"
            sa["run_id"] = "burst"
            sa["source_node"] = "Platform_30"
            sa["command_id"] = f"cmd-{seq}"
            sa["message"] = "M" * 120
            sa["audit_sequence"] = seq
            sa["sent_at_ns"] = time.monotonic_ns()
            w_ack.write(sa)
            time.sleep(0.005)
        time.sleep(4.0)


def run_rx(count: int) -> None:
    import rti.connextdds as dds

    provider = dds.QosProvider(
        dds.QosProviderParams(
            url_profile=[
                "/workspace/harness_v2/qos/act_qos_profiles.xml",
                "/workspace/harness_v2/datamodel/gen/ActTypes.xml",
            ]
        )
    )
    with dds.DomainParticipant(20, provider.participant_qos_from_profile("ACT_QOS_LIB::lan_control_router_participant")) as p:
        t_ack = dds.DynamicData.Topic(p, "PlatformCommandAck", provider.type("control_command_ack"))
        t_contact = dds.DynamicData.Topic(p, "ContactReport", provider.type("contact_report"))
        t_status = dds.DynamicData.Topic(p, "PlatformStatus", provider.type("platform_init_status"))
        t_detail = dds.DynamicData.Topic(p, "PlatformDetailStatus", provider.type("platform_detail_status"))
        t_mesh = dds.DynamicData.Topic(p, "ActRouterMeshStatus", provider.type("RouterMeshStatus"))
        r_ack = dds.DynamicData.DataReader(
            p.implicit_subscriber, t_ack, provider.datareader_qos_from_profile("ACT_QOS_LIB::lan_event")
        )
        r_contact = dds.DynamicData.DataReader(
            p.implicit_subscriber, t_contact, provider.datareader_qos_from_profile("ACT_QOS_LIB::lan_event")
        )
        r_status = dds.DynamicData.DataReader(
            p.implicit_subscriber, t_status, provider.datareader_qos_from_profile("ACT_QOS_LIB::lan_status")
        )
        r_detail = dds.DynamicData.DataReader(
            p.implicit_subscriber, t_detail, provider.datareader_qos_from_profile("ACT_QOS_LIB::lan_status")
        )
        r_mesh = dds.DynamicData.DataReader(
            p.implicit_subscriber, t_mesh, provider.datareader_qos_from_profile("ACT_QOS_LIB::router_mesh_status")
        )
        readers = {
            "PlatformCommandAck": r_ack,
            "ContactReport": r_contact,
            "PlatformStatus": r_status,
            "PlatformDetailStatus": r_detail,
        }
        deadline = time.monotonic() + 20
        while not all(len(r.matched_publications) >= 1 for r in readers.values()):
            if time.monotonic() > deadline:
                raise RuntimeError("control LAN reader discovery timed out")
            time.sleep(0.05)
        print("Ready", flush=True)
        arrivals = []
        per_topic = {k: [] for k in readers}
        seen = {k: set() for k in readers}
        mesh_samples = 0
        latest_peer_presence = None
        end_at = time.monotonic() + 18.0
        last_rx = time.monotonic()
        while time.monotonic() < end_at:
            got = False
            for s in r_mesh.take():
                if s.info.valid:
                    mesh_samples += 1
                    for peer in s.data["peers"]:
                        if peer["node_name"] == "Platform_30":
                            latest_peer_presence = str(peer["presence"])
            for name, r in readers.items():
                for s in r.take():
                    if s.info.valid:
                        seq = int(s.data["audit_sequence"])
                        if seq <= 0 or seq in seen[name]:
                            continue
                        seen[name].add(seq)
                        now_ns = time.monotonic_ns()
                        lat_ms = (now_ns - int(s.data["sent_at_ns"])) / 1e6
                        idx = len(arrivals)
                        arrivals.append((idx, name, seq, lat_ms))
                        per_topic[name].append((idx, lat_ms))
                        got = True
            if got:
                last_rx = time.monotonic()
                if all(len(v) >= count for v in per_topic.values()):
                    break
            elif len(arrivals) > 0 and (time.monotonic() - last_rx) > 3.5:
                break
            else:
                time.sleep(0.005)
        import statistics

        half = max(1, len(arrivals) // 2)
        summary = {}
        for name, entries in per_topic.items():
            lats = sorted(lat for _, lat in entries)
            summary[name] = {
                "received": len(entries),
                "sent": count,
                "first_half": sum(1 for idx, _ in entries if idx < half),
                "min_ms": round(lats[0], 1) if lats else None,
                "median_ms": round(statistics.median(lats), 1) if lats else None,
                "p95_ms": round(lats[int(0.95 * (len(lats) - 1))], 1) if lats else None,
                "max_ms": round(lats[-1], 1) if lats else None,
            }
        summary["RouterHealth_via_MeshStatus"] = {
            "mesh_status_samples": mesh_samples,
            "platform_30_presence": latest_peer_presence,
        }
        print(json.dumps(summary), flush=True)


def parse_queues(table: str) -> dict:
    parsed = {}
    for line in table.splitlines():
        fields = [part.strip() for part in line.split("|")[1:-1]]
        if fields and fields[0].isdigit():
            parsed[int(fields[0])] = tuple(map(int, fields[1:4]))
    return parsed


def parse_marks(path: Path) -> dict:
    counts = {}
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line:
            continue
        dscp = int(line.split("\t")[0])
        counts[dscp] = counts.get(dscp, 0) + 1
    return dict(sorted(counts.items()))


def run_report(work: Path) -> None:
    rx_lines = [line for line in (work / "rx.log").read_text().splitlines() if line.startswith("{")]
    if not rx_lines:
        raise RuntimeError(f"Missing rx JSON summary in {work / 'rx.log'}")
    received = json.loads(rx_lines[-1])
    before = parse_queues((work / "queues-before.txt").read_text())
    after = parse_queues((work / "queues.txt").read_text())
    delta = {str(q): [b - a for a, b in zip(before[q], after[q])] for q in sorted(after)}
    tx_marks = parse_marks(work / "tx.marks")
    rx_marks = parse_marks(work / "rx.marks")
    for dscp in (48, 34, 26):
        if tx_marks.get(dscp, 0) == 0 or rx_marks.get(dscp, 0) == 0:
            raise RuntimeError(f"Expected IPv6 DSCP {dscp} on both ends: tx={tx_marks}, rx={rx_marks}")
    if delta["3"][0] == 0 or delta["2"][0] == 0 or delta["0"][0] == 0:
        raise RuntimeError(f"Expected Queue 3, Queue 2, and Queue 0 activity: delta={delta}")
    if received.get("ContactReport", {}).get("received", 0) == 0:
        raise RuntimeError(f"Missing end-to-end Routing Service deliveries: {received}")
    print(
        json.dumps(
            {
                "wan_contract": "six_topic_union",
                "transport": "UDPv6",
                "received_on_control_lan": received,
                "tx_ipv6_dscp_packets": tx_marks,
                "rx_ipv6_dscp_packets": rx_marks,
                "queue_delta_enqueued_dequeued_overflow": delta,
            },
            indent=2,
        ),
        flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("config")
    c.add_argument("--out", required=True)
    c.add_argument("--role", choices=("control", "platform"), required=True)
    for name in ("tx_ctrl", "tx_plat", "rx_ctrl"):
        p = sub.add_parser(name)
        p.add_argument("--count", type=int, default=20)
    r = sub.add_parser("report")
    r.add_argument("--work", required=True)
    args = parser.parse_args()
    if args.cmd == "config":
        build_config(Path(args.out), args.role)
    elif args.cmd == "tx_ctrl":
        run_tx(args.count)
    elif args.cmd == "tx_plat":
        run_platform_tx(args.count)
    elif args.cmd == "rx_ctrl":
        run_rx(args.count)
    else:
        run_report(Path(args.work))


if __name__ == "__main__":
    main()
