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

class PlatformSim:
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
      self.platform_mission_status_type = self.qos_provider.type("platform_mission_status")
      self.platform_waypoint_status_type = self.qos_provider.type("platform_waypoint_status")
      self.platform_debug_status_type = self.qos_provider.type("platform_debug_status")
      self.platform_thruster_status_type = self.qos_provider.type("platform_thruster_status")
      self.platform_power_status_type = self.qos_provider.type("platform_power_status")
      self.platform_data_type = self.qos_provider.type("platform_data")
      self.contact_report_type = self.qos_provider.type("contact_report")



      # Create Topics and associate with types
      self.control_cmd_topic = dds.DynamicData.Topic(
          self.participant,
          "ControlCommand",
          self.control_cmd_type
      )
      self.control_cmd_ack_topic = dds.DynamicData.Topic(
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
      self.platform_mission_status_topic = dds.DynamicData.Topic(
          self.participant,
          "PlatformMissionStatus",
          self.platform_mission_status_type
      )
      self.platform_waypoint_status_topic = dds.DynamicData.Topic(
          self.participant,
          "PlatformWaypointStatus",
          self.platform_waypoint_status_type
      )
      self.platform_debug_status_topic = dds.DynamicData.Topic(
          self.participant,
          "PlatformDebugStatus",
          self.platform_debug_status_type
      )
      self.platform_thruster_status_topic = dds.DynamicData.Topic(
          self.participant,
          "PlatformThrusterStatus",
          self.platform_thruster_status_type
      )
      self.platform_power_status_topic = dds.DynamicData.Topic(
          self.participant,
          "PlatformPowerStatus",
          self.platform_power_status_type
      )
      self.platform_data_topic = dds.DynamicData.Topic(
          self.participant,
          "PlatformData",
          self.platform_data_type
      )
      self.contact_report_topic = dds.DynamicData.Topic(
          self.participant,
          "ContactReport",
          self.contact_report_type
      )

      # Create DataWriters/DataReaders with the specified QoS profiles
      self.control_cmd_reader = dds.DynamicData.DataReader(
          self.control_cmd_topic,
          self.qos_provider.datareader_qos_from_profile(args.qos_profile)
      )
      self.platform_data_reader = dds.DynamicData.DataReader(
          self.platform_data_topic,
          self.qos_provider.datareader_qos_from_profile(args.qos_profile)
      )
      self.contact_report_reader = dds.DynamicData.DataReader(
          self.contact_report_topic,
          self.qos_provider.datareader_qos_from_profile(args.qos_profile)
      )
      self.control_cmd_ack_writer = dds.DynamicData.DataWriter(
          self.control_cmd_ack_topic,
          self.qos_provider.datawriter_qos_from_profile(args.qos_profile)
      )
      self.platform_primary_status_writer = dds.DynamicData.DataWriter(
          self.platform_primary_status_topic,
          self.qos_provider.datawriter_qos_from_profile(args.qos_profile)
      )
      self.platform_detail_status_writer = dds.DynamicData.DataWriter(
          self.platform_detail_status_topic,
          self.qos_provider.datawriter_qos_from_profile(args.qos_profile)
      )
      self.platform_mission_status_writer = dds.DynamicData.DataWriter(
          self.platform_mission_status_topic,
          self.qos_provider.datawriter_qos_from_profile(args.qos_profile)
      )
      self.platform_waypoint_status_writer = dds.DynamicData.DataWriter(
          self.platform_waypoint_status_topic,
          self.qos_provider.datawriter_qos_from_profile(args.qos_profile)
      )
      self.platform_debug_status_writer = dds.DynamicData.DataWriter(
          self.platform_debug_status_topic,
          self.qos_provider.datawriter_qos_from_profile(args.qos_profile)
      )
      self.platform_thruster_status_writer = dds.DynamicData.DataWriter(
          self.platform_thruster_status_topic,
          self.qos_provider.datawriter_qos_from_profile(args.qos_profile)
      )
      self.platform_power_status_writer = dds.DynamicData.DataWriter(
          self.platform_power_status_topic,
          self.qos_provider.datawriter_qos_from_profile(args.qos_profile)
      )
      self.platform_data_writer = dds.DynamicData.DataWriter(
          self.platform_data_topic,
          self.qos_provider.datawriter_qos_from_profile(args.qos_profile)
      )
      self.contact_report_writer = dds.DynamicData.DataWriter(
          self.contact_report_topic,
          self.qos_provider.datawriter_qos_from_profile(args.qos_profile)
      )

      time.sleep(2)

      # Ignore self published Topics
      print("ignoring self published PlatformData")
      self.participant.ignore_datawriter(self.platform_data_writer.instance_handle)

      print("ignoring self published ContactReport")
      self.participant.ignore_datawriter(
          self.contact_report_writer.instance_handle)


    async def read_control_command(self):
      print("Waiting for Control Commands")
      async for data in self.control_cmd_reader.take_data_async():
        print(f'- Received ControlCommand from {data["msg.source"]}')

    async def read_platform_data(self):
      print("Waiting for Platform Data ")
      async for data in self.platform_data_reader.take_data_async():
        print(f'- Received PlatformData from {data["msg.source"]}')

    async def read_contact_report(self):
      print("Waiting for Contact Report")
      async for data in self.contact_report_reader.take_data_async():
        print(f'- Received ContactReport from {data["msg.source"]}')


    async def write_cmd_ack(self):
      # Create sample
      cmd_ack_sample = dds.DynamicData(self.control_cmd_ack_type)

      # Set Source
      cmd_ack_sample["msg.source"] = args.source

      # Set Destination
      cmd_ack_sample["msg.destination"] = args.destination

      # Set Session "GUID"
      session_guid = [args.session for d in range(16)]
      cmd_ack_sample["msg.session"] = session_guid

      # Create sim "Payload"
      payload = [random.randrange(0, 10, 2) for d in range(16)]
      cmd_ack_sample["msg.payload"] = payload

      while True:
          self.control_cmd_ack_writer.write(cmd_ack_sample)
          print("Writing to ControlCommandAck topic")
          await asyncio.sleep(1)

    async def write_primary_status(self):
      # Create sample
      primary_status_sample = dds.DynamicData(self.platform_primary_status_type)

      # Set Source
      primary_status_sample["msg.source"] = args.source

      # Set Destination
      primary_status_sample["msg.destination"] = args.destination

      # Set Session "GUID"
      session_guid = [args.session for d in range(16)]
      primary_status_sample["msg.session"] = session_guid

      # Create sim "Payload"
      payload = [random.randrange(0, 10, 2) for d in range(16)]
      primary_status_sample["msg.payload"] = payload

      while True:
        self.platform_primary_status_writer.write(primary_status_sample)
        print("Writing to PlatformPrimaryStatus topic")
        await asyncio.sleep(1)

    async def write_detail_status(self):
      # Create sample
      detail_status_sample = dds.DynamicData(self.platform_detail_status_type)

      # Set Source
      detail_status_sample["msg.source"] = args.source

      # Set Destination
      detail_status_sample["msg.destination"] = args.destination

      # Set Session "GUID"
      session_guid = [args.session for d in range(16)]
      detail_status_sample["msg.session"] = session_guid

      # Create sim "Payload"
      payload = [random.randrange(0, 10, 2) for d in range(16)]
      detail_status_sample["msg.payload"] = payload

      while True:
        self.platform_detail_status_writer.write(detail_status_sample)
        print("Writing to PlatformDetailStatus topic")
        await asyncio.sleep(1)

    async def write_mission_status(self):
        mission_status_sample = dds.DynamicData(self.platform_mission_status_type)
        mission_status_sample["msg.source"] = args.source
        mission_status_sample["msg.destination"] = args.destination
        mission_status_sample["msg.source_type"] = "Mission"
        session_guid = [args.session for d in range(16)]
        mission_status_sample["msg.session"] = session_guid

        while True:
            payload = [random.randrange(0, 50, 3) for d in range(16)]
            mission_status_sample["msg.payload"] = payload
            self.platform_mission_status_writer.write(mission_status_sample)
            print("Writing to PlatformMissionStatus topic")
            await asyncio.sleep(1)

    async def write_waypoint_status(self):
        waypoint_status_sample = dds.DynamicData(self.platform_waypoint_status_type)
        waypoint_status_sample["msg.source"] = args.source
        waypoint_status_sample["msg.destination"] = args.destination
        waypoint_status_sample["msg.source_type"] = "Waypoint"
        session_guid = [args.session for d in range(16)]
        waypoint_status_sample["msg.session"] = session_guid

        while True:
            payload = [random.randrange(0, 100, 5) for d in range(16)]
            waypoint_status_sample["msg.payload"] = payload
            self.platform_waypoint_status_writer.write(waypoint_status_sample)
            print("Writing to PlatformWaypointStatus topic")
            await asyncio.sleep(1)

    async def write_debug_status(self):
        debug_status_sample = dds.DynamicData(self.platform_debug_status_type)
        debug_status_sample["msg.source"] = args.source
        debug_status_sample["msg.destination"] = args.destination
        debug_status_sample["msg.source_type"] = "Debug"
        session_guid = [args.session for d in range(16)]
        debug_status_sample["msg.session"] = session_guid

        while True:
            payload = [random.randrange(0, 255, 7) for d in range(16)]
            debug_status_sample["msg.payload"] = payload
            self.platform_debug_status_writer.write(debug_status_sample)
            print("Writing to PlatformDebugStatus topic")
            await asyncio.sleep(1)

    async def write_thruster_status(self):
        thruster_status_sample = dds.DynamicData(self.platform_thruster_status_type)
        thruster_status_sample["msg.source"] = args.source
        thruster_status_sample["msg.destination"] = args.destination
        thruster_status_sample["msg.source_type"] = "Thruster"
        session_guid = [args.session for d in range(16)]
        thruster_status_sample["msg.session"] = session_guid

        while True:
            payload = [random.randrange(0, 120, 4) for d in range(16)]
            thruster_status_sample["msg.payload"] = payload
            self.platform_thruster_status_writer.write(thruster_status_sample)
            print("Writing to PlatformThrusterStatus topic")
            await asyncio.sleep(1)

    async def write_power_status(self):
        power_status_sample = dds.DynamicData(self.platform_power_status_type)
        power_status_sample["msg.source"] = args.source
        power_status_sample["msg.destination"] = args.destination
        power_status_sample["msg.source_type"] = "Power"
        session_guid = [args.session for d in range(16)]
        power_status_sample["msg.session"] = session_guid

        while True:
            payload = [random.randrange(0, 240, 6) for d in range(16)]
            power_status_sample["msg.payload"] = payload
            self.platform_power_status_writer.write(power_status_sample)
            print("Writing to PlatformPowerStatus topic")
            await asyncio.sleep(1)

    async def write_data(self):
      # Create sample
      data_sample = dds.DynamicData(self.platform_data_type)

      # Set Source
      data_sample["msg.source"] = args.source

      # Set Destination
      data_sample["msg.destination"] = args.destination

      # Set Session "GUID"
      session_guid = [args.session for d in range(16)]
      data_sample["msg.session"] = session_guid

      # Create sim "Payload"
      payload = [random.randrange(0, 10, 2) for d in range(16)]
      data_sample["msg.payload"] = payload

      while True:
        self.platform_data_writer.write(data_sample)
        print("Writing to PlatformData topic")
        await asyncio.sleep(1)

    async def write_contact_report(self):
      # Create sample
      contact_report_sample = dds.DynamicData(self.contact_report_type)

      # Set Source Name
      contact_report_sample["msg.source"] = args.source

      # Set Source Type
      contact_report_sample["msg.source_type"] = "Platform"

      # Set Destination Name
      contact_report_sample["msg.destination"] = args.destination

      # Set Session "GUID"
      session_guid = [args.session for d in range(16)]
      contact_report_sample["msg.session"] = session_guid

      # Create sim "Payload"
      payload = [random.randrange(0, 10, 2) for d in range(16)]
      contact_report_sample["msg.payload"] = payload

      while True:
          self.contact_report_writer.write(contact_report_sample)
          print("Writing to ContactReport topic")
          await asyncio.sleep(1)

    async def run(self) -> None:
        await asyncio.gather(
            self.read_control_command(),
            self.read_platform_data(),
            self.read_contact_report(),
            self.write_cmd_ack(),
            self.write_primary_status(),
            self.write_detail_status(),
            self.write_mission_status(),
            self.write_waypoint_status(),
            self.write_debug_status(),
            self.write_thruster_status(),
            self.write_power_status(),
            self.write_data(),
            self.write_contact_report()
            )




if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Platform Sim"
    )
    print("\n\nRUNNING PLATFORM SIM\n\n")
    parser.add_argument(
        "--source", type=str, default=0, help="Source"
    )
    parser.add_argument(
        "--destination", type=str, default="", help="Destination"
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
      rti.asyncio.run(PlatformSim(args).run())
        
    except KeyboardInterrupt:
        pass


