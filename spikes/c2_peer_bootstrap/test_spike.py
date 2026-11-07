import unittest

from spike import DIRECTORY, discovered, reconcile_peers, roundtrip


class PeerReconciliationTests(unittest.TestCase):
    def test_duplicate_directories_add_each_peer_once(self):
        configured, calls = {}, []
        for _ in range(4):
            reconcile_peers(DIRECTORY, configured, calls.append)
        self.assertEqual(calls, [entry["peer"] for entry in DIRECTORY])
        self.assertEqual(configured, {entry["name"]: entry["peer"] for entry in DIRECTORY})

    def test_failed_add_is_not_recorded_and_can_be_retried(self):
        configured = {}

        def reject(peer):
            raise RuntimeError(f"add rejected: {peer}")

        with self.assertRaisesRegex(RuntimeError, "add rejected"):
            reconcile_peers(DIRECTORY, configured, reject)
        self.assertEqual(configured, {})
        reconcile_peers(DIRECTORY, configured, lambda peer: None)
        self.assertEqual(len(configured), len(DIRECTORY))

    def test_changed_locator_is_explicitly_out_of_scope(self):
        configured = {"Open": "old"}
        with self.assertRaisesRegex(ValueError, "locator changes"):
            reconcile_peers(DIRECTORY, configured, lambda peer: None)
        self.assertEqual(configured, {"Open": "old"})

    def test_discovery_matches_identity_and_full_configuration(self):
        snapshot = {"discovered": [
            {"name": "Other", "partial_configuration": False},
            {"name": "Open", "partial_configuration": True},
            {"name": "Open", "partial_configuration": False, "handle": "guid"},
        ]}
        self.assertEqual(discovered(snapshot, "Open"), [
            {"name": "Open", "partial_configuration": False, "handle": "guid"}])
        self.assertEqual(discovered(snapshot, "Missing"), [])

    def test_roundtrip_matches_tuple_ack_received_over_process_pipe(self):
        class Client:
            def request(self, operation, **fields):
                self.sent = (operation, fields)

            def snapshot(self):
                return {"acks": [("Open", 10)]}

        client = Client()
        result = roundtrip(client, "Open", 10)
        self.assertEqual(result["evidence"]["acks"], [("Open", 10)])
        self.assertEqual(client.sent, ("probe", {"target": "Open", "sequence": 10}))


if __name__ == "__main__":
    unittest.main()
