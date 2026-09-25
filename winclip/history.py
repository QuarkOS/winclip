"""Clipboard history rules. apply is the only mutation."""

from __future__ import annotations

import hashlib
import sqlite3
import uuid
from dataclasses import dataclass
from pathlib import Path

MAX_ITEMS = 25
MAX_BYTES = 4 * 1024 * 1024
SENSITIVE_MIMES = frozenset({
    "x-kde-passwordManagerHint",
    "x-kde-password",
})
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


class BootId(str):
    """Brand for /proc/sys/kernel/random/boot_id. Not interchangeable with ClipId."""


class ClipId(str):
    """Brand for an item id. Stable across pin, unpin, and duplicate promotion."""


def _chunk(hasher: hashlib._Hash, value: str | bytes | None) -> None:
    if value is None:
        hasher.update(b"\x00")
        return
    data = value.encode("utf-8") if isinstance(value, str) else value
    hasher.update(b"\x01")
    hasher.update(len(data).to_bytes(4, "big"))
    hasher.update(data)


@dataclass(frozen=True, slots=True)
class Payload:
    text: str | None
    html: str | None
    png: bytes | None

    def digest(self) -> bytes:
        """sha256 of a length-prefixed encoding of text, html, and png.

        None and empty bytes hash differently. The digest is not stored.
        """
        hasher = hashlib.sha256()
        _chunk(hasher, self.text)
        _chunk(hasher, self.html)
        _chunk(hasher, self.png)
        return hasher.digest()

    def __post_init__(self) -> None:
        if self.text is None and self.html is None and self.png is None:
            raise ValueError("empty payload")
        if self.png is not None and not self.png.startswith(PNG_MAGIC):
            raise ValueError("png does not start with PNG magic")
        if _size(self.text, self.html, self.png) > MAX_BYTES:
            raise ValueError("payload exceeds MAX_BYTES")


@dataclass(frozen=True, slots=True)
class Item:
    id: ClipId
    payload: Payload


@dataclass(frozen=True, slots=True)
class Record:
    offered_mimes: frozenset[str]
    text: str | None
    html: str | None
    png: bytes | None
    boot_id: BootId


@dataclass(frozen=True, slots=True)
class Promote:
    item_id: ClipId


@dataclass(frozen=True, slots=True)
class Pin:
    item_id: ClipId


@dataclass(frozen=True, slots=True)
class Unpin:
    item_id: ClipId


@dataclass(frozen=True, slots=True)
class Delete:
    item_id: ClipId


@dataclass(frozen=True, slots=True)
class ClearUnpinned:
    pass


@dataclass(frozen=True, slots=True)
class Enable:
    pass


@dataclass(frozen=True, slots=True)
class ForBoot:
    boot_id: BootId


Boot = ForBoot

Command = Record | Promote | Pin | Unpin | Delete | ClearUnpinned | Enable | ForBoot


@dataclass(frozen=True, slots=True)
class Effect:
    publish: Payload | None = None


@dataclass(frozen=True, slots=True)
class Row:
    id: ClipId
    pinned: bool
    text: str | None
    png: bytes | None


class StorageError(Exception):
    """The database schema is not version 1. Callers must not overwrite it."""


def _size(text: str | None, html: str | None, png: bytes | None) -> int:
    total = 0
    if text is not None:
        total += len(text.encode("utf-8"))
    if html is not None:
        total += len(html.encode("utf-8"))
    if png is not None:
        total += len(png)
    return total


def _blank(value: str | None) -> str | None:
    if value is None or value.strip() == "":
        return None
    return value


def _payload_from_parts(
    text: str | None,
    html: str | None,
    png: bytes | None,
) -> Payload | None:
    text = _blank(text)
    html = _blank(html)
    if png is not None and len(png) == 0:
        png = None
    if text is None and html is None and png is None:
        return None
    if png is not None and not png.startswith(PNG_MAGIC):
        return None
    if _size(text, html, png) > MAX_BYTES:
        return None
    return Payload(text, html, png)


def _normalized(record: Record) -> Payload | None:
    if SENSITIVE_MIMES.intersection(record.offered_mimes):
        return None
    return _payload_from_parts(record.text, record.html, record.png)


@dataclass(frozen=True, slots=True)
class History:
    enabled: bool
    boot_id: BootId
    pinned: tuple[Item, ...]
    recent: tuple[Item, ...]

    def __post_init__(self) -> None:
        if not self.enabled and (self.pinned or self.recent):
            raise ValueError("disabled history holds items")
        if len(self.pinned) + len(self.recent) > MAX_ITEMS:
            raise ValueError("history exceeds MAX_ITEMS")
        ids = [item.id for item in self.items]
        if len(ids) != len(set(ids)):
            raise ValueError("repeated ClipId")
        digests = [item.payload.digest() for item in self.items]
        if len(digests) != len(set(digests)):
            raise ValueError("repeated payload digest")

    @property
    def items(self) -> tuple[Item, ...]:
        return self.pinned + self.recent

    def apply(self, command: Command) -> tuple[History, Effect]:
        """Apply one command. The same end state returns this history."""
        if isinstance(command, Enable):
            if self.enabled:
                return self, Effect()
            return History(True, self.boot_id, (), ()), Effect()
        if isinstance(command, ForBoot):
            if command.boot_id == self.boot_id:
                return self, Effect()
            return History(self.enabled, command.boot_id, self.pinned, ()), Effect()
        if isinstance(command, ClearUnpinned):
            if not self.recent:
                return self, Effect()
            return History(self.enabled, self.boot_id, self.pinned, ()), Effect()
        if isinstance(command, Delete):
            return self._delete(command.item_id)
        if isinstance(command, Pin):
            return self._pin(command.item_id)
        if isinstance(command, Unpin):
            return self._unpin(command.item_id)
        if isinstance(command, Promote):
            return self._promote(command.item_id)
        if isinstance(command, Record):
            return self._record(command)
        raise AssertionError(f"unhandled command {type(command).__name__}")

    def stored_digest(self, record: Record) -> bytes | None:
        """Digest apply would store, or None when apply would reject the record."""
        if not self.enabled:
            return None
        if self.boot_id and record.boot_id != self.boot_id:
            return None
        payload = _normalized(record)
        if payload is None:
            return None
        return payload.digest()

    def _record(self, command: Record) -> tuple[History, Effect]:
        if not self.enabled:
            return self, Effect()
        # A stale boot must not refill recent. Expiry is one id on the history.
        if self.boot_id and command.boot_id != self.boot_id:
            return self, Effect()
        payload = _normalized(command)
        if payload is None:
            return self, Effect()
        digest = payload.digest()
        boot_id = self.boot_id or command.boot_id
        found = _take(self.pinned, digest)
        if found is not None:
            item, rest = found
            pinned = (Item(item.id, payload),) + rest
            if pinned == self.pinned and boot_id == self.boot_id:
                return self, Effect()
            return History(self.enabled, boot_id, pinned, self.recent), Effect()
        found = _take(self.recent, digest)
        if found is not None:
            item, rest = found
            recent = (Item(item.id, payload),) + rest
            if recent == self.recent and boot_id == self.boot_id:
                return self, Effect()
            return History(self.enabled, boot_id, self.pinned, recent), Effect()
        if len(self.pinned) >= MAX_ITEMS:
            return self, Effect()
        recent = (Item(ClipId(uuid.uuid4().hex), payload),) + self.recent
        while len(self.pinned) + len(recent) > MAX_ITEMS and recent:
            recent = recent[:-1]
        return History(self.enabled, boot_id, self.pinned, recent), Effect()

    def _pin(self, item_id: ClipId) -> tuple[History, Effect]:
        if any(item.id == item_id for item in self.pinned):
            return self, Effect()
        found = _take_id(self.recent, item_id)
        if found is None:
            return self, Effect()
        item, rest = found
        return History(self.enabled, self.boot_id, (item,) + self.pinned, rest), Effect()

    def _unpin(self, item_id: ClipId) -> tuple[History, Effect]:
        if any(item.id == item_id for item in self.recent):
            return self, Effect()
        found = _take_id(self.pinned, item_id)
        if found is None:
            return self, Effect()
        item, rest = found
        return History(self.enabled, self.boot_id, rest, (item,) + self.recent), Effect()

    def _delete(self, item_id: ClipId) -> tuple[History, Effect]:
        found = _take_id(self.pinned, item_id)
        if found is not None:
            _item, rest = found
            return History(self.enabled, self.boot_id, rest, self.recent), Effect()
        found = _take_id(self.recent, item_id)
        if found is None:
            return self, Effect()
        _item, rest = found
        return History(self.enabled, self.boot_id, self.pinned, rest), Effect()

    def _promote(self, item_id: ClipId) -> tuple[History, Effect]:
        found = _take_id(self.pinned, item_id)
        if found is not None:
            item, rest = found
            pinned = (item,) + rest
            history = self if pinned == self.pinned else History(
                self.enabled, self.boot_id, pinned, self.recent
            )
            return history, Effect(item.payload)
        found = _take_id(self.recent, item_id)
        if found is None:
            return self, Effect()
        item, rest = found
        recent = (item,) + rest
        history = self if recent == self.recent else History(
            self.enabled, self.boot_id, self.pinned, recent
        )
        return history, Effect(item.payload)


def _take(items: tuple[Item, ...], digest: bytes) -> tuple[Item, tuple[Item, ...]] | None:
    for index, item in enumerate(items):
        if item.payload.digest() == digest:
            return item, items[:index] + items[index + 1 :]
    return None


def _take_id(items: tuple[Item, ...], item_id: ClipId) -> tuple[Item, tuple[Item, ...]] | None:
    for index, item in enumerate(items):
        if item.id == item_id:
            return item, items[:index] + items[index + 1 :]
    return None


def rows(history: History) -> tuple[Row, ...]:
    """Project items for the panel. png is set only when text is missing."""
    projected: list[Row] = []
    for item in history.pinned:
        projected.append(_row(item, True))
    for item in history.recent:
        projected.append(_row(item, False))
    return tuple(projected)


def _row(item: Item, pinned: bool) -> Row:
    text = item.payload.text
    png = None if text is not None else item.payload.png
    return Row(item.id, pinned, text, png)


def load(path: Path) -> History:
    """Read history.sqlite. A missing file is a disabled empty history."""
    if not path.exists():
        return History(False, BootId(""), (), ())
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise StorageError(str(exc)) from exc
    try:
        try:
            schema = conn.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()
            enabled_row = conn.execute(
                "SELECT value FROM meta WHERE key = 'enabled'"
            ).fetchone()
            boot_row = conn.execute(
                "SELECT value FROM meta WHERE key = 'boot_id'"
            ).fetchone()
        except sqlite3.Error as exc:
            raise StorageError(str(exc)) from exc
        if schema is None or schema[0] != "1":
            raise StorageError("schema is not 1")
        boot_id = BootId(boot_row[0]) if boot_row else BootId("")
        if enabled_row is None or enabled_row[0] != "1":
            return History(False, boot_id, (), ())
        try:
            pinned_rows = conn.execute(
                "SELECT id, text, html, png FROM item WHERE bucket = 'pinned' "
                "ORDER BY position"
            ).fetchall()
            recent_rows = conn.execute(
                "SELECT id, text, html, png FROM item WHERE bucket = 'recent' "
                "ORDER BY position"
            ).fetchall()
        except sqlite3.Error as exc:
            raise StorageError(str(exc)) from exc
    finally:
        conn.close()
    pinned = _items_from_rows(pinned_rows)
    recent = _items_from_rows(recent_rows)
    pinned, recent = _drop_duplicate_digests(pinned, recent)
    while len(pinned) + len(recent) > MAX_ITEMS and recent:
        recent = recent[:-1]
    while len(pinned) + len(recent) > MAX_ITEMS and pinned:
        pinned = pinned[:-1]
    return History(True, boot_id, pinned, recent)


def _items_from_rows(sql_rows: list[tuple]) -> tuple[Item, ...]:
    items: list[Item] = []
    for item_id, text, html, png in sql_rows:
        if isinstance(png, memoryview):
            png = bytes(png)
        payload = _payload_from_parts(text, html, png)
        if payload is None:
            continue
        items.append(Item(ClipId(item_id), payload))
    return tuple(items)


def _drop_duplicate_digests(
    pinned: tuple[Item, ...],
    recent: tuple[Item, ...],
) -> tuple[tuple[Item, ...], tuple[Item, ...]]:
    seen: set[bytes] = set()
    kept_pinned: list[Item] = []
    for item in pinned:
        digest = item.payload.digest()
        if digest in seen:
            continue
        seen.add(digest)
        kept_pinned.append(item)
    kept_recent: list[Item] = []
    for item in recent:
        digest = item.payload.digest()
        if digest in seen:
            continue
        seen.add(digest)
        kept_recent.append(item)
    return tuple(kept_pinned), tuple(kept_recent)


def save(history: History, path: Path) -> None:
    """Replace the database contents in one transaction."""
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    path.parent.chmod(0o700)
    conn = sqlite3.connect(path)
    try:
        path.chmod(0o600)
        conn.execute("PRAGMA journal_mode=DELETE")
        conn.execute("BEGIN")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS meta ("
            "key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS item ("
            "id TEXT PRIMARY KEY, bucket TEXT NOT NULL, position INTEGER NOT NULL, "
            "text TEXT, html TEXT, png BLOB)"
        )
        conn.execute("DELETE FROM meta")
        conn.execute("DELETE FROM item")
        conn.execute("INSERT INTO meta (key, value) VALUES ('schema', '1')")
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('enabled', ?)",
            ("1" if history.enabled else "0",),
        )
        conn.execute(
            "INSERT INTO meta (key, value) VALUES ('boot_id', ?)",
            (str(history.boot_id),),
        )
        if history.enabled:
            for bucket, items in (("pinned", history.pinned), ("recent", history.recent)):
                for position, item in enumerate(items):
                    conn.execute(
                        "INSERT INTO item (id, bucket, position, text, html, png) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            str(item.id),
                            bucket,
                            position,
                            item.payload.text,
                            item.payload.html,
                            item.payload.png,
                        ),
                    )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def read_boot_id(path: str = "/proc/sys/kernel/random/boot_id") -> BootId:
    """Read the kernel boot id. Strip whitespace. This is the reboot key."""
    return BootId(Path(path).read_text(encoding="ascii").strip())
