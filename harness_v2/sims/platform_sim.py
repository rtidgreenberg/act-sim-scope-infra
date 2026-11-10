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
import os
import uuid
import json
import math
from pathlib import Path
from delivery_audit import DeliveryAudit
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "gui/mesh_dashboard/server"))
from mission_planning import MIN_BOAT_SEPARATION_M, goal_plan
from mission_navigation import Navigator

RUN_ID = ""
AUDIT = None
AUDIT_START_FILE = ""
MISSION_PAYLOAD_VERSION = 1
MISSION_OPERATIONS = {"route", "replace_route", "cancel", "return_to_base", "set_time_scale"}
MAX_MISSION_WAYPOINTS = 64
MAX_MISSION_PAYLOAD_BYTES = 8192
MISSION_SPEED_KNOTS = 10.0
METERS_PER_NAUTICAL_MILE = 1852.0
SAN_DIEGO_LAUNCH_POSITIONS = {
    "Platform_30": (32.6900, -117.3200),
    "Platform_31": (32.7050, -117.3050),
    "Platform_32": (32.7200, -117.3200),
    "Platform_33": (32.7350, -117.3050),
    "Platform_34": (32.7500, -117.3200),
}


def parse_mission_payload(payload):
    """Validate the compact, versioned JSON carried by ControlCommand.payload."""
    try:
        raw = bytes(payload)
        if not raw or len(raw) > MAX_MISSION_PAYLOAD_BYTES:
            raise ValueError("payload must contain 1 to 8192 bytes")
        command = json.loads(raw.decode("utf-8"))
    except (TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("payload must be UTF-8 JSON") from exc
    if not isinstance(command, dict) or set(command) - {"v", "op", "w", "t", "l", "g", "f"}:
        raise ValueError("payload has unsupported fields")
    if command.get("v") != MISSION_PAYLOAD_VERSION:
        raise ValueError("unsupported mission payload version")
    operation = command.get("op")
    if operation not in MISSION_OPERATIONS:
        raise ValueError("unsupported mission operation")
    goal = command.get("g")
    fleet = command.get("f")
    if fleet is not None and (not isinstance(fleet, list) or not 1 <= len(fleet) <= 70 or
            any(not isinstance(node, str) or not node.startswith("Platform_") or
                not node[9:].isdigit() or not 30 <= int(node[9:]) <= 99 for node in fleet) or
            len(set(fleet)) != len(fleet)):
        raise ValueError("fleet must contain unique platform nodes 30 through 99")
    if goal is not None:
        if operation not in {"route", "replace_route"} or "w" in command or "l" in command:
            raise ValueError("goals require a route operation without precomputed navigation")
        if not isinstance(goal, dict):
            raise ValueError("goal must be an object")
        if goal.get("kind") == "loiter" and set(goal) == {"kind", "center", "radius_m"}:
            if (isinstance(goal["radius_m"], bool) or goal["radius_m"] not in (500, 1000, 2000, 3000)):
                raise ValueError("invalid loiter radius")
            points = [goal["center"]]
        elif goal.get("kind") == "waypoints" and set(goal) == {"kind", "waypoints"}:
            points = goal["waypoints"]
        else:
            raise ValueError("unsupported goal specification")
        parse_mission_payload(json.dumps({"v": 1, "op": "route", "w": points}).encode())
        if fleet is None:
            raise ValueError("autonomous goals require the shared-location fleet roster")
        return {"operation": operation, "waypoints": [], "goal": goal, "fleet": fleet}
    if operation == "set_time_scale":
        scale = command.get("t")
        if isinstance(scale, bool) or not isinstance(scale, (int, float)) or scale not in (1, 20):
            raise ValueError("time_scale must be 1 or 20")
        if "w" in command or "l" in command or "f" in command:
            raise ValueError("set_time_scale does not accept waypoints")
        return {"operation": operation, "waypoints": [], "time_scale": float(scale)}
    if "t" in command:
        raise ValueError(f"{operation} does not accept time_scale")
    waypoints = command.get("w", [])
    if operation in {"route", "replace_route"} or (operation == "return_to_base" and "w" in command):
        if not isinstance(waypoints, list) or not 1 <= len(waypoints) <= MAX_MISSION_WAYPOINTS:
            raise ValueError(f"{operation} requires 1 to {MAX_MISSION_WAYPOINTS} waypoints")
        normalized = []
        for waypoint in waypoints:
            if (not isinstance(waypoint, list) or len(waypoint) != 2 or
                    any(isinstance(value, bool) or not isinstance(value, (int, float))
                        for value in waypoint)):
                raise ValueError("each waypoint must be [latitude, longitude]")
            latitude, longitude = float(waypoint[0]), float(waypoint[1])
            if not -90.0 <= latitude <= 90.0 or not -180.0 <= longitude <= 180.0:
                raise ValueError("waypoint coordinate is out of range")
            normalized.append((latitude, longitude))
    elif "w" in command:
        raise ValueError(f"{operation} does not accept waypoints")
    else:
        normalized = []
    result = {"operation": operation, "waypoints": normalized}
    if fleet is not None:
        if operation == "cancel":
            raise ValueError("cancel does not accept a fleet")
        result["fleet"] = fleet
    if "l" in command:
        index = command["l"]
        if (operation not in {"route", "replace_route"} or isinstance(index, bool) or
                not isinstance(index, int) or not 0 <= index < len(normalized) or
                len(set(normalized[index:])) < 2):
            raise ValueError("loop_start must identify a non-degenerate route circuit")
        result["loop_start"] = index
    return result


def launch_position_for(source):
    if source in SAN_DIEGO_LAUNCH_POSITIONS:
        return SAN_DIEGO_LAUNCH_POSITIONS[source]
    try:
        offset = int(str(source).rsplit("_", 1)[1]) - 30
    except (IndexError, ValueError):
        offset = 0
    return (32.6900 + 0.015 * (offset % 5), -117.3200 - 0.015 * (offset // 5))


async def wait_for_audit_start():
    if not AUDIT_START_FILE:
        return
    start_file = Path(AUDIT_START_FILE)
    while not start_file.exists() or start_file.stat().st_size == 0:
        await asyncio.sleep(0.1)

class PlatformSim:
    def __init__(self, args):
      self.args = args
      self.home_latitude, self.home_longitude = launch_position_for(args.source)
      self.latitude = self.home_latitude
      self.longitude = self.home_longitude
      self.mission_id = ""
      self.mission_phase = "IDLE"
      self.waypoints = []
      self.waypoint_index = 0
      self.mission_started_at = None
      self.last_motion_at = time.monotonic()
      self.time_scale = 1.0
      self.mission_elapsed_s = 0.0
      self.loop_start = None
      self.mission_command_results = {}
      self.navigator = Navigator(args.source, RUN_ID)
      self.navigation_state = "IDLE"
      self.navigation_speed_knots = 0.0
      self.heading_deg = 0.0

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
          self.qos_provider.datareader_qos_from_profile("ACT_QOS_LIB::lan_event")
      )
      self.platform_data_reader = dds.DynamicData.DataReader(
          self.platform_data_topic,
          self.qos_provider.datareader_qos_from_profile(args.qos_profile)
      )
      self.location_reader = dds.DynamicData.DataReader(
          self.platform_init_status_topic,
          self.qos_provider.datareader_qos_from_profile("ACT_QOS_LIB::lan_status")
      )
      self.contact_report_reader = dds.DynamicData.DataReader(
          self.contact_report_topic,
          self.qos_provider.datareader_qos_from_profile(args.qos_profile)
      )
      self.control_cmd_ack_writer = dds.DynamicData.DataWriter(
          self.control_cmd_ack_topic,
          self.qos_provider.datawriter_qos_from_profile("ACT_QOS_LIB::lan_event")
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
          print(f'- Received ControlCommand from {data["source"]}')
          AUDIT.log("received", "ControlCommand", data)
          accepted = True
          message = "received"
          if data["destination"] == self.args.source and data["command_type"] == "MISSION":
              accepted, message = self.handle_mission_command(
                  data["payload"], data["command_id"], data["source"], data["run_id"])
          elif data["destination"] == self.args.source and data["command_type"] not in ("", "STATUS_REQUEST"):
              accepted = False
              message = f'unsupported command type: {data["command_type"]}'
          elif data["destination"] != self.args.source:
              accepted = False
              message = "command is addressed to another platform"
          ack = dds.DynamicData(self.control_cmd_ack_type)
          ack["source"] = self.args.source
          ack["destination"] = data["source"]
          ack["run_id"] = data["run_id"]
          ack["source_node"] = self.args.source
          ack["audit_sequence"] = data["audit_sequence"]
          ack["sent_at_ns"] = data["sent_at_ns"]
          ack["command_id"] = data["command_id"]
          ack["accepted"] = accepted
          ack["message"] = message
          ack["timestamp"] = int(time.time() * 1_000_000)
          self.control_cmd_ack_writer.write(ack)
          AUDIT.log("sent", "PlatformCommandAck", ack)

    def handle_mission_command(self, payload, command_id, source="", run_id=""):
      key = (run_id, source, command_id)
      if key in self.mission_command_results:
          return self.mission_command_results[key]
      try:
          command = parse_mission_payload(payload)
          self.apply_mission_command(command, command_id)
          result = (True, f'{command["operation"]} accepted')
      except ValueError as exc:
          result = (False, str(exc))
      self.mission_command_results[key] = result
      while len(self.mission_command_results) > 200:
          self.mission_command_results.pop(next(iter(self.mission_command_results)))
      return result

    def apply_mission_command(self, command, command_id):
      operation = command["operation"]
      now = time.monotonic()
      navigator = getattr(self, "navigator", None)
      if "goal" in command:
          if not navigator or self.args.source not in command["fleet"]:
              raise ValueError("goal requires onboard navigation and the platform in its fleet roster")
          planned = goal_plan(navigator.planner, [self.latitude, self.longitude],
                              self.args.source, command["goal"])
          command = dict(command, **planned)
      if operation == "return_to_base":
          waypoints = list(command["waypoints"]) or [(self.home_latitude, self.home_longitude)]
          if self._distance_m(*waypoints[-1], self.home_latitude, self.home_longitude) > 0.1:
              raise ValueError("return_to_base route must end at the platform home position")
          if navigator:
              waypoints = navigator.planner.plan([self.latitude, self.longitude],
                                                [[self.home_latitude, self.home_longitude]])
          command = dict(command, waypoints=waypoints)
      if navigator and operation not in {"cancel", "set_time_scale"}:
          navigator.expected_peers = set(command.get("fleet", [])) - {self.args.source}
          navigator.exclusions = (
              [(command["goal"]["center"], command["goal"]["radius_m"])]
              if command.get("goal", {}).get("kind") == "loiter" else [])
          navigator.detour = None
      if operation in {"route", "replace_route"}:
          self.waypoints = list(command["waypoints"])
          self.waypoint_index = 0
          self.mission_id = command_id
          self.mission_phase = "TRANSIT"
          self.mission_started_at = now
          self.mission_elapsed_s = 0.0
          self.loop_start = command.get("loop_start")
      elif operation == "cancel":
          self.waypoints = []
          self.waypoint_index = 0
          self.mission_phase = "CANCELLED"
          self.navigation_speed_knots = 0.0
          self.navigation_state = self.mission_phase
          self.loop_start = None
      elif operation == "set_time_scale":
          self.advance_mission()
          self.time_scale = command["time_scale"]
      else:
          self.waypoints = list(command["waypoints"])
          self.waypoint_index = 0
          self.mission_id = command_id
          self.mission_phase = "RTB"
          self.mission_started_at = now
          self.mission_elapsed_s = 0.0
          self.loop_start = None
      self.last_motion_at = now

    @staticmethod
    def _distance_m(latitude_a, longitude_a, latitude_b, longitude_b):
      latitude_scale = 111_320.0
      longitude_scale = latitude_scale * math.cos(math.radians((latitude_a + latitude_b) / 2.0))
      return math.hypot((latitude_b - latitude_a) * latitude_scale,
                        (longitude_b - longitude_a) * longitude_scale)

    def advance_mission(self):
      now = time.monotonic()
      elapsed_wall_s = max(0.0, now - self.last_motion_at)
      elapsed_s = elapsed_wall_s * self.time_scale
      travel_m = MISSION_SPEED_KNOTS * METERS_PER_NAUTICAL_MILE / 3600.0 * elapsed_s
      self.last_motion_at = now
      if self.mission_phase not in {"TRANSIT", "RTB", "ORBIT"} or not self.waypoints:
          self.navigation_speed_knots = 0.0
          self.navigation_state = self.mission_phase
          return
      self.mission_elapsed_s += elapsed_s
      navigator = getattr(self, "navigator", None)
      if navigator and travel_m > 0:
          start = (self.latitude, self.longitude)
          destination = self.waypoints[self.waypoint_index]
          finish, state = navigator.step(start, destination, travel_m, elapsed_wall_s, time.time())
          self.latitude, self.longitude = finish
          moved = self._distance_m(*start, *finish)
          self.navigation_speed_knots = min(MISSION_SPEED_KNOTS, moved / elapsed_s * 3600 / 1852)
          self.heading_deg = (math.degrees(math.atan2(
              (finish[1] - start[1]) * math.cos(math.radians(start[0])), finish[0] - start[0])) % 360
              if moved > .001 else self.heading_deg)
          if state != self.navigation_state:
              print(f"Navigation {self.args.source}: {state}", flush=True)
          self.navigation_state = state
          if self._distance_m(*finish, *destination) < .05:
              self.waypoint_index += 1
              if self.loop_start is not None:
                  if self.waypoint_index == len(self.waypoints):
                      self.waypoint_index = self.loop_start
                  if self.waypoint_index >= self.loop_start:
                      self.mission_phase = "ORBIT"
              elif self.waypoint_index == len(self.waypoints):
                  self.mission_phase = "COMPLETE"
                  self.navigation_speed_knots = 0.0
                  self.navigation_state = self.mission_phase
          return
      while travel_m > 0 and self.waypoint_index < len(self.waypoints):
          waypoint_lat, waypoint_lon = self.waypoints[self.waypoint_index]
          distance_m = self._distance_m(self.latitude, self.longitude, waypoint_lat, waypoint_lon)
          if distance_m <= travel_m or distance_m < 0.01:
              self.latitude, self.longitude = waypoint_lat, waypoint_lon
              travel_m -= distance_m
              self.waypoint_index += 1
              if self.loop_start is not None:
                  if self.waypoint_index == len(self.waypoints):
                      self.waypoint_index = self.loop_start
                  if self.waypoint_index >= self.loop_start:
                      self.mission_phase = "ORBIT"
              continue
          fraction = travel_m / distance_m
          self.latitude += (waypoint_lat - self.latitude) * fraction
          self.longitude += (waypoint_lon - self.longitude) * fraction
          travel_m = 0
      if self.waypoint_index == len(self.waypoints):
          self.mission_phase = "COMPLETE"
          self.navigation_speed_knots = 0.0
          self.navigation_state = self.mission_phase

    async def read_platform_data(self):
      print("Waiting for Platform Data ")
      async for data in self.platform_data_reader.take_data_async():
                print(f'- Received PlatformData from {data["source"]}'); AUDIT.log("received", "PlatformData", data)

    async def read_locations(self):
      async for data in self.location_reader.take_data_async():
          sample = json.loads(data.to_json())
          self.navigator.record_location(sample, time.time())
          if sample.get("source_node") != self.args.source and sample.get("run_id") == RUN_ID:
              AUDIT.log("received", "PlatformInitStatus", data)

    async def navigate(self):
      while True:
          self.advance_mission()
          await asyncio.sleep(.1)

    async def read_contact_report(self):
      print("Waiting for Contact Report")
      async for data in self.contact_report_reader.take_data_async():
                print(f'- Received ContactReport from {data["source"]}'); AUDIT.log("received", "ContactReport", data)


    async def write_primary_status(self):
      sample = dds.DynamicData(self.platform_init_status_type)
      sample["source"] = self.args.source
      seq = 0

      while True:
        seq += 1
        self.advance_mission()
        sample["latitude"] = self.latitude
        sample["longitude"] = self.longitude
        sample["altitude_m"] = 0.0
        sample["heading_deg"] = self.heading_deg
        sample["speed_knots"] = self.navigation_speed_knots
        sample["time_scale"] = self.time_scale
        sample["minimum_separation_m"] = MIN_BOAT_SEPARATION_M
        sample["navigation_state"] = self.navigation_state
        sample["mission_id"] = self.mission_id
        sample["mission_phase"] = self.mission_phase
        sample["heartbeat_seq"] = seq
        sample["run_id"] = RUN_ID
        sample["source_node"] = self.args.source
        sample["audit_sequence"] = seq
        sample["sent_at_ns"] = time.time_ns()
        sample["timestamp"] = int(time.time() * 1_000_000)
        self.platform_init_status_writer.write(sample)
        AUDIT.log("sent", "PlatformInitStatus", sample)
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
        sample["run_id"] = RUN_ID
        sample["source_node"] = args.source
        sample["audit_sequence"] = seq
        sample["sent_at_ns"] = time.time_ns()
        sample["timestamp"] = int(time.time() * 1_000_000)
        self.platform_detail_status_writer.write(sample)
        AUDIT.log("sent", "PlatformDetailStatus", sample)
        print("Writing to PlatformDetailStatus topic")
        await asyncio.sleep(1)

    async def write_mission_status(self):
        sample = dds.DynamicData(self.platform_mission_status_type)
        sample["source"] = self.args.source
        seq = 0

        while True:
            seq += 1
            self.advance_mission()
            sample["mission_id"] = self.mission_id
            sample["mission_phase"] = self.mission_phase
            sample["waypoint_index"] = self.waypoint_index
            sample["waypoints_total"] = len(self.waypoints)
            if self.waypoint_index < len(self.waypoints):
                waypoint_lat, waypoint_lon = self.waypoints[self.waypoint_index]
                sample["distance_to_waypoint_m"] = self._distance_m(
                    self.latitude, self.longitude, waypoint_lat, waypoint_lon)
            else:
                sample["distance_to_waypoint_m"] = 0.0
            sample["mission_elapsed_s"] = self.mission_elapsed_s
            sample["mission_fuel_remaining_pct"] = max(0.0, 100.0 - seq * 0.05)
            sample["run_id"] = RUN_ID
            sample["source_node"] = self.args.source
            sample["audit_sequence"] = seq
            sample["sent_at_ns"] = time.time_ns()
            sample["timestamp"] = int(time.time() * 1_000_000)
            self.platform_mission_status_writer.write(sample)
            AUDIT.log("sent", "PlatformMissionStatus", sample)
            print("Writing to PlatformMissionStatus topic")
            await asyncio.sleep(1)

    async def write_waypoint_status(self):
        sample = dds.DynamicData(self.platform_waypoint_status_type)
        sample["source"] = self.args.source
        seq = 0

        while True:
            seq += 1
            self.advance_mission()
            wp_id = min(self.waypoint_index, max(0, len(self.waypoints) - 1))
            sample["waypoint_id"] = wp_id
            if wp_id < len(self.waypoints):
                waypoint_lat, waypoint_lon = self.waypoints[wp_id]
                distance_m = self._distance_m(self.latitude, self.longitude, waypoint_lat, waypoint_lon)
                sample["wp_latitude"] = waypoint_lat
                sample["wp_longitude"] = waypoint_lon
                sample["eta_s"] = distance_m / (MISSION_SPEED_KNOTS * METERS_PER_NAUTICAL_MILE / 3600.0)
            else:
                sample["wp_latitude"] = self.latitude
                sample["wp_longitude"] = self.longitude
                sample["eta_s"] = 0.0
            sample["wp_altitude_m"] = 0.0
            sample["wp_speed_knots"] = MISSION_SPEED_KNOTS
            sample["achieved"] = self.mission_phase in {"HOLDING", "COMPLETE"}
            sample["run_id"] = RUN_ID
            sample["source_node"] = self.args.source
            sample["audit_sequence"] = seq
            sample["sent_at_ns"] = time.time_ns()
            sample["timestamp"] = int(time.time() * 1_000_000)
            self.platform_waypoint_status_writer.write(sample)
            AUDIT.log("sent", "PlatformWaypointStatus", sample)
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
            sample["run_id"] = RUN_ID
            sample["source_node"] = args.source
            sample["audit_sequence"] = seq
            sample["sent_at_ns"] = time.time_ns()
            sample["timestamp"] = int(time.time() * 1_000_000)
            self.platform_debug_status_writer.write(sample)
            AUDIT.log("sent", "PlatformDebugStatus", sample)
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
            sample["run_id"] = RUN_ID
            sample["source_node"] = args.source
            sample["audit_sequence"] = seq
            sample["sent_at_ns"] = time.time_ns()
            sample["timestamp"] = int(time.time() * 1_000_000)
            self.platform_thruster_status_writer.write(sample)
            AUDIT.log("sent", "PlatformThrusterStatus", sample)
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
            sample["run_id"] = RUN_ID
            sample["source_node"] = args.source
            sample["audit_sequence"] = seq
            sample["sent_at_ns"] = time.time_ns()
            sample["timestamp"] = int(time.time() * 1_000_000)
            self.platform_power_status_writer.write(sample)
            AUDIT.log("sent", "PlatformPowerStatus", sample)
            print("Writing to PlatformPowerStatus topic")
            await asyncio.sleep(1)
            await asyncio.sleep(1)

    async def write_data(self):
      import math
      sample = dds.DynamicData(self.platform_data_type)
      sample["source"] = args.source
      sample["sensor_id"] = "CTD-01"
      seq = 0

      while True:
        seq += 1
        t = seq * 0.1
        sample["water_temperature_c"] = 12.0 + 2.0 * math.sin(t * 0.3)
        sample["salinity_psu"] = 35.0 + 0.5 * math.sin(t * 0.2)
        sample["sound_velocity_mps"] = 1500.0 + 5.0 * math.sin(t * 0.1)
        sample["current_speed_knots"] = 1.5 + 0.5 * math.sin(t * 0.4)
        sample["current_direction_deg"] = (180.0 + t * 2.0) % 360.0
        sample["visibility_m"] = 15.0 + 5.0 * math.sin(t * 0.15)
        sample["sea_state"] = min(9, max(0, 3 + random.randint(-1, 1)))
        sample["ambient_noise_db"] = 60.0 + random.uniform(-5, 5)
        sample["run_id"] = RUN_ID
        sample["source_node"] = args.source
        sample["audit_sequence"] = seq
        sample["sent_at_ns"] = time.time_ns()
        sample["timestamp"] = int(time.time() * 1_000_000)
        self.platform_data_writer.write(sample)
        AUDIT.log("sent", "PlatformData", sample)
        print("Writing to PlatformData topic")
        await asyncio.sleep(1)

    async def write_contact_report(self):
      import math
      sample = dds.DynamicData(self.contact_report_type)
      sample["source"] = args.source
      seq = 0

      while True:
          seq += 1
          t = seq * 0.2
          sample["contact_id"] = f"C-{(seq // 30) % 5:03d}"
          sample["classification"] = random.choice(["MERCHANT", "FISHING", "UNKNOWN", "MILITARY"])
          sample["bearing_deg"] = (45.0 + t * 3.0) % 360.0
          sample["range_m"] = 2000.0 + 500.0 * math.sin(t * 0.1)
          sample["course_deg"] = (120.0 + t) % 360.0
          sample["speed_knots"] = 10.0 + 3.0 * math.sin(t * 0.3)
          sample["depth_m"] = 0.0
          sample["confidence_pct"] = min(99.0, 60.0 + seq * 0.1)
          sample["sensor_type"] = "PASSIVE_SONAR"
          sample["latitude"] = 33.0 + 0.01 * math.sin(t * 0.05)
          sample["longitude"] = -117.0 + 0.01 * math.cos(t * 0.05)
          sample["lost"] = random.random() < 0.05
          sample["run_id"] = RUN_ID
          sample["source_node"] = args.source
          sample["audit_sequence"] = seq
          sample["sent_at_ns"] = time.time_ns()
          sample["timestamp"] = int(time.time() * 1_000_000)
          self.contact_report_writer.write(sample)
          AUDIT.log("sent", "ContactReport", sample)
          print("Writing to ContactReport topic")
          await asyncio.sleep(1)

    async def run(self) -> None:
        await wait_for_audit_start()
        await asyncio.gather(
            self.read_control_command(),
            self.read_locations(),
            self.navigate(),
            self.read_platform_data(),
            self.read_contact_report(),
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
    parser.add_argument("--run-id", default="", help="Shared delivery-audit run identity")
    parser.add_argument("--audit-start-file", default="", help="Publish only after this gate is opened")

    args = parser.parse_args()
    RUN_ID = args.run_id or os.environ.get("ACT_RUN_ID", str(uuid.uuid4()))
    AUDIT = DeliveryAudit(args.source, RUN_ID)
    AUDIT_START_FILE = args.audit_start_file

    verbosity_levels = {
        0: dds.Verbosity.SILENT,
        1: dds.Verbosity.EXCEPTION,
        2: dds.Verbosity.WARNING,
        3: dds.Verbosity.STATUS_ALL,
    }

    # Sets Connext verbosity to help debugging
    verbosity = verbosity_levels.get(args.verbosity, dds.Verbosity.EXCEPTION)

    dds.Logger.instance.verbosity = verbosity

    try: simulator = PlatformSim(args); Path(os.environ["NODE_DEBUG_DIR"], "simulator_ready").touch(); rti.asyncio.run(simulator.run())
        
    except KeyboardInterrupt:
        pass
