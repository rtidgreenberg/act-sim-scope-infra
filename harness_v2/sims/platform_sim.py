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
      self.platform_init_status_type = self.qos_provider.type("platform_init_status")
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
      self.platform_init_status_writer = dds.DynamicData.DataWriter(
          self.platform_init_status_topic,
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
      import math
      sample = dds.DynamicData(self.platform_init_status_type)
      sample["source"] = args.source
      seq = 0
      base_lat = 33.0 + random.uniform(-1, 1)
      base_lon = -117.0 + random.uniform(-1, 1)

      while True:
        seq += 1
        t = seq * 0.1
        sample["latitude"] = base_lat + 0.001 * math.sin(t)
        sample["longitude"] = base_lon + 0.001 * math.cos(t)
        sample["altitude_m"] = -50.0 + 2.0 * math.sin(t * 0.3)
        sample["heading_deg"] = (90.0 + t * 5.0) % 360.0
        sample["speed_knots"] = 8.0 + 2.0 * math.sin(t * 0.5)
        sample["heartbeat_seq"] = seq
        sample["timestamp"] = int(time.time() * 1_000_000)
        self.platform_init_status_writer.write(sample)
        print("Writing to PlatformInitStatus topic")
        await asyncio.sleep(1)

    async def write_detail_status(self):
      import math
      sample = dds.DynamicData(self.platform_detail_status_type)
      sample["source"] = args.source
      seq = 0

      while True:
        seq += 1
        t = seq * 0.2
        sample["roll_deg"] = 2.0 * math.sin(t)
        sample["pitch_deg"] = 1.5 * math.cos(t * 0.7)
        sample["yaw_deg"] = (180.0 + t * 3.0) % 360.0
        sample["depth_m"] = 50.0 + 5.0 * math.sin(t * 0.1)
        sample["battery_pct"] = max(0.0, 95.0 - seq * 0.01)
        sample["comms_link_quality"] = min(100, 85 + random.randint(-5, 5))
        sample["timestamp"] = int(time.time() * 1_000_000)
        self.platform_detail_status_writer.write(sample)
        print("Writing to PlatformDetailStatus topic")
        await asyncio.sleep(1)

    async def write_mission_status(self):
        sample = dds.DynamicData(self.platform_mission_status_type)
        sample["source"] = args.source
        sample["mission_id"] = f"MSN-{random.randint(1000,9999)}"
        sample["mission_phase"] = "TRANSIT"
        wp_total = random.randint(5, 12)
        sample["waypoints_total"] = wp_total
        seq = 0

        while True:
            seq += 1
            sample["waypoint_index"] = min(seq // 30, wp_total - 1)
            sample["distance_to_waypoint_m"] = max(0.0, 500.0 - (seq % 30) * 17.0)
            sample["mission_elapsed_s"] = float(seq)
            sample["mission_fuel_remaining_pct"] = max(0.0, 100.0 - seq * 0.05)
            if seq % 30 == 0:
                sample["mission_phase"] = random.choice(["TRANSIT", "LOITER", "EXECUTE", "RTB"])
            sample["timestamp"] = int(time.time() * 1_000_000)
            self.platform_mission_status_writer.write(sample)
            print("Writing to PlatformMissionStatus topic")
            await asyncio.sleep(1)

    async def write_waypoint_status(self):
        import math
        sample = dds.DynamicData(self.platform_waypoint_status_type)
        sample["source"] = args.source
        base_lat = 33.0 + random.uniform(-1, 1)
        base_lon = -117.0 + random.uniform(-1, 1)
        seq = 0

        while True:
            seq += 1
            wp_id = (seq // 20) % 10
            sample["waypoint_id"] = wp_id
            sample["wp_latitude"] = base_lat + wp_id * 0.01
            sample["wp_longitude"] = base_lon + wp_id * 0.008
            sample["wp_altitude_m"] = -30.0 - wp_id * 5.0
            sample["wp_speed_knots"] = 6.0 + wp_id * 0.5
            sample["eta_s"] = max(0.0, 300.0 - (seq % 20) * 15.0)
            sample["achieved"] = (seq % 20) == 0
            sample["timestamp"] = int(time.time() * 1_000_000)
            self.platform_waypoint_status_writer.write(sample)
            print("Writing to PlatformWaypointStatus topic")
            await asyncio.sleep(1)

    async def write_debug_status(self):
        sample = dds.DynamicData(self.platform_debug_status_type)
        sample["source"] = args.source
        seq = 0

        while True:
            seq += 1
            sample["cpu_usage_pct"] = random.randint(15, 65)
            sample["mem_usage_pct"] = random.randint(40, 75)
            sample["disk_usage_pct"] = min(99, 50 + seq // 100)
            sample["internal_temp_c"] = 35.0 + random.uniform(-2, 5)
            sample["process_count"] = random.randint(80, 120)
            sample["error_count"] = random.randint(0, 3)
            sample["warning_count"] = random.randint(0, 10)
            sample["last_error_msg"] = "" if random.random() > 0.1 else "sensor timeout"
            sample["timestamp"] = int(time.time() * 1_000_000)
            self.platform_debug_status_writer.write(sample)
            print("Writing to PlatformDebugStatus topic")
            await asyncio.sleep(1)

    async def write_thruster_status(self):
        sample = dds.DynamicData(self.platform_thruster_status_type)
        sample["source"] = args.source
        seq = 0

        while True:
            seq += 1
            sample["thruster_id"] = seq % 4
            sample["rpm"] = 1200.0 + random.uniform(-50, 50)
            sample["commanded_rpm"] = 1200.0
            sample["current_amps"] = 12.0 + random.uniform(-1, 2)
            sample["temperature_c"] = 45.0 + random.uniform(-3, 8)
            sample["fault"] = random.random() < 0.02
            sample["timestamp"] = int(time.time() * 1_000_000)
            self.platform_thruster_status_writer.write(sample)
            print("Writing to PlatformThrusterStatus topic")
            await asyncio.sleep(1)

    async def write_power_status(self):
        sample = dds.DynamicData(self.platform_power_status_type)
        sample["source"] = args.source
        seq = 0

        while True:
            seq += 1
            sample["bus_voltage"] = 48.0 + random.uniform(-0.5, 0.5)
            sample["bus_current"] = 15.0 + random.uniform(-2, 3)
            sample["battery_voltage"] = 51.0 - seq * 0.001
            sample["battery_soc_pct"] = max(0.0, 95.0 - seq * 0.02)
            sample["power_watts"] = sample["bus_voltage"] * sample["bus_current"]
            sample["energy_consumed_wh"] = seq * 0.2
            sample["charging"] = False
            sample["time_remaining_min"] = max(0, int(480 - seq * 0.1))
            sample["timestamp"] = int(time.time() * 1_000_000)
            self.platform_power_status_writer.write(sample)
            print("Writing to PlatformPowerStatus topic")
            await asyncio.sleep(1)
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


