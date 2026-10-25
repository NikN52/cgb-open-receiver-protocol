"""Shared, bounded wire primitives for the public reference tools (stdlib only).

This is an independent implementation of the specification. It contains no
bridge code. A checked control sentence is never a navigation measurement.
"""

from __future__ import annotations

import datetime as dt
import ipaddress
import math
import re
import time
from dataclasses import dataclass
from urllib.parse import unquote_to_bytes

MAX_LINE = 512
MAX_UBX = 4096
TCP_PORT = 28495
DISCOVERY_PORT = 28496
PUSH_PORT = 28497
CAPS = frozenset("nmea ubx_rx imu_pcgbi wheel batt time udp_push".split())
RESERVED_CAPS = frozenset(("rtcm_in", "cmd_pair"))


class ProtocolError(ValueError):
    def __init__(self, rule: str, message: str):
        super().__init__(message)
        self.rule = rule


def checksum(body: str | bytes) -> int:
    data = body.encode("ascii") if isinstance(body, str) else body
    value = 0
    for byte in data:
        value ^= byte
    return value


def sentence(body: str) -> bytes:
    wire = f"${body}*{checksum(body):02X}".encode("ascii")
    if len(wire) > MAX_LINE:
        raise ProtocolError("line_length", "line exceeds 512 bytes")
    return wire + b"\r\n"


def control(verb: str, **fields: object) -> bytes:
    return sentence("PCGBO," + verb + "".join(f",{k}={v}" for k, v in fields.items()))


@dataclass(frozen=True)
class Sentence:
    raw: bytes
    name: str
    fields: tuple[str, ...]

    @property
    def kind(self) -> str:
        return self.name[-3:] if not self.name.startswith("P") and re.fullmatch(r"[A-Z]{2}[A-Z]{3}", self.name) else self.name


def parse_sentence(raw: bytes) -> Sentence:
    # Callers pass a line without CRLF, so embedded terminators cannot hide data.
    if len(raw) > MAX_LINE:
        raise ProtocolError("line_length", "line exceeds 512 bytes")
    if not raw.startswith(b"$") or any(b < 32 or b > 126 for b in raw):
        raise ProtocolError("framing", "expected a printable ASCII dollar sentence")
    if len(raw) < 6 or raw[-3:-2] != b"*" or raw.count(b"*") != 1:
        raise ProtocolError("checksums", "expected exactly two checksum digits")
    if not re.fullmatch(rb"[0-9A-Fa-f]{2}", raw[-2:]):
        raise ProtocolError("checksums", "invalid checksum digits")
    if checksum(raw[1:-3]) != int(raw[-2:], 16):
        raise ProtocolError("checksums", "checksum mismatch")
    fields = raw[1:-3].decode("ascii").split(",")
    if not re.fullmatch(r"[A-Z][A-Z0-9]{3,7}", fields[0]):
        raise ProtocolError("framing", "invalid sentence name")
    return Sentence(raw, fields[0], tuple(fields[1:]))


class StreamDecoder:
    """CRLF framing independent of read/notification boundaries, with UBX skip.

    An entire length-delimited UBX frame is consumed before looking for '$'.
    Binary data is not enabled for navigation by this framing operation.
    """

    def __init__(self):
        self.line = bytearray()
        self.cr = False
        self.discard = False
        self.sync = False
        self.ubx = bytearray()
        self.ubx_size = 0
        self.binary_frames = 0
        self.errors: list[ProtocolError] = []

    def fail(self, rule: str, message: str) -> None:
        # Keep one sample per rejection category. A noisy peer must not evict
        # an earlier checksum failure with thousands of later framing errors.
        if not any(error.rule == rule for error in self.errors):
            self.errors.append(ProtocolError(rule, message))

    def feed(self, data: bytes) -> list[Sentence]:
        output: list[Sentence] = []
        for byte in data:
            if self.ubx:
                self.ubx.append(byte)
                if len(self.ubx) == 6:
                    self.ubx_size = 8 + int.from_bytes(self.ubx[4:6], "little")
                    if self.ubx_size > MAX_UBX:
                        self.fail("binary_frames", "UBX frame exceeds 4096 bytes")
                        # Consume the declared payload without allocating it.
                if len(self.ubx) >= 6 and self.ubx_size > MAX_UBX:
                    if len(self.ubx) == 6:
                        self.skip_binary = self.ubx_size - 6
                    self.ubx.clear()
                    self.ubx_size = 0
                    continue
                if self.ubx_size and len(self.ubx) == self.ubx_size:
                    a = b = 0
                    for value in self.ubx[2:-2]:
                        a = (a + value) & 255
                        b = (b + a) & 255
                    if bytes((a, b)) != self.ubx[-2:]:
                        self.fail("binary_frames", "UBX checksum mismatch")
                    else:
                        self.binary_frames += 1
                    self.ubx.clear()
                    self.ubx_size = 0
                continue
            if getattr(self, "skip_binary", 0):
                self.skip_binary -= 1
                continue
            if self.sync:
                self.sync = False
                if byte == 0x62:
                    self.ubx = bytearray(b"\xb5\x62")
                    continue
                self.fail("framing", "unrecognized binary sync")
            if byte == 0xB5:
                if self.line:
                    self.fail("framing", "binary frame interrupted a text line")
                self.line.clear()
                self.cr = self.discard = False
                self.sync = True
                continue
            if self.cr:
                self.cr = False
                if byte == 10:
                    if self.line and not self.discard:
                        try:
                            output.append(parse_sentence(bytes(self.line)))
                        except ProtocolError as exc:
                            self.fail(exc.rule, str(exc))
                    self.line.clear()
                    self.discard = False
                    continue
                self.fail("framing", "CR was not followed by LF")
                self.line.clear()
                self.discard = False
            if byte == 13:
                self.cr = True
            elif byte == 10:
                self.fail("framing", "LF without CR")
                self.line.clear()
                self.discard = False
            elif byte == 36:
                if self.line:
                    self.fail("framing", "new dollar before line terminator")
                self.line = bytearray(b"$")
                self.discard = False
            elif self.discard:
                continue
            elif self.line and 32 <= byte <= 126:
                if len(self.line) == MAX_LINE:
                    self.fail("line_length", "line exceeds 512 bytes")
                    self.line.clear()
                    self.discard = True
                else:
                    self.line.append(byte)
            elif self.line or byte not in (0,):
                self.fail("framing", "unexpected byte outside an ASCII sentence")
                self.line.clear()
                self.discard = True
        return output

    def finish(self) -> None:
        if self.line or self.cr or self.sync or self.ubx or getattr(self, "skip_binary", 0):
            self.fail("framing", "incomplete frame at end of stream")


def uint(value: str, name: str, maximum: int = 0xFFFFFFFF) -> int:
    if not re.fullmatch(r"[0-9]{1,20}", value):
        raise ProtocolError("field_values", f"{name} must be an unsigned decimal")
    number = int(value)
    if number > maximum:
        raise ProtocolError("field_values", f"{name} is out of range")
    return number


def number(value: str, name: str, minimum: float = -math.inf,
           maximum: float = math.inf) -> float:
    if not re.fullmatch(r"-?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)", value):
        raise ProtocolError("field_values", f"{name} must be a finite decimal")
    result = float(value)
    if not math.isfinite(result) or not minimum <= result <= maximum:
        raise ProtocolError("field_values", f"{name} is out of range")
    return result


def decode_text(value: str) -> str:
    if re.search(r"%(?![0-9A-Fa-f]{2})", value):
        raise ProtocolError("field_values", "invalid percent escape")
    try:
        text = unquote_to_bytes(value).decode("utf-8", "strict")
    except UnicodeError as exc:
        raise ProtocolError("field_values", "text is not encoded UTF-8") from exc
    if any(ord(c) < 32 or ord(c) == 127 for c in text):
        raise ProtocolError("field_values", "text contains control characters")
    return text


REQUIRED = {
    "HELLO": {"pv", "id", "boot", "caps"},
    "ACCEPT": {"pv", "use"},
    "HB": {"boot", "seq", "ms", "fix"},
    "STATUS": {"level", "code"}, "ERROR": {"code"},
    "DISCOVER": {"pv", "nonce"}, "OFFER": {"pv", "nonce", "id", "tcp"},
    "SUBSCRIBE": {"pv", "session", "port", "lease"},
    "DATA": {"session", "boot", "seq", "ms", "age_ms"},
    "WHEEL": {"ms", "v_mps", "dir"}, "BATT": {"ms"},
    "TIME": {"ms", "utc_ms", "sigma_ms", "src"},
}


def parse_control(line: Sentence) -> tuple[str, dict[str, str]]:
    if line.name != "PCGBO" or not line.fields:
        raise ProtocolError("control_grammar", "not a PCGBO control sentence")
    verb, *pairs = line.fields
    if not re.fullmatch(r"[A-Z]{2,16}", verb):
        raise ProtocolError("control_grammar", "invalid verb")
    fields: dict[str, str] = {}
    for pair in pairs:
        if "=" not in pair:
            raise ProtocolError("control_grammar", "expected key=value")
        key, value = pair.split("=", 1)
        if not re.fullmatch(r"[a-z][a-z0-9_]*", key):
            raise ProtocolError("control_grammar", "invalid field name")
        if key in fields:
            raise ProtocolError("control_grammar", "duplicate key")
        if not value or not re.fullmatch(r"[A-Za-z0-9._~%+-]+", value):
            raise ProtocolError("control_grammar", "empty or unescaped field value")
        if key in ("name", "maker", "fw", "msg") and "+" in value:
            raise ProtocolError("field_values", "display text needs percent-encoded plus")
        decode_text(value)
        parts = value.split("+")
        decoded_parts = [decode_text(part) for part in parts]
        if any(not part for part in parts) or len(set(decoded_parts)) != len(parts):
            raise ProtocolError("control_grammar", "empty or duplicate list element")
        fields[key] = value
    if not REQUIRED.get(verb, set()).issubset(fields):
        raise ProtocolError("control_grammar", "missing required field")
    if "pv" in fields:
        version = fields["pv"]
        pattern = r"1" if verb == "DISCOVER" else (r"1\.0" if verb in ("ACCEPT", "SUBSCRIBE") else r"1\.[0-9]+")
        if not re.fullmatch(pattern, version):
            raise ProtocolError("version", "unsupported or malformed protocol major")
    for key in ("nonce", "session"):
        if key in fields and not re.fullmatch(r"[0-9a-f]{16,32}", fields[key]):
            raise ProtocolError("field_values", f"invalid {key}")
    if "boot" in fields and not re.fullmatch(r"[0-9a-f]{8}", fields["boot"]):
        raise ProtocolError("field_values", "boot must be eight lowercase hex digits")
    if "id" in fields and not re.fullmatch(r"[A-Za-z0-9._~-]{1,32}", fields["id"]):
        raise ProtocolError("field_values", "invalid pseudonymous device id")
    for key in ("ms", "seq", "age_ms"):
        if key in fields:
            uint(fields[key], key)
    if "utc_ms" in fields:
        uint(fields["utc_ms"], "utc_ms", 0x7FFFFFFFFFFFFFFF)
    for key in ("fix", "charging"):
        if key in fields and fields[key] not in ("0", "1"):
            raise ProtocolError("field_values", f"invalid {key}")
    for key in ("caps", "use", "talkers"):
        if key in fields:
            items = fields[key].split("+")
            if len(items) != len(set(items)) or any(not re.fullmatch(r"[A-Za-z0-9._~%-]+", i) for i in items):
                raise ProtocolError("capabilities", "invalid list")
            decoded_items = [decode_text(item) for item in items]
            if key != "talkers" and ("nmea" not in decoded_items or set(decoded_items) & RESERVED_CAPS):
                raise ProtocolError("capabilities", "nmea required; write capabilities reserved")
            if key == "talkers" and any(not re.fullmatch(r"[A-Z]{2}", item) for item in items):
                raise ProtocolError("field_values", "talkers must be two uppercase ASCII letters")
            if key != "talkers":
                fields[key] = "+".join(decoded_items)
    for key in ("tcp", "port"):
        if key in fields and not 1 <= uint(fields[key], key, 65535):
            raise ProtocolError("field_values", "port must be 1..65535")
    if verb == "SUBSCRIBE" and (fields["lease"] != "10" or fields["port"] != str(PUSH_PORT)):
        raise ProtocolError("field_values", f"subscription needs port {PUSH_PORT} and lease 10 seconds")
    if verb == "STATUS" and fields["level"] not in ("info", "warn", "error"):
        raise ProtocolError("field_values", "invalid status level")
    if "code" in fields and not re.fullmatch(r"[A-Z][A-Z0-9_]{0,47}", fields["code"]):
        raise ProtocolError("field_values", "invalid diagnostic code")
    if verb == "WHEEL":
        number(fields["v_mps"], "v_mps", 0)
        if fields["dir"] not in ("fwd", "rev", "unknown"):
            raise ProtocolError("field_values", "invalid wheel direction")
        if "sigma_mps" in fields:
            number(fields["sigma_mps"], "sigma_mps", 0)
    if verb == "BATT":
        if not set(fields) & {"mv", "pct"}:
            raise ProtocolError("field_values", "battery needs at least one measurement")
        if "mv" in fields:
            uint(fields["mv"], "mv", 100000)
        if "pct" in fields:
            number(fields["pct"], "pct", 0, 100)
    if verb == "TIME":
        number(fields["sigma_ms"], "sigma_ms", 0)
        if fields["src"] not in ("gnss", "rtc"):
            raise ProtocolError("field_values", "invalid time source")
    return verb, fields


def parse_imu(line: Sentence) -> tuple[int, tuple[float, ...]]:
    if line.name != "PCGBI" or len(line.fields) not in (2, 7):
        raise ProtocolError("imu", "PCGBI needs exactly 2 or 7 numeric fields")
    clock = uint(line.fields[0], "ms")
    values = tuple(number(v, "IMU axis") for v in line.fields[1:])
    if any(abs(v) > 500 for v in values[:1 if len(values) == 1 else 3]):
        raise ProtocolError("imu", "gyro exceeds 500 deg/s")
    if len(values) == 6 and math.hypot(*values[3:]) > 50:
        raise ProtocolError("imu", "acceleration exceeds 50 m/s2")
    return clock, values


class ImuClock:
    """Interval validation only; this tool does not implement heading fusion."""

    def __init__(self):
        self.last: tuple[int, int, int] | None = None

    def accept(self, line: Sentence, arrival_ms: int) -> str:
        clock, values = parse_imu(line)
        result = "boundary"
        if self.last is not None:
            old_clock, old_arrival, old_axes = self.last
            if arrival_ms < old_arrival:
                return "rejected"
            delta = (clock - old_clock) & 0xFFFFFFFF
            if delta == 0:
                return "duplicate"
            if 0 < delta < 0x80000000 and delta <= 1000 and arrival_ms - old_arrival <= 1000 and len(values) == old_axes:
                result = "step"
        self.last = clock, arrival_ms, len(values)
        return result


def utc_of_day(value: str) -> int:
    if not re.fullmatch(r"[0-9]{6}(?:\.[0-9]{1,6})?", value):
        raise ProtocolError("epoch_match", "invalid NMEA UTC")
    hour, minute, second = int(value[:2]), int(value[2:4]), float(value[4:])
    if hour > 23 or minute > 59 or second >= 61:
        raise ProtocolError("epoch_match", "invalid NMEA UTC range")
    return round(((hour * 60 + minute) * 60 + second) * 1000)


def rmc_epoch(line: Sentence) -> int:
    if len(line.fields) < 9:
        raise ProtocolError("mandatory_rmc_gga", "short RMC")
    value = line.fields[8]
    if not re.fullmatch(r"[0-9]{6}", value):
        raise ProtocolError("epoch_match", "invalid RMC date")
    year = int(value[4:6])
    try:
        day = dt.date(1900 + year if year >= 80 else 2000 + year, int(value[2:4]), int(value[:2]))
    except ValueError as exc:
        raise ProtocolError("epoch_match", "invalid RMC date") from exc
    return day.toordinal() * 86400000 + utc_of_day(line.fields[0])


def newer_u32(value: int, previous: int) -> bool:
    return 0 < ((value - previous) & 0xFFFFFFFF) < 0x80000000


def local_ipv4(value: str, *, wildcard: bool = False) -> str:
    address = ipaddress.IPv4Address(value)
    allowed = (address.is_loopback or address.is_link_local or
               any(address in ipaddress.IPv4Network(net) for net in
                   ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16")))
    if not (allowed or wildcard and value == "0.0.0.0"):
        raise ValueError("use a loopback, RFC1918 or link-local IPv4 address")
    return str(address)


class PushWindow:
    """Validate an explicitly selected UDP sender, boot and leased session."""

    def __init__(self, peer: tuple[str, int], session: str, boot: str, now: float | None = None):
        self.peer_ip, self.session, self.boot = peer[0], session, boot
        self.last_seq: int | None = None
        self.renew(now)

    def renew(self, now: float | None = None):
        self.lease_until = (time.monotonic() if now is None else now) + 10

    def accept(self, data: bytes, peer: tuple[str, int], now: float | None = None) -> tuple[bool, str]:
        if (time.monotonic() if now is None else now) >= self.lease_until:
            return False, "lease_expired"
        if peer[0] != self.peer_ip:
            return False, "foreign_sender"
        if len(data) > 1200 or not data.endswith(b"\r\n"):
            return False, "datagram_length"
        try:
            parts = [parse_sentence(p) for p in data[:-2].split(b"\r\n")]
            verb, header = parse_control(parts[0])
            if verb != "DATA" or len(parts) < 2:
                return False, "header"
            if header["session"] != self.session:
                return False, "foreign_session"
            if header["boot"] != self.boot:
                return False, "foreign_boot"
            if uint(header["age_ms"], "age_ms") > 1000:
                return False, "stale"
            seq = uint(header["seq"], "seq")
            if self.last_seq is not None and not newer_u32(seq, self.last_seq):
                return False, "duplicate_or_old"
            if any(p.name.startswith("P") or not re.fullmatch(r"[A-Z]{2}[A-Z]{3}", p.name) for p in parts[1:]):
                return False, "non_nmea_payload"
        except (ProtocolError, IndexError) as exc:
            return False, getattr(exc, "rule", "header")
        self.last_seq = seq
        return True, "accepted"
