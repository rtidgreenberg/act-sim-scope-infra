import unittest

from policy import Policy


class PolicyTests(unittest.TestCase):
    def test_user_override_survives_mission_changes_and_auto_restores_current_phase(self):
        active = Policy().transition("ORBIT")
        user = active.select(dict(session=1, revision=1, mode="User",
                                  topics=["ContactReport"]))
        self.assertEqual(user.selected(), ["ContactReport", "PlatformStatus"])
        idle = user.transition("CANCELLED")
        self.assertEqual(idle.selected(), ["ContactReport"])
        auto = idle.select(dict(session=1, revision=3, mode="Auto", topics=[]))
        self.assertEqual(auto.selected(), [])

    def test_active_navigation_is_protected_even_for_empty_user_selection(self):
        user = Policy().select(dict(session=1, revision=0, mode="User", topics=[]))
        self.assertEqual(user.transition("TRANSIT").selected(), ["PlatformStatus"])

    def test_reject_invalid_requests_without_mutating_policy(self):
        policy = Policy()
        request = dict(session=1, revision=0, mode="User", topics=["ContactReport"])
        for change in (dict(session=0), dict(revision=2), dict(topics=["Unknown"]),
                       dict(topics=["ContactReport"] * 2), dict(mode="Unknown"),
                       dict(mode="Auto")):
            with self.subTest(change=change), self.assertRaises(ValueError):
                policy.select(request | change)
        self.assertEqual(policy.revision, 0)
        self.assertEqual(policy.selected(), [])
