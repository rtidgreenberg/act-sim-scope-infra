"""Historical, successfully applied platform receive selections for delivery audits."""

from bisect import bisect_right


class ReceivePolicyHistory:
    def __init__(self):
        self.records = {}

    def add(self, event):
        run, node = event.get("run_id"), event.get("node")
        timestamp = event.get("sent_at_ns")
        profile, teams = event.get("receive_profile"), event.get("teams")
        if (not isinstance(run, str) or not run or not isinstance(node, str) or not node
                or type(timestamp) is not int or timestamp <= 0
                or profile not in ("Init", "ActiveMission")
                or not isinstance(teams, list) or not all(isinstance(team, str) for team in teams)):
            raise ValueError(f"invalid receive-policy audit record for {node}")
        state = {"receive_profile": profile, "teams": tuple(teams)}
        records = self.records.setdefault((run, node), [])
        index = bisect_right([item[0] for item in records], timestamp)
        if index and records[index - 1][0] == timestamp:
            if records[index - 1][1] != state:
                raise ValueError(f"conflicting receive-policy audit records for {node} at {timestamp}")
            return
        records.insert(index, (timestamp, state))

    def has_node(self, run, node):
        return (run, node) in self.records

    def state_at(self, run, node, timestamp):
        records = self.records.get((run, node), [])
        if not records or records[0][1]["receive_profile"] != "Init":
            raise ValueError(f"missing initial receive-policy audit record for {node} in run {run}")
        if type(timestamp) is not int or timestamp <= 0:
            raise ValueError(f"missing valid send timestamp for receive-policy audit of {node}")
        index = bisect_right([item[0] for item in records], timestamp)
        # A platform that has not started yet cannot receive navigation.
        return records[index - 1][1] if index else {"receive_profile": "Init", "teams": ()}

    def prune(self, earliest_ns, run):
        for key, records in list(self.records.items()):
            if key[0] != run:
                del self.records[key]
                continue
            index = bisect_right([item[0] for item in records], earliest_ns)
            # Keep startup evidence and the last selection before the rolling window.
            if index > 2:
                del records[1:index - 1]
