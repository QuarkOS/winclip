"""Length-prefixed frames shared with the shell extension."""

from __future__ import annotations

import struct
from dataclasses import dataclass

MAX_FRAME = 12 * 1024 * 1024

HELLO = 1
TOGGLE = 2
OFFER = 3
MARK_FOCUS = 4
FOCUS = 5
PASTE = 6
PLACE = 7


@dataclass(frozen=True, slots=True)
class Hello:
    pass


@dataclass(frozen=True, slots=True)
class Toggle:
    pass


@dataclass(frozen=True, slots=True)
class Offer:
    parts: tuple[tuple[str, bytes], ...]


@dataclass(frozen=True, slots=True)
class MarkFocus:
    pass


@dataclass(frozen=True, slots=True)
class Focus:
    wm_class: str


@dataclass(frozen=True, slots=True)
class Paste:
    chord: str


@dataclass(frozen=True, slots=True)
class Place:
    x: int
    y: int


Message = Hello | Toggle | Offer | MarkFocus | Focus | Paste | Place

_EMPTY = {
    HELLO: Hello,
    TOGGLE: Toggle,
    MARK_FOCUS: MarkFocus,
}


def encode(message: Message) -> bytes:
    """5 byte header plus payload. Header is u32be length, then u8 type."""
    kind, payload = _encode_payload(message)
    if len(payload) > MAX_FRAME:
        raise ValueError("declared length over MAX_FRAME")
    return len(payload).to_bytes(4, "big") + bytes((kind,)) + payload


def feed(buffer: bytes) -> tuple[tuple[Message, ...], bytes]:
    """Take complete frames out of buffer. Return the leftover bytes."""
    messages: list[Message] = []
    while len(buffer) >= 5:
        length = int.from_bytes(buffer[:4], "big")
        if length > MAX_FRAME:
            raise ValueError("declared length over MAX_FRAME")
        if len(buffer) < 5 + length:
            break
        kind = buffer[4]
        payload = buffer[5 : 5 + length]
        messages.append(_decode(kind, payload))
        buffer = buffer[5 + length :]
    return tuple(messages), buffer


def _encode_payload(message: Message) -> tuple[int, bytes]:
    if isinstance(message, Hello):
        return HELLO, b""
    if isinstance(message, Toggle):
        return TOGGLE, b""
    if isinstance(message, MarkFocus):
        return MARK_FOCUS, b""
    if isinstance(message, Focus):
        return FOCUS, message.wm_class.encode("utf-8")
    if isinstance(message, Paste):
        return PASTE, message.chord.encode("utf-8")
    if isinstance(message, Place):
        return PLACE, struct.pack(">ii", message.x, message.y)
    if isinstance(message, Offer):
        return OFFER, _encode_offer(message.parts)
    raise ValueError("unknown message")


def _encode_offer(parts: tuple[tuple[str, bytes], ...]) -> bytes:
    chunks = [len(parts).to_bytes(4, "big")]
    for mime, data in parts:
        raw = mime.encode("utf-8")
        if len(raw) > 0xFFFF:
            raise ValueError("mime name too long")
        chunks.append(len(raw).to_bytes(2, "big"))
        chunks.append(raw)
        chunks.append(len(data).to_bytes(4, "big"))
        chunks.append(data)
    return b"".join(chunks)


def _decode(kind: int, payload: bytes) -> Message:
    if kind in _EMPTY:
        if payload:
            raise ValueError("empty message carried a payload")
        return _EMPTY[kind]()
    if kind == FOCUS:
        return Focus(_utf8(payload))
    if kind == PASTE:
        return Paste(_utf8(payload))
    if kind == PLACE:
        if len(payload) != 8:
            raise ValueError("place payload")
        x, y = struct.unpack(">ii", payload)
        return Place(x, y)
    if kind == OFFER:
        return Offer(_decode_offer(payload))
    raise ValueError("unknown type")


def _utf8(payload: bytes) -> str:
    try:
        return payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("payload is not utf-8") from exc


def _decode_offer(payload: bytes) -> tuple[tuple[str, bytes], ...]:
    if len(payload) < 4:
        raise ValueError("offer payload")
    count = int.from_bytes(payload[:4], "big")
    offset = 4
    parts: list[tuple[str, bytes]] = []
    for _ in range(count):
        if offset + 2 > len(payload):
            raise ValueError("offer payload")
        mime_len = int.from_bytes(payload[offset : offset + 2], "big")
        offset += 2
        if offset + mime_len + 4 > len(payload):
            raise ValueError("offer payload")
        mime = _utf8(payload[offset : offset + mime_len])
        offset += mime_len
        data_len = int.from_bytes(payload[offset : offset + 4], "big")
        offset += 4
        if offset + data_len > len(payload):
            raise ValueError("offer payload")
        data = payload[offset : offset + data_len]
        offset += data_len
        parts.append((mime, data))
    if offset != len(payload):
        raise ValueError("offer payload")
    return tuple(parts)
