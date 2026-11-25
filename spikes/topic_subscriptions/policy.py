"""Bounded fixture policy; not the production GatewayModel implementation."""

from dataclasses import dataclass, replace


TOPICS = {"ContactReport": "State", "PlatformMissionStatus": "Periodic",
          "PlatformStatus": "Periodic"}
ACTIVE = {"TRANSIT", "ORBIT", "RTB", "HOLDING"}
PHASES = ACTIVE | {"IDLE", "COMPLETE", "CANCELLED"}
EXPRESSION = (
    "NOT (source = %4) AND destination = %5 AND "
    "((original_topic = 'Inventory' OR original_topic = 'Applied' OR "
    "original_topic = 'SubscriptionRequest' OR original_topic = 'SubscriptionAck' OR "
    "original_topic = 'Mission' OR original_topic = 'Team') OR "
    "((original_topic = %0 OR original_topic = %1 OR original_topic = %2) AND "
    "(team = '' OR team = %3)))")


@dataclass(frozen=True)
class Policy:
    session: int = 1
    revision: int = 0
    mission: str = "IDLE"
    mode: str = "Auto"
    user_topics: tuple = ()
    team: str = "TEAM_A"

    def selected(self):
        topics = set(TOPICS) if self.mode == "Auto" and self.mission in ACTIVE else (
            set(self.user_topics) if self.mode == "User" else set())
        if self.mission in ACTIVE:
            topics.add("PlatformStatus")
        return sorted(topics)

    def parameters(self):
        names = self.selected() + ["__not_selected__"] * (3 - len(self.selected()))
        return tuple(names + [self.team, "B", "B"])

    def select(self, request):
        if request["session"] != self.session:
            raise ValueError("stale router session")
        if request["revision"] != self.revision:
            raise ValueError("conflicting subscription revision")
        topics = tuple(request["topics"])
        if len(topics) > 3 or len(set(topics)) != len(topics):
            raise ValueError("duplicate or oversized topic selection")
        if any(topic not in TOPICS for topic in topics):
            raise ValueError("unsupported topic")
        if request["mode"] not in {"Auto", "User"}:
            raise ValueError("unsupported selection mode")
        if request["mode"] == "Auto" and topics:
            raise ValueError("Auto must not carry a user topic selection")
        return replace(self, revision=self.revision + 1, mode=request["mode"],
                       user_topics=topics)

    def transition(self, phase):
        if phase not in PHASES:
            raise ValueError("unsupported mission phase")
        return replace(self, revision=self.revision + 1, mission=phase)

    def change_team(self, team):
        if team not in {"TEAM_A", "TEAM_B", ""}:
            raise ValueError("unsupported fixture team")
        return replace(self, revision=self.revision + 1, team=team)
