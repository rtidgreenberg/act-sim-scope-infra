# (c) 2025 Copyright, Real-Time Innovations, Inc.  All rights reserved.
# RTI grants Licensee a license to use, modify, compile, and create derivative
# works of the Software.  Licensee has the right to distribute object form only
# for use with RTI products.  The Software is provided "as is", with no warranty
# of any type, including any warranty for fitness for any purpose. RTI is under
# no obligation to maintain or support the Software.  RTI shall not be liable for
# any incidental or consequential damages arising out of the use or inability to
# use the software.

import rti.connextdds as dds
from rti.types.builtin import String
import time
import argparse
import random
import threading
import rti.asyncio
import asyncio
import uuid

class C2Sim:
    def __init__(self, args):

      # Load QoS/Types from XML files (uses NDDS_QOS_PROFILES env var)
      self.qos_provider = dds.QosProvider.default

      # Create a Participant from specific QOS Profile
      self.participant = dds.DomainParticipant(
          args.domain_id, self.qos_provider.participant_qos_from_profile(
              args.qos_profile)
      )

      #Pull in DynamicData types
      self.control_cmd_type = self.qos_provider.type("control_command")
      self.control_cmd_ack_type = self.qos_provider.type("control_command_ack")
      self.platform_init_status_type = self.qos_provider.type("platform_init_status")
      self.platform_detail_status_type = self.qos_provider.type("platform_detail_status")
      self.contact_report_type = self.qos_provider.type("contact_report")


      # Create Topics and associate with types
      self.control_cmd_topic = dds.DynamicData.Topic(
          self.participant,
          "ControlCommand",
          self.control_cmd_type
      )
      self.platform_cmd_ack_topic = dds.DynamicData.Topic(
          self.participant,
          "PlatformCommandAck",
          self.control_cmd_ack_type
      )
      self.platform_init_status_topic = dds.DynamicData.Topic(
          self.participant,
          "PlatformInitStatus",
          self.platform_init_status_type
      )
      self.platform_detail_status_topic = dds.DynamicData.Topic(
          self.participant,
          "PlatformDetailStatus",
          self.platform_detail_status_type
      )

      self.contact_report_topic = dds.DynamicData.Topic(
          self.participant,
          "ContactReport",
          self.contact_report_type
      )

      # Create DataWriters/DataReaders with the specified QoS profiles
      self.control_cmd_writer = dds.DynamicData.DataWriter(
          self.control_cmd_topic,
          self.qos_provider.datawriter_qos_from_profile(args.qos_profile)
      )
      self.control_contact_report_writer = dds.DynamicData.DataWriter(
          self.contact_report_topic,
          self.qos_provider.datawriter_qos_from_profile(args.qos_profile)
      )
      self.platform_cmd_ack_reader = dds.DynamicData.DataReader(
          self.platform_cmd_ack_topic,
          self.qos_provider.datareader_qos_from_profile(args.qos_profile)
      )
      self.platform_init_status_reader = dds.DynamicData.DataReader(
          self.platform_init_status_topic,
          self.qos_provider.datareader_qos_from_profile(args.qos_profile)
      )
      self.platform_detail_status_reader = dds.DynamicData.DataReader(
          self.platform_detail_status_topic,
          self.qos_provider.datareader_qos_from_profile(args.qos_profile)
      )
      self.contact_report_reader = dds.DynamicData.DataReader(
          self.contact_report_topic,
          self.qos_provider.datareader_qos_from_profile(args.qos_profile)
      )

      print("ignoring self published ContactReports")
      self.participant.ignore_datawriter(
          self.control_contact_report_writer.instance_handle)

    async def read_primary_status_data(self):
      print("Waiting for Primary Status data")
      async for data in self.platform_init_status_reader.take_data_async():
        print(f'- Received PlatformInitStatus from {data["source"]}')
       

    async def read_detail_status_data(self):
      print("Waiting for Detail Status data")
      async for data in self.platform_detail_status_reader.take_data_async():
        print(f'- Received PlatformDetailStatus from {data["source"]}')

    async def read_cmd_ack_data(self):
      print("Waiting for CommandAck data")
      async for data in self.platform_cmd_ack_reader.take_data_async():
        print(f'- Received PlatformCommandAck from {data["source"]}')

    async def read_contact_report_data(self):
      print("Waiting for ContactReport data")
      async for data in self.contact_report_reader.take_data_async():
        print(f'- Received ContactReport from {data["source"]}')

    async def write_cmd(self):
      import math
      cmd_sample = dds.DynamicData(self.control_cmd_type)
      cmd_sample["source"] = args.source
      cmd_sample["destination"] = args.destination

      contact_sample = dds.DynamicData(self.contact_report_type)
      contact_sample["source"] = args.source
      seq = 0

      while True:
          seq += 1
          t = seq * 0.2
          # Control command
          cmd_sample["command_id"] = f"cmd-{seq}"
          cmd_sample["command_type"] = "STATUS_REQUEST"
          cmd_sample["payload"] = [random.randrange(0, 10, 2) for _ in range(16)]
          cmd_sample["timestamp"] = int(time.time() * 1_000_000)
          self.control_cmd_writer.write(cmd_sample)
          print("Writing to ControlCommand topic")

          # Contact report
          contact_sample["contact_id"] = f"C2-{(seq // 20) % 3:03d}"
          contact_sample["classification"] = random.choice(["FRIENDLY", "UNKNOWN", "HOSTILE"])
          contact_sample["bearing_deg"] = (90.0 + t * 2.0) % 360.0
          contact_sample["range_m"] = 5000.0 + 1000.0 * math.sin(t * 0.1)
          contact_sample["course_deg"] = (200.0 + t) % 360.0
          contact_sample["speed_knots"] = 12.0 + 2.0 * math.sin(t * 0.3)
          contact_sample["depth_m"] = 0.0
          contact_sample["confidence_pct"] = min(99.0, 70.0 + seq * 0.05)
          contact_sample["sensor_type"] = "RADAR"
          contact_sample["latitude"] = 33.5 + 0.01 * math.sin(t * 0.05)
          contact_sample["longitude"] = -117.5 + 0.01 * math.cos(t * 0.05)
          contact_sample["lost"] = False
          contact_sample["timestamp"] = int(time.time() * 1_000_000)
          self.control_contact_report_writer.write(contact_sample)
          print("Writing to ContactReport topic")

          await asyncio.sleep(1)


    async def run(self) -> None:
        await asyncio.gather(
            self.write_cmd(),
            self.read_primary_status_data(),
            self.read_detail_status_data(),
            self.read_cmd_ack_data(),
            self.read_contact_report_data()
            )




if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="C2 Sim"
    )
    print("\n\nRUNNING C2 SIM\n\n")
    parser.add_argument(
        "--source", type=str, default=0, help="Source Name"
    )
    parser.add_argument(
        "--destination", type=str, default=1, help="Destination Name"
    )
    parser.add_argument(
        "--qos_profile", type=str, default=0, help="QOS Profile"
    )
    parser.add_argument(
        "--session", type=int, default=0, help="Session ID"
    )
    parser.add_argument(
        "-d", "--domain_id", type=int, default=0, help="Domain ID"
    )
    parser.add_argument(
        "-v", "--verbosity", type=int, default=1, help="How much debugging output to show | Range: 0-3 | Default: 1",
    )

    args = parser.parse_args()

    verbosity_levels = {
        0: dds.Verbosity.SILENT,
        1: dds.Verbosity.EXCEPTION,
        2: dds.Verbosity.WARNING,
        3: dds.Verbosity.STATUS_ALL,
    }

    # Sets Connext verbosity to help debugging
    verbosity = verbosity_levels.get(args.verbosity, dds.Verbosity.EXCEPTION)

    dds.Logger.instance.verbosity = verbosity

    try:
      # Run
      rti.asyncio.run(C2Sim(args).run())
        
    except KeyboardInterrupt:
        pass


