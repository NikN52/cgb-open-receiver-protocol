#!/usr/bin/env python3
"""Bounded 30-second local receiver conformance probe (Python stdlib only).

PASS means observed during this run. SKIP means not exercised; it is never a
claim of hardware, security, vehicle or optional-transport conformance.
"""

from __future__ import annotations

import argparse
from collections import Counter
import ipaddress
import json
from pathlib import Path
import secrets
import socket
import time

from protocol import (CAPS, DISCOVERY_PORT, TCP_PORT, ImuClock, ProtocolError,
                      StreamDecoder, control, local_ipv4, newer_u32, number,
                      parse_control, rmc_epoch, uint, utc_of_day)


RULES = {
    "checksums": ("MUST", "Every text sentence has a valid NMEA XOR checksum"),
    "line_length": ("MUST", "Every text line is at most 512 bytes without CRLF"),
    "framing": ("MUST", "ASCII CRLF framing survives arbitrary read boundaries"),
    "binary_frames": ("MUST", "Observed UBX frames are bounded and checksummed"),
    "control_grammar": ("MUST", "Control fields are complete and keys are unique"),
    "field_values": ("MUST", "Observed numeric and encoded control values are valid"),
    "version": ("MUST", "Extended traffic uses a compatible protocol major"),
    "capabilities": ("MUST", "Capabilities are valid; no reserved write capability"),
    "control_direction": ("MUST", "Client-only control verbs are not emitted by the device"),
    "mandatory_rmc_gga": ("MUST", "RMC and GGA are both present"),
    "epoch_match": ("MUST", "Every completed RMC epoch has a GGA with matching UTC"),
    "epoch_order": ("SHOULD", "GGA -> GST when present -> RMC within an epoch"),
    "epoch_rate": ("MUST", "Advancing UTC epochs are emitted at 1..10 Hz"),
    "no_stale_repeats": ("MUST", "RMC measurement epochs advance, even when stationary"),
    "fresh_epochs": ("MUST", "GNSS epochs continue independently of heartbeat traffic"),
    "fix_validity": ("MUST", "Observed RMC/GGA validity and HB fix claims are consistent"),
    "gst": ("SHOULD", "GST is present for every completed epoch"),
    "gsa_gsv": ("SHOULD", "GSA and GSV are observed"),
    "hello_once": ("MUST", "Exactly one HELLO is sent on an extended connection"),
    "hb_after_accept": ("MUST", "No periodic HB occurs before ACCEPT"),
    "hb_timing": ("MUST", "HB is 1 Hz after ACCEPT (0.5..1.5 s observation tolerance)"),
    "hb_identity": ("MUST", "HB boot matches HELLO and uint32 sequence advances"),
    "link_activity": ("MUST", "No five-second interval without a valid message"),
    "discovery_nonce": ("MUST", "Unicast OFFER echoes this probe's random nonce"),
    "discovery_identity": ("MUST", "OFFER identity and port match TCP HELLO and endpoint"),
    "imu": ("MUST", "Observed PCGBI uses the published grammar and sensor bounds"),
}


class StreamAudit:
    def __init__(self, profile: str = "extended"):
        self.profile = profile
        self.decoder = StreamDecoder()
        self.counts: Counter = Counter()
        self.failures: dict[str, str] = {}
        self.hello: dict[str, str] | None = None
        self.accept_at: float | None = None
        self.last_hb: float | None = None
        self.hb_seq: int | None = None
        self.hb_ms: int | None = None
        self.last_activity: float | None = None
        self.last_rmc_at: float | None = None
        self.first_rmc_at: float | None = None
        self.last_fix_at: float | None = None
        self.last_rmc_key: int | None = None
        self.last_epoch: int | None = None
        self.epochs: dict[int, dict] = {}
        self.selected = {"nmea"}
        self.completed = 0
        self.good_fixes = 0
        self.order = 0
        self.imu_clock = ImuClock()

    def fail(self, rule: str, detail: str):
        self.failures.setdefault(rule, detail)

    def feed(self, data: bytes, now: float) -> list[dict[str, str]]:
        hellos = []
        for line in self.decoder.feed(data):
            self.order += 1
            self.counts[line.kind] += 1
            try:
                if line.name == "PCGBO":
                    verb, fields = parse_control(line)
                    self.counts[verb] += 1
                    if verb in ("ACCEPT", "DISCOVER", "SUBSCRIBE", "OFFER", "DATA"):
                        self.fail("control_direction", "unexpected control verb on device TCP stream")
                    if verb == "HELLO":
                        if self.hello is not None:
                            self.fail("hello_once", "repeated HELLO on one connection")
                        else:
                            self.hello = fields
                            hellos.append(fields)
                    elif verb == "HB":
                        if self.accept_at is None:
                            self.fail("hb_after_accept", "HB before ACCEPT")
                        if self.hello is None or fields["boot"] != self.hello["boot"]:
                            self.fail("hb_identity", "HB boot does not match HELLO")
                        seq, ms = uint(fields["seq"], "seq"), uint(fields["ms"], "ms")
                        if self.hb_seq is not None and (not newer_u32(seq, self.hb_seq) or
                                                      not newer_u32(ms, self.hb_ms)):
                            self.fail("hb_identity", "HB sequence/uptime did not advance")
                        if self.last_hb is not None and not 0.5 <= now - self.last_hb <= 1.5:
                            self.fail("hb_timing", "HB arrival interval outside tolerance")
                        if self.last_hb is None and self.accept_at is not None and now - self.accept_at > 1.5:
                            self.fail("hb_timing", "first HB delayed after ACCEPT")
                        self.hb_seq, self.hb_ms, self.last_hb = seq, ms, now
                        if fields["fix"] == "1" and (self.last_fix_at is None or now - self.last_fix_at > 1.5):
                            self.fail("fix_validity", "HB claims a fix without fresh navigation")
                    elif verb in ("WHEEL", "BATT", "TIME"):
                        if self.profile == "extended" and (self.accept_at is None or verb.lower() not in self.selected):
                            self.fail("capabilities", "unselected supplemental data")
                elif line.name == "PCGBI":
                    if self.profile == "extended" and (self.accept_at is None or "imu_pcgbi" not in self.selected):
                        self.fail("capabilities", "unselected IMU data")
                    self.imu_clock.accept(line, round(now * 1000))
                elif line.kind in ("GGA", "GST", "RMC"):
                    self.navigation(line, now)
            except ProtocolError as exc:
                self.fail(exc.rule, str(exc))
                continue
            if self.last_activity is not None and now - self.last_activity >= 5:
                self.fail("link_activity", "five seconds without valid traffic")
            self.last_activity = now
        for exc in self.decoder.errors:
            self.fail(exc.rule, str(exc))
        if self.decoder.binary_frames and self.profile == "extended" and (self.accept_at is None or "ubx_rx" not in self.selected):
            self.fail("capabilities", "UBX observed without ubx_rx negotiation")
        self.decoder.errors.clear()
        return hellos

    def navigation(self, line, now):
        if not line.fields:
            raise ProtocolError("epoch_match", "missing UTC")
        key = utc_of_day(line.fields[0])
        entry = self.epochs.setdefault(key, {})
        if line.kind == "GGA":
            if len(line.fields) < 14:
                raise ProtocolError("mandatory_rmc_gga", "short GGA")
            quality = uint(line.fields[5], "GGA quality", 8)
            uint(line.fields[6], "satellites", 999)
            if quality:
                self.validate_coordinates(line.fields[1:5])
                if line.fields[8] and line.fields[9] != "M":
                    raise ProtocolError("field_values", "GGA altitude must use metres")
                number(line.fields[7], "HDOP", 0)
            for value, unit, field in ((line.fields[8], line.fields[9], "altitude"),
                                       (line.fields[10], line.fields[11], "geoid separation")):
                if value:
                    number(value, field)
                    if unit != "M":
                        raise ProtocolError("field_values", f"GGA {field} must use metres")
            entry["GGA"] = self.order, quality
            if key == self.last_rmc_key and "RMC" in entry and entry["RMC"][1] == "A" and quality:
                self.last_fix_at = self.last_rmc_at
        elif line.kind == "GST":
            if len(line.fields) < 8:
                raise ProtocolError("field_values", "short GST")
            for value in line.fields[1:]:
                if value:
                    number(value, "GST value", 0)
            entry["GST"] = self.order
        else:
            epoch = rmc_epoch(line)
            status = line.fields[1]
            if status not in ("A", "V"):
                raise ProtocolError("fix_validity", "RMC status must be A or V")
            if status == "A":
                self.validate_coordinates(line.fields[2:6])
                if line.fields[6]:
                    number(line.fields[6], "RMC speed in knots", 0)
                if line.fields[7]:
                    if number(line.fields[7], "RMC true course", 0) >= 360:
                        raise ProtocolError("field_values", "RMC course must be less than 360 degrees")
            if self.last_epoch is not None:
                delta = epoch - self.last_epoch
                if delta <= 0:
                    self.fail("no_stale_repeats", "repeated or backward RMC measurement UTC")
                elif not 100 <= delta <= 1000:
                    self.fail("epoch_rate", "UTC epoch interval outside 100..1000 ms")
            if self.last_rmc_at is not None:
                arrival_gap = now - self.last_rmc_at
                if arrival_gap > 1.5:
                    self.fail("epoch_rate", "GNSS arrival gap exceeds 1.5-second observation tolerance")
                if arrival_gap >= 5:
                    self.fail("fresh_epochs", "five seconds without GNSS epochs despite traffic")
            if self.first_rmc_at is None:
                self.first_rmc_at = now
            self.last_epoch, self.last_rmc_at, self.last_rmc_key = epoch, now, key
            entry["RMC"] = self.order, status
            self.last_fix_at = now if status == "A" and entry.get("GGA", (0, 0))[1] > 0 else None
        # Retain a small horizon so either RMC/GGA wire order can be checked.
        if len(self.epochs) > 32:
            oldest = next(iter(self.epochs))
            self.finalize_epoch(self.epochs.pop(oldest))

    @staticmethod
    def validate_coordinates(fields):
        if len(fields) != 4:
            raise ProtocolError("field_values", "short coordinate tuple")
        lat, ns, lon, ew = fields
        if ns not in ("N", "S") or ew not in ("E", "W"):
            raise ProtocolError("field_values", "invalid coordinate hemisphere")
        for value, digits, maximum in ((lat, 2, 90), (lon, 3, 180)):
            import re
            if not re.fullmatch(r"[0-9]{" + str(digits + 2) + r"}(?:\.[0-9]+)?", value):
                raise ProtocolError("field_values", "invalid NMEA coordinate syntax")
            degree, minute = int(value[:digits]), number(value[digits:], "minutes", 0)
            if minute >= 60 or degree > maximum or degree == maximum and minute:
                raise ProtocolError("field_values", "NMEA coordinate out of range")

    def finalize_epoch(self, entry):
        if "RMC" not in entry:
            return  # The observation can end between GGA and RMC.
        self.completed += 1
        if "GGA" not in entry:
            self.fail("epoch_match", "RMC epoch has no matching GGA")
            return
        gga_order, quality = entry["GGA"]
        rmc_order, status = entry["RMC"]
        if not gga_order < rmc_order or "GST" in entry and not gga_order < entry["GST"] < rmc_order:
            self.fail("epoch_order", "recommended epoch order not observed")
        if "GST" not in entry:
            self.fail("gst", "an RMC epoch has no GST")
        if (status == "V") != (quality == 0):
            self.fail("fix_validity", "RMC validity and GGA quality disagree")
        if status == "A" and quality > 0:
            self.good_fixes += 1

    def finish(self, now: float, duration: float, discovery: dict | None = None,
               discovery_error: str | None = None, port: int = TCP_PORT, eof: bool = False) -> dict:
        # A timed observation may end in the middle of a valid ongoing frame.
        # Only actual EOF makes that a framing violation by the peer.
        if eof:
            self.decoder.finish()
        for exc in self.decoder.errors:
            self.fail(exc.rule, str(exc))
        for entry in self.epochs.values():
            self.finalize_epoch(entry)
        self.epochs.clear()
        if not self.counts["RMC"] or not self.counts["GGA"]:
            self.fail("mandatory_rmc_gga", "missing RMC or GGA")
        if self.completed < 2:
            self.fail("epoch_rate", "fewer than two GNSS epochs")
        if self.first_rmc_at is not None and self.last_rmc_at - self.first_rmc_at >= 2:
            arrival_hz = (self.counts["RMC"] - 1) / (self.last_rmc_at - self.first_rmc_at)
            if not 0.8 <= arrival_hz <= 12:
                self.fail("epoch_rate", "observed average emission cadence outside tolerance")
        if self.last_rmc_at is None or now - self.last_rmc_at >= 5:
            self.fail("fresh_epochs", "no GNSS epoch within five seconds of end")
        if self.last_activity is None or now - self.last_activity >= 5:
            self.fail("link_activity", "no valid message within five seconds of end")
        if not self.counts["GSA"] or not self.counts["GSV"]:
            self.fail("gsa_gsv", "missing GSA or GSV")
        if self.profile == "extended":
            if self.counts["HELLO"] != 1:
                self.fail("hello_once", "expected exactly one HELLO")
            if self.accept_at is None:
                self.fail("hb_after_accept", "no compatible HELLO to accept")
            if self.counts["HB"] < 2 or self.last_hb is None or now - self.last_hb > 1.5:
                self.fail("hb_timing", "missing or stopped 1 Hz heartbeat after ACCEPT")
            elif self.accept_at is not None and self.last_hb - self.accept_at > duration:
                self.fail("hb_timing", "invalid observation clock")
            if discovery is None:
                self.fail("discovery_nonce", discovery_error or "no matching OFFER")
            elif self.hello is None or discovery["id"] != self.hello["id"] or int(discovery["tcp"]) != port:
                self.fail("discovery_identity", "OFFER and TCP identity/port do not match")
        rows = []
        extended_rules = {"hello_once", "hb_after_accept", "hb_timing", "hb_identity", "discovery_nonce", "discovery_identity"}
        for rule, (requirement, description) in RULES.items():
            skip = None
            if self.profile == "baseline" and rule in extended_rules:
                skip = "extended profile not requested"
            if rule in {"control_grammar", "version", "capabilities", "control_direction"} and not self.counts["PCGBO"]:
                skip = "no control sentences observed"
            if rule == "discovery_identity" and discovery is None:
                skip = "no OFFER to compare"
            if rule == "hb_identity" and self.last_hb is None:
                skip = "no valid heartbeat identity observed"
            if rule == "imu" and not self.counts["PCGBI"]:
                skip = "no IMU data advertised or observed"
            if rule == "binary_frames" and not self.decoder.binary_frames:
                skip = "no UBX frames observed"
            if rule in self.failures:
                state, detail = "FAIL", self.failures[rule]
            elif skip:
                state, detail = "SKIP", skip
            else:
                state, detail = "PASS", "observed without violation"
            rows.append(dict(rule=rule, requirement=requirement, status=state,
                             description=description, detail=detail))
        for rule in ("uart_read_only", "local_interface_binding", "two_clients_and_backpressure",
                     "five_second_loss_transition", "invalid_fix_transition", "broadcast_discovery",
                     "bluetooth_spp_ble", "udp_push", "pairing_reserved"):
            rows.append(dict(rule=rule, requirement="MANUAL", status="SKIP",
                             description="Requires source inspection or a separate fault/transport test",
                             detail="not proven by a passive TCP observation"))
        return dict(profile=self.profile, duration_seconds=round(duration, 3),
                    sentences=dict(self.counts), completed_epochs=self.completed,
                    valid_fix_epochs=self.good_fixes, rules=rows,
                    passed=not any(r["status"] == "FAIL" and r["requirement"] == "MUST" for r in rows))


def discover(host: str, interface: str, timeout: float = 1.5) -> tuple[dict | None, str | None]:
    nonce = secrets.token_hex(8)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.bind((interface, 0))
        sock.settimeout(timeout)
        sock.sendto(control("DISCOVER", pv=1, nonce=nonce), (host, DISCOVERY_PORT))
        deadline = time.monotonic() + timeout
        error = None
        while time.monotonic() < deadline:
            sock.settimeout(max(0.01, deadline - time.monotonic()))
            try:
                data, peer = sock.recvfrom(1201)
            except (socket.timeout, ConnectionResetError):
                break
            if peer[0] != host:
                error = "OFFER came from a foreign endpoint"
                continue
            decoder = StreamDecoder()
            lines = decoder.feed(data)
            decoder.finish()
            try:
                if decoder.errors or len(lines) != 1:
                    raise ProtocolError("discovery_nonce", "invalid OFFER framing")
                verb, fields = parse_control(lines[0])
                if verb == "OFFER" and fields["nonce"] == nonce:
                    return fields, None
                error = "OFFER nonce did not match"
            except ProtocolError as exc:
                error = str(exc)
        return None, error or "no discovery reply before timeout"


def check(host: str, port: int, duration: float, profile: str, interface: str) -> dict:
    offer, discovery_error = discover(host, interface)
    audit = StreamAudit(profile)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((interface, 0))
        sock.settimeout(3)
        sock.connect((host, port))
        sock.settimeout(0.2)
        start = time.monotonic()
        eof = False
        while time.monotonic() - start < duration:
            try:
                data = sock.recv(4096)
            except socket.timeout:
                continue
            now = time.monotonic()
            if not data:
                audit.fail("link_activity", "TCP disconnected before observation ended")
                eof = True
                break
            hellos = audit.feed(data, now)
            if profile == "extended" and hellos and audit.accept_at is None:
                audit.selected = set(hellos[0]["caps"].split("+")) & (CAPS - {"udp_push"})
                sock.sendall(control("ACCEPT", pv="1.0", use="+".join(sorted(audit.selected))))
                audit.accept_at = time.monotonic()
        return audit.finish(time.monotonic(), time.monotonic() - start, offer, discovery_error, port, eof=eof)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("host", help="receiver's local IPv4 address")
    parser.add_argument("--port", type=int, default=TCP_PORT)
    parser.add_argument("--seconds", type=float, default=30)
    parser.add_argument("--profile", choices=("baseline", "extended"), default="extended")
    parser.add_argument("--interface", help="local Wi-Fi/AP IPv4 address; required outside loopback")
    parser.add_argument("--prefix-length", type=int, default=24, choices=range(1, 33), metavar="1..32")
    parser.add_argument("--json", type=Path, help="optional report file (contains no raw location stream)")
    args = parser.parse_args()
    try:
        local_ipv4(args.host)
        if not 1 <= args.port <= 65535 or not 2 <= args.seconds <= 3600:
            raise ValueError("port must be 1..65535 and seconds 2..3600")
        interface = args.interface or ("127.0.0.1" if ipaddress.IPv4Address(args.host).is_loopback else None)
        if interface is None:
            raise ValueError("select the local Wi-Fi/AP address with --interface")
        local_ipv4(interface)
        if ipaddress.IPv4Address(args.host) not in ipaddress.IPv4Network(f"{interface}/{args.prefix_length}", strict=False):
            raise ValueError("receiver is outside the selected local subnet")
        report = check(args.host, args.port, args.seconds, args.profile, interface)
    except (ValueError, OSError) as exc:
        parser.exit(2, f"check_device: {exc}\n")
    for row in report["rules"]:
        print(f"{row['status']:4} {row['requirement']:6} {row['rule']}: {row['detail']}")
    print(f"epochs={report['completed_epochs']} valid_fix_epochs={report['valid_fix_epochs']}")
    print("PASS" if report["passed"] else "FAIL")
    if args.json:
        args.json.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    raise SystemExit(0 if report["passed"] else 1)


if __name__ == "__main__":
    main()
