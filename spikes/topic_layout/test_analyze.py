import unittest

from analyze import summarize


class AccountingTests(unittest.TestCase):
    def test_bundled_records_count_packet_bytes_once(self):
        prefix = "01" * 12
        rows = [dict(kind="DATA", writer="000004c2", prefix=prefix, sequence=sequence)
                for sequence in (10, 11)]
        packet = dict(time=2.0, ip_bytes=900, discovery=True,
                      payload=b"RTPS" + bytes(4) + bytes.fromhex(prefix), rows=rows)
        result = summarize([packet], dict(start=1, end=3))
        self.assertEqual(result["discovery_ip_bytes"], 900)
        self.assertEqual(result["sedp_ip_bytes"], 900)
        self.assertEqual(result["unique_subscription_records"], 2)

    def test_repeated_record_to_multiple_peers_counts_wire_bytes_but_not_new_records(self):
        prefix = "02" * 12
        record = dict(kind="DATA", writer="000004c2", prefix=prefix, sequence=7)
        packet = dict(time=2.0, ip_bytes=500, discovery=True,
                      payload=b"RTPS" + bytes(4) + bytes.fromhex(prefix), rows=[record])
        values = [packet, packet, dict(packet, time=3.0)]
        result = summarize(values, dict(start=1, end=3), prefix)
        self.assertEqual(result["discovery_ip_bytes"], 1000)
        self.assertEqual(result["unique_subscription_records"], 1)
        self.assertEqual(summarize(values, dict(start=1, end=3), "03" * 12)["packets"], 0)


if __name__ == "__main__":
    unittest.main()
