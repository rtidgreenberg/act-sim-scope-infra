import struct
import unittest

from wire import decode, is_builtin_writer


def packet(kind, body, flags=1):
    endian = "<" if flags & 1 else ">"
    return (b"RTPS\x02\x05\x01\x01" + bytes(range(12)) +
            bytes([kind, flags]) + struct.pack(endian + "H", len(body)) + body)


class WireTests(unittest.TestCase):
    def test_builtin_writer_includes_vendor_specific_spdp2(self):
        for entity in ("000003c2", "000004c2", "00010182", "00020082", "000300c3"):
            self.assertTrue(is_builtin_writer(entity), entity)
        for entity in ("80000002", "80002102", "00000103"):
            self.assertFalse(is_builtin_writer(entity), entity)

    def test_initial_zero_base_and_final_positive_ack(self):
        body = bytes.fromhex("8000000780000002") + struct.pack("<iIII", 0, 0, 0, 1)
        initial = decode(packet(6, body), 1)[0]
        self.assertEqual((initial["base"], initial["num_bits"], initial["missing"],
                          initial["final"], initial["count"]), (0, 0, [], False, 1))
        body = bytes.fromhex("8000000780000002") + struct.pack(">iIII", 0, 9, 0, 5)
        positive = decode(packet(6, body, flags=2), 2)[0]
        self.assertEqual((positive["base"], positive["missing"], positive["final"]),
                         (9, [], True))

    def test_repair_bitmap_uses_most_significant_bits(self):
        body = (bytes.fromhex("8000000780000002") +
                struct.pack("<iIIIII", 0, 2, 33, 0x80000000, 0x80000000, 3))
        row = decode(packet(6, body), 1)[0]
        self.assertEqual(row["missing"], [2, 34])
        self.assertEqual(row["count"], 3)

    def test_session_sequence_offsets_and_heartbeat(self):
        data = bytes.fromhex("000010000000000080000002") + struct.pack("<iIiI", 0, 7, 0, 2)
        self.assertEqual(decode(packet(0x14, data), 1)[0]["sequence"], 2)
        heartbeat = (bytes.fromhex("8000000780000002") +
                     struct.pack("<iIiIiIiII", 0, 1, 0, 8, 0, 2, 0, 8, 9))
        row = decode(packet(0x1b, heartbeat), 2)[0]
        self.assertEqual((row["first"], row["last"], row["first_batch"],
                          row["last_batch"], row["count"]), (2, 8, 1, 8, 9))

    def test_rejects_truncated_bitmap(self):
        body = bytes.fromhex("8000000780000002") + struct.pack("<iIII", 0, 2, 33, 0)
        with self.assertRaises(ValueError):
            decode(packet(6, body), 1)


if __name__ == "__main__":
    unittest.main()
