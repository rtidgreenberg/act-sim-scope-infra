#!/usr/bin/env python3
"""Separate latest contact state from required events without changing production IDL."""

from pathlib import Path
import argparse
from functools import partial
import sys

import rti.connextdds as dds

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "instance_churn_reconnect"))
from spike import NativeEndpoints, main, memory_kib, wait_for
from topics import COMPARISON_CASES, topic_class


class SymmetricEndpoints(NativeEndpoints):
    extra_qos_files = (HERE / "qos.xml",)

    def __init__(self, family, filtered, event_durability="production",
                 priority_mode="production", priorities=None, flow_profile="coarse"):
        self.rss_before_endpoints_kib = memory_kib()
        super().__init__(family, filtered, event_durability, priority_mode, priorities, flow_profile)
        self.reverse_writers = {}
        self.reverse_readers = {}
        try:
            source, receiver = self.participants
            for topic_name in self.topics:
                if topic_name == "Event":
                    continue
                _, writer_profile, reader_profile = self.endpoint_profiles()[topic_name]
                source_topic, receiver_topic = self.topics[topic_name]
                description = source_topic
                if filtered:
                    description = dds.DynamicData.ContentFilteredTopic(
                        source_topic, topic_name + "ReverseFiltered",
                        dds.Filter("source_id <> 'ChurnSource'"))
                reader = dds.DynamicData.DataReader(
                    dds.Subscriber(source), description,
                    self.qos.datareader_qos_from_profile(reader_profile))
                self.cleanup.callback(reader.close)
                source.ignore_datareader(reader.instance_handle)
                writer = dds.DynamicData.DataWriter(
                    dds.Publisher(receiver), receiver_topic,
                    self.qos.datawriter_qos_from_profile(writer_profile))
                self.cleanup.callback(writer.close)
                receiver.ignore_datawriter(writer.instance_handle)
                self.reverse_readers[topic_name] = reader
                self.reverse_writers[topic_name] = writer
            wait_for(lambda: all(
                writer.publication_matched_status.current_count == 1
                for writer in self.all_writers().values()) and all(
                    reader.subscription_matched_status.current_count == 1
                    for reader in self.all_readers().values()))
            self.rss_after_endpoints_kib = memory_kib()
        except Exception:
            self.close()
            raise

    def all_writers(self):
        return {**{"Source/" + name: writer for name, writer in self.writers.items()},
                "Receiver/Event": self.ack_writer,
                **{"Receiver/" + name: writer for name, writer in self.reverse_writers.items()}}

    def all_readers(self):
        return {"Source/Event": self.ack_reader,
                **{"Receiver/" + name: reader for name, reader in self.readers.items()},
                **{"Source/" + name: reader for name, reader in self.reverse_readers.items()}}

    def metrics(self):
        result = super().metrics()
        writers = {name: writer for name, writer in self.all_writers().items()
                   if not name.endswith("/Status")}
        result["writer_metrics"] = {}
        for name, writer in writers.items():
            cache = writer.datawriter_cache_status
            protocol = writer.datawriter_protocol_status
            values = {
                "cache_samples": cache.sample_count,
                "unacked": writer.reliable_writer_cache_changed_status.unacknowledged_sample_count,
                "pushed_bytes": protocol.pushed_sample_bytes,
                "pulled_bytes": protocol.pulled_sample_bytes,
                "nacks": protocol.received_nack_count,
                "inactive_readers": writer.reliable_reader_activity_changed_status.inactive_count,
            }
            result["writer_metrics"][name] = {
                field: value.total if isinstance(value, (dds.EventCount32, dds.EventCount64)) else value
                for field, value in values.items()}
        result["contact_unacked"] = result["writer_metrics"]["Source/" + self.data_topic]["unacked"]
        result["event_unacked"] = result["writer_metrics"]["Source/Event"]["unacked"]
        result["ack_unacked"] = result["writer_metrics"]["Receiver/Event"]["unacked"]
        result["unacked"] = sum(values["unacked"] for values in result["writer_metrics"].values())
        result["health_deadline_misses"] = self.readers["Status"].requested_deadline_missed_status.total_count
        return result

    def effective_qos(self):
        result = super().effective_qos()
        reader_protocol = self.reader.qos.protocol.rtps_reliable_reader
        result["repair_reader"] = {name: str(getattr(reader_protocol, name)) for name in (
            "nack_period", "min_heartbeat_response_delay", "max_heartbeat_response_delay",
            "heartbeat_suppression_duration")}
        result["rss_before_endpoints_kib"] = self.rss_before_endpoints_kib
        result["rss_after_endpoints_kib"] = self.rss_after_endpoints_kib
        readers = self.all_readers()
        result["endpoint_inventory"] = {
            "writers": {name: {
                "topic": writer.topic_name, "writer_guid": str(writer.instance_handle),
                "history": str(writer.qos.history.kind),
                "depth": writer.qos.history.depth, "reliability": str(writer.qos.reliability.kind),
                "durability": str(writer.qos.durability.kind),
                "channel_count": len(writer.qos.multi_channel.channels),
                "flow_controller": writer.qos.publish_mode.flow_controller_name,
                "matched_readers": writer.publication_matched_status.current_count,
            } for name, writer in self.all_writers().items()},
            "readers": {name: {
                "topic": reader.matched_publication_data(reader.matched_publications[0]).topic_name,
                "topic_description": reader.topic_name, "matched_writers": len(reader.matched_publications),
                "history": str(reader.qos.history.kind),
                "depth": reader.qos.history.depth, "reliability": str(reader.qos.reliability.kind),
                "heartbeat_suppression": str(reader.qos.protocol.rtps_reliable_reader.heartbeat_suppression_duration),
                "heartbeat_suppression_s": float(
                    reader.qos.protocol.rtps_reliable_reader.heartbeat_suppression_duration),
                "deadline": str(reader.qos.deadline.period),
            } for name, reader in readers.items()},
        }
        return result


class ThreeTopicEndpoints(SymmetricEndpoints):
    contact_topic = "Contacts"
    requires_complete_history = False
    contract = "three_topic"

    def endpoint_profiles(self):
        profiles = super().endpoint_profiles()
        profiles["Event"] = ("WanEvent", "ThreeTopic::events", "ChurnSpike::event_reader")
        profiles["Contacts"] = ("WanEvent", "ThreeTopic::contacts", "ThreeTopic::contact_reader")
        return profiles

    def __init__(self, family="Event", filtered=True, event_durability="production",
                 priority_mode="sample_priority", priorities=None, flow_profile="coarse"):
        if family != "Event" or event_durability != "production" or priority_mode != "sample_priority":
            raise ValueError("Three-topic spike requires contact workload, volatile durability and sample priority")
        super().__init__(family, filtered, event_durability, priority_mode, priorities, flow_profile)

    def write(self, writer, data, operation="write", handle=None):
        original_topic = str(data["original_topic"])
        if writer.topic_name != topic_class(original_topic):
            raise ValueError(f"{original_topic} cannot use {writer.topic_name}")
        super().write(writer, data, operation, handle)


class ComparisonEndpoints(ThreeTopicEndpoints):
    def __init__(self, *args, comparison_case, **kwargs):
        if comparison_case not in ("baseline", "bands_all", "bands_last", "none_all",
                                   "bands_no_suppression", "split_keep_all"):
            raise ValueError(f"Unknown three-topic comparison: {comparison_case}")
        self.comparison_case = comparison_case
        self.requires_complete_history = comparison_case == "split_keep_all"
        super().__init__(*args, **kwargs)

    def endpoint_profiles(self):
        profiles = super().endpoint_profiles()
        if self.comparison_case.startswith("bands") or self.comparison_case == "split_keep_all":
            profiles["Event"] = ("WanEvent", "ChurnSpike::priority_bands_repair",
                                 "ChurnSpike::event_reader")
            profiles["Contacts"] = ("WanEvent", "ChurnSpike::priority_bands_last1",
                                    "ThreeTopic::contact_reader")
        if self.comparison_case.endswith("all"):
            type_name, writer, _ = profiles["Contacts"]
            profiles["Contacts"] = (type_name, writer, "ChurnSpike::event_reader")
        if self.comparison_case == "split_keep_all":
            profiles["Contacts"] = ("WanEvent", "ChurnSpike::priority_bands_repair",
                                    "ChurnSpike::event_reader")
        if self.comparison_case == "bands_no_suppression":
            type_name, writer, _ = profiles["Contacts"]
            profiles["Contacts"] = (type_name, writer, "ThreeTopic::contact_reader_no_suppression")
        return profiles

    def effective_qos(self):
        return {**super().effective_qos(), "comparison_case": self.comparison_case}


class MixedControlEndpoints(SymmetricEndpoints):
    def __init__(self, *args, comparison_case="mixed_last", **kwargs):
        if comparison_case not in ("mixed_last", "mixed_all", "mixed_no_suppression"):
            raise ValueError(f"Unknown two-topic comparison: {comparison_case}")
        self.comparison_case = comparison_case
        super().__init__(*args, **kwargs)

    def endpoint_profiles(self):
        profiles = super().endpoint_profiles()
        writer = ("ChurnSpike::priority_bands_last1" if self.comparison_case == "mixed_last"
                  else "ChurnSpike::priority_bands_repair")
        reader = ("ThreeTopic::contact_reader_no_suppression"
                  if self.comparison_case == "mixed_no_suppression" else "ChurnSpike::event_reader")
        profiles["Event"] = ("WanEvent", writer, reader)
        return profiles

    def effective_qos(self):
        return {**super().effective_qos(), "comparison_case": self.comparison_case}


if __name__ == "__main__":
    comparison_parser = argparse.ArgumentParser(add_help=False)
    comparison_parser.add_argument("--comparison-case", choices=COMPARISON_CASES, default="baseline")
    comparison, remaining = comparison_parser.parse_known_args()
    sys.argv[1:] = remaining
    endpoint_class = (MixedControlEndpoints if comparison.comparison_case.startswith("mixed")
                      else ComparisonEndpoints)
    factory = partial(endpoint_class, comparison_case=comparison.comparison_case)
    main(factory, default_priority_mode="sample_priority",
         default_family="Event", priority_modes=("sample_priority",))
