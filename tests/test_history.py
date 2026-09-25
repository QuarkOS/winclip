"""History rules with no display."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from winclip.history import (
    MAX_BYTES,
    PNG_MAGIC,
    BootId,
    ClearUnpinned,
    Delete,
    Enable,
    ForBoot,
    Pin,
    Record,
    Unpin,
    load,
    save,
)

PNG = (
    PNG_MAGIC
    + b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00\x90wS\xde"
    + b"\x00\x00\x00\x0cIDATx\x9cc\xf8\xcf\xc0\x00\x00\x00\x03\x00\x01\x00\x05\xfe\x02\xfe"
    + b"\x00\x00\x00\x00IEND\xaeB`\x82"
)


class HistoryTests(unittest.TestCase):
    def test_rules_hold_without_a_display(self) -> None:
        missing = Path(tempfile.mkdtemp()) / "missing.sqlite"
        history = load(missing)
        self.assertEqual((history.enabled, history.pinned, history.recent), (False, (), ()))

        history, effect = history.apply(Enable())
        self.assertEqual((history.enabled, effect.publish), (True, None))

        boot_a = BootId("boot-a")
        boot_b = BootId("boot-b")
        history, _ = history.apply(Record(frozenset(), "alpha", None, None, boot_a))
        history, _ = history.apply(Record(frozenset(), "beta", "<b>beta</b>", None, boot_a))
        beta_id = history.recent[0].id
        alpha_id = history.recent[1].id

        history, _ = history.apply(Pin(beta_id))
        self.assertEqual(history.pinned[0].payload.html, "<b>beta</b>")
        self.assertEqual([item.id for item in history.recent], [alpha_id])

        history, _ = history.apply(
            Record(frozenset({"x-kde-passwordManagerHint"}), "secret", None, None, boot_a)
        )
        self.assertEqual([item.payload.text for item in history.items], ["beta", "alpha"])

        history, _ = history.apply(Record(frozenset(), "alpha", None, None, boot_a))
        self.assertEqual(history.recent[0].id, alpha_id)

        history, _ = history.apply(Record(frozenset(), "beta", "<b>beta</b>", None, boot_a))
        self.assertEqual(history.pinned[0].id, beta_id)

        history, _ = history.apply(ClearUnpinned())
        self.assertEqual([item.payload.text for item in history.items], ["beta"])

        history, _ = history.apply(ForBoot(boot_b))
        self.assertEqual([item.id for item in history.pinned], [beta_id])
        self.assertEqual(history.boot_id, boot_b)
        self.assertEqual(history.recent, ())

        same, _ = history.apply(ForBoot(boot_b))
        self.assertIs(same, history)

        history, _ = history.apply(Unpin(beta_id))
        history, _ = history.apply(Record(frozenset(), "old", None, None, boot_a))
        history, _ = history.apply(ForBoot(boot_b))
        self.assertEqual([item.payload.text for item in history.recent], ["beta"])

        history, _ = history.apply(Delete(beta_id))
        self.assertEqual(history.items, ())

    def test_cap_keeps_pins_and_drops_oversize(self) -> None:
        history, _ = load(Path(tempfile.mkdtemp()) / "missing.sqlite").apply(Enable())
        boot = BootId("boot")
        for n in range(30):
            history, _ = history.apply(Record(frozenset(), f"item-{n}", None, None, boot))
        self.assertEqual(
            [item.payload.text for item in history.recent],
            [f"item-{n}" for n in range(29, 4, -1)],
        )
        oldest = history.recent[-1].id
        history, _ = history.apply(Pin(oldest))
        history, _ = history.apply(Record(frozenset(), "newer", None, None, boot))
        self.assertEqual(history.pinned[0].payload.text, "item-5")
        self.assertEqual(history.recent[0].payload.text, "newer")
        self.assertEqual(len(history.items), 25)

        huge = "x" * (MAX_BYTES + 1)
        stayed, _ = history.apply(Record(frozenset(), huge, None, None, boot))
        self.assertEqual(stayed.recent[0].payload.text, "newer")

        while len(history.pinned) < 25:
            history, _ = history.apply(
                Record(frozenset(), f"p-{len(history.pinned)}", None, None, boot)
            )
            history, _ = history.apply(Pin(history.recent[0].id))
        blocked, _ = history.apply(Record(frozenset(), "overflow", None, None, boot))
        self.assertEqual(len(blocked.pinned), 25)
        self.assertEqual(blocked.recent, ())

    def test_load_roundtrip_text_html_and_png(self) -> None:
        path = Path(tempfile.mkdtemp()) / "history.sqlite"
        boot = BootId("boot-png")
        history, _ = load(path).apply(Enable())
        history, _ = history.apply(Record(frozenset(), "plain", None, None, boot))
        history, _ = history.apply(Record(frozenset(), "rich", "<b>rich</b>", None, boot))
        history, _ = history.apply(Record(frozenset(), None, None, PNG, boot))
        save(history, path)
        loaded = load(path)
        self.assertTrue(loaded.enabled)
        self.assertEqual(loaded.boot_id, boot)
        self.assertEqual(loaded.recent[0].payload.png, PNG)
        self.assertIsNone(loaded.recent[0].payload.text)
        self.assertEqual(loaded.recent[1].payload.html, "<b>rich</b>")
        self.assertEqual(loaded.recent[1].payload.text, "rich")
        self.assertEqual(loaded.recent[2].payload.text, "plain")
        self.assertEqual(loaded.recent[0].id, history.recent[0].id)

    def test_disabled_save_ignores_leftover_rows(self) -> None:
        path = Path(tempfile.mkdtemp()) / "history.sqlite"
        boot = BootId("boot")
        history, _ = load(path).apply(Enable())
        history, _ = history.apply(Record(frozenset(), "kept-out", None, None, boot))
        save(history, path)
        conn = sqlite3.connect(path)
        conn.execute("UPDATE meta SET value = '0' WHERE key = 'enabled'")
        conn.commit()
        conn.close()
        loaded = load(path)
        self.assertFalse(loaded.enabled)
        self.assertEqual(loaded.items, ())

    def test_bad_schema_is_not_overwritten(self) -> None:
        path = Path(tempfile.mkdtemp()) / "history.sqlite"
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        conn.execute("INSERT INTO meta VALUES ('schema', '2')")
        conn.commit()
        conn.close()
        with self.assertRaises(Exception) as caught:
            load(path)
        self.assertEqual(type(caught.exception).__name__, "StorageError")


if __name__ == "__main__":
    unittest.main()
