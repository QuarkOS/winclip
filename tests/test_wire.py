"""Frame bytes. No display."""

import struct
import unittest

from winclip.wire import MAX_FRAME, Focus, Hello, MarkFocus, Offer, Paste, Place, Toggle, encode, feed


class WireTests(unittest.TestCase):
    def test_empty_messages(self) -> None:
        self.assertEqual(encode(Hello()), b"\x00\x00\x00\x00\x01")
        self.assertEqual(encode(Toggle()), b"\x00\x00\x00\x00\x02")
        self.assertEqual(encode(MarkFocus()), b"\x00\x00\x00\x00\x04")

    def test_focus_and_paste_are_raw_utf8(self) -> None:
        self.assertEqual(encode(Focus("x")), b"\x00\x00\x00\x01\x05x")
        self.assertEqual(encode(Paste("ctrl-v")), b"\x00\x00\x00\x06\x06ctrl-v")
        self.assertEqual(encode(Paste("ctrl-shift-v")), b"\x00\x00\x00\x0c\x06ctrl-shift-v")

    def test_place_is_two_i32be(self) -> None:
        frame = encode(Place(-8, 100))
        self.assertEqual(frame, b"\x00\x00\x00\x08\x07" + struct.pack(">ii", -8, 100))
        messages, rest = feed(frame)
        self.assertEqual(messages, (Place(-8, 100),))
        self.assertEqual(rest, b"")

    def test_offer_carries_empty_sensitive_hint(self) -> None:
        message = Offer(
            (
                ("text/plain", b"hi"),
                ("x-kde-passwordManagerHint", b""),
            )
        )
        mime_text = b"text/plain"
        mime_hint = b"x-kde-passwordManagerHint"
        payload = struct.pack(">I", 2)
        payload += struct.pack(">H", len(mime_text)) + mime_text
        payload += struct.pack(">I", len(b"hi")) + b"hi"
        payload += struct.pack(">H", len(mime_hint)) + mime_hint
        payload += struct.pack(">I", 0)
        frame = struct.pack(">I", len(payload)) + bytes((3,)) + payload
        self.assertEqual(encode(message), frame)
        messages, rest = feed(frame + b"\x00")
        self.assertEqual(messages, (message,))
        self.assertEqual(rest, b"\x00")

    def test_partial_frame_waits(self) -> None:
        frame = encode(Toggle()) + encode(Hello())
        messages, rest = feed(frame[:3])
        self.assertEqual(messages, ())
        self.assertEqual(rest, frame[:3])
        messages, rest = feed(rest + frame[3:])
        self.assertEqual(messages, (Toggle(), Hello()))
        self.assertEqual(rest, b"")

    def test_oversize_and_unknown_type(self) -> None:
        oversize = (MAX_FRAME + 1).to_bytes(4, "big") + b"\x01"
        with self.assertRaises(ValueError):
            feed(oversize)
        with self.assertRaises(ValueError):
            feed(b"\x00\x00\x00\x00\x09")


if __name__ == "__main__":
    unittest.main()
