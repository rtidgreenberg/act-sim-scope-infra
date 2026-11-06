#!/usr/bin/env python3
"""Read-only fleet capture and independent Ethernet/MAC/RTPS reconciliation."""

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import ipaddress
import json
import logging
from pathlib import Path
import re
import struct
import subprocess
import sys
import time
import urllib.request


ROOT = Path(__file__).resolve().parents[2]
PREFIX = "act-emane-container-baseline-"

CAPTURE = """
import json, pathlib, subprocess, sys, time
work, duration = sys.argv[1], int(sys.argv[2])
with open(work + '/capture.log', 'w') as log:
    process = subprocess.Popen(
        ['dumpcap', '-p', '-i', 'emane0', '-P', '-s', '0',
         '-a', 'duration:' + str(duration), '-w', work + '/capture.pcap'],
        stdout=log, stderr=log)
    try:
        time.sleep(1)
        if process.poll() is not None:
            raise RuntimeError(pathlib.Path(work + '/capture.log').read_text())
        print(json.dumps({'pid': process.pid}), flush=True)
        code = process.wait(timeout=duration + 15)
        if code:
            raise RuntimeError(pathlib.Path(work + '/capture.log').read_text())
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
"""

SNAPSHOT = """
import json, subprocess, sys, time
nem = sys.argv[1]
start = time.time_ns()
stats = subprocess.run(['emanesh', 'localhost', 'get stat ' + nem + ' all'],
                       check=True, text=True, capture_output=True).stdout
end = time.time_ns()
table_start = time.time_ns()
tables = subprocess.run(['emanesh', 'localhost', 'get table ' + nem + ' all'],
                        check=True, text=True, capture_output=True).stdout
print(json.dumps({'start_ns': start, 'end_ns': end,
                  'table_start_ns': table_start, 'table_end_ns': time.time_ns(),
                  'stats': stats, 'tables': tables}))
"""


def run(args):
    result = subprocess.run(args, check=True, text=True, capture_output=True, timeout=30)
    return result.stdout


def api(url, endpoint):
    with urllib.request.urlopen(url.rstrip("/") + "/api/" + endpoint, timeout=10) as response:
        return json.load(response)


def parse_stats(text):
    result = defaultdict(dict)
    for layer, name, value in re.findall(
            r"nem\s+\d+\s+(\w+)\s+((?:num|processed)\w+)\s*=\s*(\d+)\s*$", text, re.M):
        result[layer][name] = int(value)
    return dict(result)


def parse_drop_tables(text):
    result = {}
    for block in text.split("\n\n"):
        lines = block.strip().splitlines()
        if not lines or "PacketDropTable" not in lines[0]:
            continue
        title = lines[0].split()
        headings = [column.strip() for column in lines[1].split("|")[1:-1]]
        rows = {}
        for line in lines[2:]:
            values = [column.strip() for column in line.split("|")[1:-1]]
            if values and values[0].isdigit():
                rows[values[0]] = dict(zip(headings[1:], map(int, values[1:])))
        result[" ".join(title[2:])] = rows
    return result


def snapshot(node):
    value = json.loads(run(["docker", "exec", node["container"], "python3", "-c",
                            SNAPSHOT, str(node["nem"])]))
    value["timestamp_ns"] = (value["start_ns"] + value["end_ns"]) // 2
    value["counters"] = parse_stats(value.pop("stats"))
    value["drops"] = parse_drop_tables(value.pop("tables"))
    return value


def layer_delta(before, after):
    result = {}
    for layer, counters in before.items():
        result[layer] = {}
        for name, value in counters.items():
            current = after[layer][name]
            if current < value:
                raise ValueError(f"Counter reset: {layer}.{name}")
            result[layer][name] = current - value
    return result


def model_headers(delta):
    headers = {}
    for layer in ("shim0", "mac"):
        counters = delta[layer]
        def total(unit, way):
            return sum(counters[f"numDownstream{unit}{kind}{way}0"]
                       for kind in ("Unicast", "Broadcast"))
        packets = total("Packets", "Tx")
        if not packets or packets != total("Packets", "Rx"):
            raise ValueError(f"Cannot derive {layer} headers across unbalanced packet boundaries")
        headers[layer] = (total("Bytes", "Tx") - total("Bytes", "Rx")) / packets
    headers["total"] = sum(headers.values())
    return headers


def pcap_records(path):
    with path.open("rb") as source:
        header = source.read(24)
        formats = {
            b"\xd4\xc3\xb2\xa1": ("<", 1000),
            b"\xa1\xb2\xc3\xd4": (">", 1000),
            b"\x4d\x3c\xb2\xa1": ("<", 1),
            b"\xa1\xb2\x3c\x4d": (">", 1),
        }
        if len(header) != 24 or header[:4] not in formats:
            raise ValueError(f"Not a classic pcap: {path}")
        endian, scale = formats[header[:4]]
        if struct.unpack_from(endian + "I", header, 20)[0] != 1:
            raise ValueError("Expected Ethernet link type")
        while record := source.read(16):
            if len(record) != 16:
                raise ValueError("Truncated pcap record")
            seconds, fraction, captured, original = struct.unpack(endian + "IIII", record)
            packet = source.read(captured)
            if len(packet) != original or captured != original:
                raise ValueError("Truncated captured frame")
            yield seconds * 1_000_000_000 + fraction * scale, packet


def decode_frame(packet):
    if len(packet) < 14:
        raise ValueError("Truncated Ethernet header")
    frame = {"source_mac": packet[6:12].hex(), "destination_mac": packet[:6].hex(),
             "bytes": len(packet), "kind": "non_ipv4"}
    ether_type = int.from_bytes(packet[12:14], "big")
    if ether_type in (0x8100, 0x88a8):
        raise ValueError("VLAN frames require explicit decoder support")
    if ether_type == 0x0806:
        frame["kind"] = "arp"
    if ether_type != 0x0800:
        return frame
    ip = packet[14:]
    if len(ip) < 20 or ip[0] >> 4 != 4:
        raise ValueError("Invalid IPv4 header")
    length = (ip[0] & 15) * 4
    total = int.from_bytes(ip[2:4], "big")
    if not 20 <= length <= total <= len(ip):
        raise ValueError("Truncated IPv4 packet")
    frame.update(source_ip=str(ipaddress.IPv4Address(ip[12:16])),
                 destination_ip=str(ipaddress.IPv4Address(ip[16:20])), kind="non_rtps",
                 ip_protocol=ip[9])
    if ip[9] == 1 and total >= length + 2:
        frame.update(icmp_type=ip[length], icmp_code=ip[length + 1])
        if (frame["icmp_type"], frame["icmp_code"]) == (3, 3):
            frame["kind"] = "icmp_port_unreachable"
    if ip[9] != 17:
        return frame
    fragment = int.from_bytes(ip[6:8], "big")
    if fragment & 0x3fff:
        frame["fragment_key"] = (frame["source_ip"], frame["destination_ip"],
                                 int.from_bytes(ip[4:6], "big"))
    if fragment & 0x1fff:
        return frame
    udp = ip[length:total]
    if len(udp) < 8:
        raise ValueError("Truncated UDP header")
    port = int.from_bytes(udp[2:4], "big")
    frame.update(udp_source_port=int.from_bytes(udp[:2], "big"), udp_destination_port=port)
    frame["domain"] = (port - 7400) // 250 if port >= 7400 else None
    if udp[8:12] == b"RTPS":
        frame["kind"] = "rtps"
    elif udp[8:12] == b"RTPX":
        frame["kind"] = "rtpx"
    return frame


def frames(path):
    decoded = [(timestamp, decode_frame(packet)) for timestamp, packet in pcap_records(path)]
    fragments = {frame["fragment_key"]: (frame.get("domain"), frame["kind"])
                 for _, frame in decoded if "fragment_key" in frame and "domain" in frame}
    for _, frame in decoded:
        if "fragment_key" in frame:
            metadata = fragments.get(frame["fragment_key"])
            if metadata is None:
                frame["kind"] = "unresolved_fragment"
            else:
                frame["domain"], frame["kind"] = metadata
    return decoded


def count_frames(decoded, mac, start_ns, end_ns):
    totals, routes = Counter(), defaultdict(Counter)
    for timestamp, frame in decoded:
        if not start_ns <= timestamp < end_ns:
            continue
        direction = "tx" if frame["source_mac"] == mac else "rx"
        totals[direction + "_bytes"] += frame["bytes"]
        totals[direction + "_packets"] += 1
        kind = frame["kind"]
        if kind == "rtps":
            kind = "wan_rtps" if frame["domain"] == 200 else "other_rtps"
            totals[f'rtps_domain_{frame["domain"]}_{direction}_bytes'] += frame["bytes"]
            totals[f'rtps_domain_{frame["domain"]}_{direction}_packets'] += 1
        totals[f"{kind}_{direction}_bytes"] += frame["bytes"]
        totals[f"{kind}_{direction}_packets"] += 1
        route = f'{frame.get("source_ip", frame["source_mac"])} -> {frame.get("destination_ip", frame["destination_mac"])}'
        routes[route][direction + "_bytes"] += frame["bytes"]
        routes[route][direction + "_packets"] += 1
    return dict(totals), dict(routes)


def discover():
    names = run(["docker", "ps", "--format", "{{.Names}}",
                 "--filter", "name=" + PREFIX]).splitlines()
    nodes = []
    for name in sorted(names):
        match = re.fullmatch(re.escape(PREFIX) + r"(control|platform)-(\d+)-1", name)
        if not match:
            raise ValueError(f"Unexpected baseline container: {name}")
        role, identifier = match.groups()
        identity = json.loads(run(["docker", "exec", name, "ip", "-j", "address",
                                   "show", "dev", "emane0"]))[0]
        nodes.append(dict(container=name, name=f"{role.title()}_{identifier}",
                          nem=1 if role == "control" else int(identifier) - 28,
                          mac=identity["address"].replace(":", "")))
    if not nodes or not any(node["name"] == "Control_20" for node in nodes):
        raise ValueError("No running baseline mesh found")
    return nodes


def monitor_records(path, offset):
    with path.open("rb") as source:
        if offset:
            source.seek(offset - 1)
            if source.read(1) != b"\n":
                source.readline()
        return [json.loads(line) for line in source if line.endswith(b"\n")]


def compare(name, actual, reference, tolerance):
    difference = actual - reference
    return dict(name=name, actual=actual, reference=reference, difference=difference,
                tolerance=tolerance, passed=abs(difference) <= tolerance)


def compare_monitor_intervals(decoded, mac, intervals):
    reference = Counter()
    interval_checks = []
    for sample in intervals:
        start = round((sample["capture_timestamp"] - sample["interval_ms"]) * 1_000_000)
        end = round(sample["capture_timestamp"] * 1_000_000)
        counted, _ = count_frames(decoded, mac, start, end)
        for direction in ("tx", "rx"):
            for unit in ("packets", "bytes"):
                field = direction + "_" + unit
                expected = counted.get("wan_rtps_" + field, 0)
                reference[field] += expected
                tolerance = max(2 if unit == "packets" else 3000, expected * .02)
                check = compare("Production WAN " + field, sample[field], expected, tolerance)
                check.update(start_ns=start, end_ns=end)
                interval_checks.append(check)
    aggregate_checks = []
    for field, expected in reference.items():
        actual = sum(sample[field] for sample in intervals)
        tolerance = max(2 if field.endswith("packets") else 3000, expected * .02)
        aggregate_checks.append(compare("Production WAN " + field, actual, expected, tolerance))
    return aggregate_checks, interval_checks


def cleanup_captures(nodes, captures):
    original_error = sys.exc_info()[1]
    errors = []
    for node in nodes:
        process = captures.get(node["name"])
        if process is not None and process.poll() is None:
            if "capture_pid" in node:
                try:
                    run(["docker", "exec", node["container"], "kill", "-INT", str(node["capture_pid"])])
                except (OSError, subprocess.SubprocessError) as error:
                    errors.append((f"{node['name']}: stopping capture", error))
            try:
                process.communicate(timeout=30)
            except (OSError, subprocess.SubprocessError) as error:
                errors.append((f"{node['name']}: waiting for capture; retaining {node['work']}", error))
        if "work" in node and (process is None or process.poll() is not None):
            try:
                run(["docker", "exec", node["container"], "python3", "-c",
                     "import os,sys; p=sys.argv[1]; "
                     "[os.unlink(p+'/'+n) for n in ('capture.pcap','capture.log') if os.path.exists(p+'/'+n)]; "
                     "os.rmdir(p)", node["work"]])
            except (OSError, subprocess.SubprocessError) as error:
                errors.append((f"{node['name']}: removing {node['work']}", error))
    for operation, error in errors:
        logging.error("Capture cleanup failed (%s): %s", operation, error)
    if errors and original_error is None:
        raise RuntimeError("Capture cleanup failed: " + "; ".join(operation for operation, _ in errors)) from errors[0][1]


def measure(args):
    nodes = discover()
    test_id = args.test_id or "mac-rtps-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", test_id):
        raise ValueError("test-id must contain only letters, digits, underscores and hyphens")
    output = ROOT / "debug/pcap/router" / test_id
    report_path = ROOT / "debug/test_reports" / (test_id + ".json")
    if output.exists() or report_path.exists():
        raise ValueError("Use a fresh test ID; retained evidence will not be overwritten")
    output.mkdir(parents=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = dict(test_id=test_id, duration_requested_s=args.duration, nodes={},
                  experiment_before=api(args.dashboard, "emane_controller"))
    calibration = (json.loads(args.calibration_report.read_text())
                   if args.calibration_report else None)
    report["calibration_report"] = str(args.calibration_report) if calibration else None
    captures = {}
    try:
        for node in nodes:
            path = ROOT / "debug" / (node["name"].lower() + "_debug") / "traffic_stats.jsonl"
            node.update(log_path=path, log_offset=path.stat().st_size,
                        work=run(["docker", "exec", node["container"], "mktemp", "-d",
                                  "/tmp/mac_rtps.XXXXXX"]).strip())
            process = subprocess.Popen(
                ["docker", "exec", node["container"], "python3", "-c", CAPTURE,
                 node["work"], str(args.duration + 15)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            captures[node["name"]] = process
            line = process.stdout.readline()
            if not line:
                raise RuntimeError(process.communicate(timeout=5)[1])
            node["capture_pid"] = json.loads(line)["pid"]
        with ThreadPoolExecutor(max_workers=len(nodes)) as pool:
            before = list(pool.map(snapshot, nodes))
            time.sleep(args.duration)
            after = list(pool.map(snapshot, nodes))
        report["status"] = "captured_pending_analysis"
        report["snapshots"] = {node["name"]: dict(before=first, after=last)
                               for node, first, last in zip(nodes, before, after)}
        report_path.write_text(json.dumps(report, indent=2) + "\n")
        for node in nodes:
            process = captures[node["name"]]
            _, error = process.communicate(timeout=30)
            if process.returncode:
                raise RuntimeError(error)
            capture_path = output / (node["name"] + ".pcap")
            run(["docker", "cp", node["container"] + ":" + node["work"] + "/capture.pcap",
                 str(capture_path)])
            node["capture_log"] = run(["docker", "exec", node["container"], "cat",
                                       node["work"] + "/capture.log"])
        for node, first, last in zip(nodes, before, after):
            capture_path = output / (node["name"] + ".pcap")
            capture_log = node["capture_log"]
            decoded = frames(capture_path)
            delta = layer_delta(first["counters"], last["counters"])
            headers = model_headers(calibration["nodes"][node["name"]]["delta"]
                                    if calibration else delta)
            start, end = first["timestamp_ns"], last["timestamp_ns"]
            duration = (end - start) / 1_000_000_000
            ethernet, routes = count_frames(decoded, node["mac"], start, end)
            mac = delta["mac"]
            tx_packets = sum(mac[f"numDownstreamPackets{k}Tx0"] for k in ("Unicast", "Broadcast"))
            tx_bytes = sum(mac[f"numDownstreamBytes{k}Tx0"] for k in ("Unicast", "Broadcast"))
            rx_packets = sum(mac[f"numUpstreamPackets{k}Rx0"] for k in ("Unicast", "Broadcast"))
            rx_bytes = sum(mac[f"numUpstreamBytes{k}Rx0"] for k in ("Unicast", "Broadcast"))
            forwarding_packets = sum(mac[f"numUpstreamPackets{k}Tx0"] for k in ("Unicast", "Broadcast"))
            checks = [compare("Ethernet TX vs MAC TX packets", ethernet.get("tx_packets", 0),
                              tx_packets, max(2, tx_packets * .02)),
                      compare("MAC TX bytes vs Ethernet plus measured model headers",
                              tx_bytes, ethernet.get("tx_bytes", 0) + ethernet.get("tx_packets", 0) * headers["total"],
                              max(3000, tx_bytes * .02))]
            captured_drops = re.search(r"received/dropped.*?:\s*(\d+)\s*/\s*(\d+)", capture_log)
            if captured_drops is None:
                raise ValueError(f"Capture drop count unavailable: {capture_log}")
            checks.append(compare("Independent capture kernel drops", int(captured_drops[2]), 0, 0))
            intervals = []
            for record in monitor_records(node["log_path"], node["log_offset"]):
                for sample in record["samples"]:
                    a = round((sample["capture_timestamp"] - sample["interval_ms"]) * 1_000_000)
                    b = round(sample["capture_timestamp"] * 1_000_000)
                    if sample["domain_id"] == 200 and start <= a < b <= end:
                        intervals.append(sample)
            if not intervals:
                raise ValueError(f"No complete monitor intervals: {node['name']}")
            checks.extend([
                compare("Production capture gap or non-live intervals",
                        sum(sample.get("capture_gap", False) or sample.get("capture_status") != "live"
                            for sample in intervals), 0, 0),
                compare("Production capture restart count changes",
                        len({sample.get("capture_restarts", 0) for sample in intervals}) - 1, 0, 0),
            ])
            aggregate_checks, interval_checks = compare_monitor_intervals(decoded, node["mac"], intervals)
            checks.extend(aggregate_checks)
            drops = {}
            for table, rows in last["drops"].items():
                drops[table] = {
                    nem: {reason: value - first["drops"].get(table, {}).get(nem, {}).get(reason, 0)
                          for reason, value in reasons.items()}
                    for nem, reasons in rows.items()}
            report["nodes"][node["name"]] = dict(
                identity={k: node[k] for k in ("container", "nem", "mac")},
                before=first, after=last, delta=delta, drops=drops,
                model_header_bytes=headers,
                measurement_s=duration, ethernet=ethernet, routes=routes,
                mac_tx_packets=tx_packets, mac_tx_bytes=tx_bytes,
                mac_rx_packets=rx_packets, mac_rx_bytes=rx_bytes,
                mac_forwarded_rx_packets=forwarding_packets,
                rates_kbps=dict(mac_rx=rx_bytes * 8 / duration / 1000,
                                mac_tx=tx_bytes * 8 / duration / 1000,
                                ethernet_rx=ethernet.get("rx_bytes", 0) * 8 / duration / 1000,
                                rtps_rx=ethernet.get("wan_rtps_rx_bytes", 0) * 8 / duration / 1000,
                                rtps_tx=ethernet.get("wan_rtps_tx_bytes", 0) * 8 / duration / 1000),
                extra_mac_tx_bytes_per_ethernet_frame=(
                    (tx_bytes - ethernet.get("tx_bytes", 0)) / ethernet["tx_packets"]),
                monitor_intervals=intervals, capture_log=capture_log, checks=checks,
                interval_checks=interval_checks)
            node["decoded"] = decoded
            node["monitor_records"] = monitor_records(node["log_path"], node["log_offset"])
        for name, value in report["nodes"].items():
            others = [other for node, other in report["nodes"].items() if node != name]
            tx_rate = sum(other["mac_tx_packets"] / other["measurement_s"] for other in others)
            tx_byte_rate = sum(other["mac_tx_bytes"] / other["measurement_s"] for other in others)
            value["peer_transmit_kbps"] = tx_byte_rate * 8 / 1000
            value["checks"].extend([
                compare("MAC RX packets/s vs other radios TX", value["mac_rx_packets"] / value["measurement_s"],
                        tx_rate, max(2 / value["measurement_s"], tx_rate * .02)),
                compare("MAC RX bytes/s vs other radios TX", value["mac_rx_bytes"] / value["measurement_s"],
                        tx_byte_rate, max(3000 / value["measurement_s"], tx_byte_rate * .02)),
            ])
            subject = next(node for node in nodes if node["name"] == name)
            mac_intervals = [record["emane_mac_tx"] for record in subject["monitor_records"]
                             if record.get("emane_mac_tx")
                             and value["before"]["timestamp_ns"] <=
                             (record["emane_mac_tx"]["capture_timestamp"] -
                              record["emane_mac_tx"]["interval_ms"]) * 1_000_000
                             and record["emane_mac_tx"]["capture_timestamp"] * 1_000_000 <=
                             value["after"]["timestamp_ns"]]
            if not mac_intervals:
                raise ValueError(f"No complete MAC monitor intervals: {name}")
            expected_rx = Counter()
            for sample in mac_intervals:
                a = round((sample["capture_timestamp"] - sample["interval_ms"]) * 1_000_000)
                b = round(sample["capture_timestamp"] * 1_000_000)
                for peer in nodes:
                    if peer["name"] == name:
                        continue
                    counted, _ = count_frames(peer["decoded"], peer["mac"], a, b)
                    expected_rx["packets"] += counted.get("tx_packets", 0)
                    expected_rx["bytes"] += (
                        counted.get("tx_bytes", 0) + counted.get("tx_packets", 0) *
                        report["nodes"][peer["name"]]["model_header_bytes"]["total"])
            for unit in ("packets", "bytes"):
                actual = sum(sample[f"mac_rx_{kind}_{unit}"] for sample in mac_intervals
                             for kind in ("unicast", "broadcast"))
                expected = expected_rx[unit]
                value["checks"].append(compare("Reported MAC RX vs independent peer Ethernet " + unit,
                                               actual, expected,
                                               max(2 if unit == "packets" else 3000, expected * .02)))
            value["mac_monitor_intervals"] = mac_intervals
        report["experiment_after"] = api(args.dashboard, "emane_controller")
        report["experiment_unchanged"] = (
            report["experiment_before"].get("impairments") == report["experiment_after"].get("impairments")
            and all(node["delta"]["shim0"].get("processedEvents", 0) == 0
                    for node in report["nodes"].values()))
        report["aggregate_passed"] = report["experiment_unchanged"] and all(
            check["passed"] for node in report["nodes"].values() for check in node["checks"])
        report["interval_timing_passed"] = all(
            check["passed"] for node in report["nodes"].values() for check in node["interval_checks"])
        report["passed"] = report["aggregate_passed"] and report["interval_timing_passed"]
        report["status"] = "analysed"
        del report["snapshots"]
        report_path.write_text(json.dumps(report, indent=2) + "\n")
        for name, node in report["nodes"].items():
            print(name, json.dumps(node["rates_kbps"]), "overhead/frame",
                  round(node["extra_mac_tx_bytes_per_ethernet_frame"], 2))
            for check in node["checks"]:
                if not check["passed"]:
                    print("  DISCREPANCY", json.dumps(check))
            failed_intervals = [check for check in node["interval_checks"] if not check["passed"]]
            if failed_intervals:
                print("  INTERVAL DISCREPANCIES", len(failed_intervals),
                      "(all timestamped comparisons retained in report)")
                for check in failed_intervals[:3]:
                    print("   ", json.dumps(check))
        print("REPORT", report_path)
        return report["passed"]
    finally:
        cleanup_captures(nodes, captures)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration", type=int, default=60)
    parser.add_argument("--test-id")
    parser.add_argument("--dashboard", default="http://localhost:8080")
    parser.add_argument("--calibration-report", type=Path,
                        help="Use a prior balanced-counter window for model header calibration")
    args = parser.parse_args()
    if not 10 <= args.duration <= 120:
        parser.error("duration must be between 10 and 120 seconds")
    raise SystemExit(0 if measure(args) else 1)


if __name__ == "__main__":
    main()
