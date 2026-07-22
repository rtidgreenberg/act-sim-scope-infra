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
      self.platform_primary_status_type = self.qos_provider.type("platform_primary_status")
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
      self.platform_primary_status_topic = dds.DynamicData.Topic(
          self.participant,
          "PlatformPrimaryStatus",
          self.platform_primary_status_type
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
      self.platform_primary_status_reader = dds.DynamicData.DataReader(
          self.platform_primary_status_topic,
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
      async for data in self.platform_primary_status_reader.take_data_async():
        print(f'- Received PlatformPrimaryStatus from {data["msg.source"]}')
       

    async def read_detail_status_data(self):
      print("Waiting for Detail Status data")
      async for data in self.platform_detail_status_reader.take_data_async():
        print(f'- Received PlatformDetailStatus from {data["msg.source"]}')

    async def read_cmd_ack_data(self):
      print("Waiting for CommandAck data")
      async for data in self.platform_cmd_ack_reader.take_data_async():
        print(f'- Received PlatformCommandAck from {data["msg.source"]}')

    async def read_contact_report_data(self):
      print("Waiting for ContactReport data")
      async for data in self.contact_report_reader.take_data_async():
        print(f'- Received ContactReport from {data["msg.source"]}')

    async def write_cmd(self):
      # Create Command sample
      cmd_sample = dds.DynamicData(self.control_cmd_type)

      # Set Source
      cmd_sample["msg.source"] = args.source

      # Set Destination
      cmd_sample["msg.destination"] = args.destination

      # Set Session "GUID"
      session_guid = [args.session for d in range(16)]
      cmd_sample["msg.session"] = session_guid

      # Create sim "Payload"
      payload = [random.randrange(0, 10, 2) for d in range(16)]
      cmd_sample["msg.payload"] = payload


      # Create Contact Report sample
      contact_report_sample = dds.DynamicData(self.contact_report_type)

      # Set Source Name
      contact_report_sample["msg.source"] = args.source

      # Set Source Type
      contact_report_sample["msg.source_type"] = "C2"

      # Set Destination Name
      contact_report_sample["msg.destination"] = args.destination

      # Set Session "GUID"
      session_guid = [args.session for d in range(16)]
      contact_report_sample["msg.session"] = session_guid

      # Create sim "Payload"
      payload = [random.randrange(0, 10, 2) for d in range(16)]
      contact_report_sample["msg.payload"] = payload


      while True:
          # Send C2 Command
          self.control_cmd_writer.write(cmd_sample)
          print("Writing to ControlCommand topic")

          # Send C2 Contact Report
          self.control_contact_report_writer.write(contact_report_sample)
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


