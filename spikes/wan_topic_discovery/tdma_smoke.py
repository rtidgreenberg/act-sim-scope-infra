"""Verify six equal-priority synthetic DDS topics over a two-node TDMA link."""

import argparse
import json
import time
from pathlib import Path

import rti.connextdds as dds
from rti.types.builtin import String


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=("tx", "rx"))
    role = parser.parse_args().role
    names = [f"LabStream{i}" for i in range(1, 7)]
    expected = {name: {f"{name}:{seq}" for seq in range(10)} for name in names}
    qos = dds.QosProvider(str(Path(__file__).with_name("qos.xml")))
    started = time.monotonic()
    with dds.DomainParticipant(102, qos.participant_qos_from_profile("SPIKE::emane_v6")) as p:
        topics = {name: dds.Topic(p, name, String) for name in names}
        if role == "tx":
            profile = qos.datawriter_qos_from_profile("SPIKE::normal")
            endpoints = {n: dds.DataWriter(p.implicit_publisher, t, profile) for n, t in topics.items()}
            matches = lambda e: len(e.matched_subscriptions)
        else:
            profile = qos.datareader_qos_from_profile("SPIKE::emane_probe_reader")
            endpoints = {n: dds.DataReader(p.implicit_subscriber, t, profile) for n, t in topics.items()}
            matches = lambda e: len(e.matched_publications)
        while not all(matches(e) == 1 for e in endpoints.values()):
            if time.monotonic() - started > 15:
                raise RuntimeError(f"{role} discovery timed out: "
                                   f"{ {n: matches(e) for n, e in endpoints.items()} }")
            time.sleep(0.01)
        result = {"role": role, "matches": {n: matches(e) for n, e in endpoints.items()},
                  "match_ms": round((time.monotonic() - started) * 1000)}
        if role == "tx":
            for seq in range(10):
                for name, writer in endpoints.items():
                    writer.write(String(f"{name}:{seq}"))
                time.sleep(0.1)
            time.sleep(1)
        else:
            received = {n: set() for n in names}
            deadline = time.monotonic() + 8
            while received != expected and time.monotonic() < deadline:
                for name, reader in endpoints.items():
                    for sample in reader.take():
                        if sample.info.valid:
                            value = sample.data.value
                            if value not in expected[name] or value in received[name]:
                                raise RuntimeError(f"Unexpected or duplicate sample on {name}: {value}")
                            received[name].add(value)
                time.sleep(0.01)
            result["received"] = {n: len(v) for n, v in received.items()}
            if received != expected:
                raise RuntimeError(f"Incomplete delivery: {result}")
        print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
