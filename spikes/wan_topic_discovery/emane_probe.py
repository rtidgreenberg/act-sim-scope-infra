"""Send and receive best-effort DDS samples with two UDPv6 traffic classes."""

import argparse
from pathlib import Path
import re
import time

import rti.connextdds as dds
from rti.types.builtin import String


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("role", choices=("tx", "rx"))
    args = parser.parse_args()
    qos = dds.QosProvider(str(Path(__file__).with_name("qos.xml")))
    with dds.DomainParticipant(102, qos.participant_qos_from_profile("SPIKE::emane_v6")) as p:
        topics = {name: dds.Topic(p, f"RfPipePriority{name}", String) for name in "AB"}
        if args.role == "rx":
            reader_qos = qos.datareader_qos_from_profile("SPIKE::emane_probe_reader")
            readers = [dds.DataReader(p.implicit_subscriber, topic, reader_qos) for topic in topics.values()]
            print("Ready", flush=True)
            end = time.monotonic() + 38
            while time.monotonic() < end:
                for reader in readers:
                    for sample in reader.take():
                        if sample.info.valid:
                            marker = re.match(r"RFTEST-[CAB]-[AB]-\d{4}", sample.data.value)
                            if marker is None:
                                raise ValueError("Unexpected DDS test payload")
                            print(marker[0])
                time.sleep(0.001)
            return

        writers = {
            (lane, band): dds.DataWriter(
                p.implicit_publisher, topic, qos.datawriter_qos_from_profile(f"SPIKE::{band}"))
            for lane, topic in topics.items() for band in ("normal", "high")
        }
        end = time.monotonic() + 10
        while not all(len(w.matched_subscriptions) == 1 for w in writers.values()):
            if time.monotonic() >= end:
                raise RuntimeError("DDS endpoints did not match over emane0")
            time.sleep(0.02)
        for case, pairs, interval in (("C", 60, 0.02), ("A", 1000, 0.0015), ("B", 1000, 0.0015)):
            start = time.monotonic()
            for seq in range(pairs):
                for lane in ("AB" if seq % 2 == 0 else "BA"):
                    band = "high" if lane == ("A" if case == "C" else case) else "normal"
                    marker = f"RFTEST-{case}-{lane}-{seq:04}"
                    writers[lane, band].write(String(marker + "x" * (1000 - len(marker))))
                time.sleep(max(0, start + (seq + 1) * interval - time.monotonic()))
            print(f"{case}: sent {2 * pairs} samples in {time.monotonic() - start:.3f}s", flush=True)
            time.sleep(5)


if __name__ == "__main__":
    main()
