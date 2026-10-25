#!/usr/bin/env python3
"""Replay published fixtures through the simulator and checker input paths."""

from __future__ import annotations

from collections import Counter
import json
from pathlib import Path

from check_device import StreamAudit
from protocol import (ImuClock, PushWindow, StreamDecoder, parse_control,
                      parse_sentence)
from simulator import DeviceSession, discovery_reply


def chunks(data: bytes, sizes: list[int]):
    offset = 0
    index = 0
    while offset < len(data):
        size = sizes[index % len(sizes)]
        yield data[offset:offset + size]
        index += 1
        offset += size


def require(actual, expected, label):
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


def run_stream(case, base):
    data = (base / case["file"]).read_bytes()
    device = DeviceSession()
    audit = StreamAudit(case.get("profile", "baseline"))
    if case.get("accepted"):
        audit.accept_at = 0.0
    replies = []
    structural_errors = set()
    now = 0.1
    stream_chunks = list(chunks(data, case.get("chunks", [4096])))
    if "arrival_ms" in case and case["kind"] == "stream":
        stream_chunks = data.splitlines(keepends=True)
        require(len(stream_chunks), len(case["arrival_ms"]), "timeline length")
    for index, chunk in enumerate(stream_chunks):
        if "arrival_ms" in case and case["kind"] == "stream":
            now = case["arrival_ms"][index] / 1000
        replies.extend(device.receive(chunk))
        audit.feed(chunk, now)
        now += 0.00001
    if case.get("eof", True):
        device.decoder.finish()
    structural_errors.update(exc.rule for exc in device.decoder.errors)
    require(dict(device.received), dict(Counter(case["names"])), "simulator framing")
    require(sorted(structural_errors), sorted(case.get("errors", [])), "simulator rejected rules")
    expected_counts = Counter(name[-3:] if len(name) == 5 and not name.startswith("P") else name
                              for name in case["names"])
    for kind, count in expected_counts.items():
        require(audit.counts[kind], count, f"checker {kind} count")
    for rule in ("checksums", "line_length", "framing", "binary_frames"):
        require(rule in audit.failures, rule in structural_errors, f"checker {rule}")
    require(device.decoder.binary_frames, case.get("binary_frames", 0), "simulator UBX frames")
    require(audit.decoder.binary_frames, case.get("binary_frames", 0), "checker UBX frames")
    require(device.accepted, case.get("device_accepted", False), "simulator ACCEPT state")
    codes = [parse_control(parse_sentence(reply[:-2]))[1]["code"] for reply in replies]
    require(codes, case.get("reply_codes", []), "simulator error replies")
    if "heartbeat_enabled" in case:
        require(device.heartbeat(1000) is not None, case["heartbeat_enabled"], "simulator HB gate")
    report = audit.finish(now, 2.0, eof=case.get("eof", True))
    failed = {row["rule"] for row in report["rules"] if row["status"] == "FAIL"}
    must_failed = {row["rule"] for row in report["rules"] if row["status"] == "FAIL" and row["requirement"] == "MUST"}
    require(sorted(must_failed), case["expected_must_failures"], "complete checker MUST-failure set")
    for rule in case.get("must_fail", []):
        require(rule in failed, True, f"checker must detect {rule}")
    for rule in case.get("must_pass", []):
        require(rule in failed, False, f"checker must accept {rule}")
    if "valid_fix_epochs" in case:
        require(report["valid_fix_epochs"], case["valid_fix_epochs"], "checker valid fixes")
    if case.get("discovery_reply") is not None:
        reply = discovery_reply(data, "demo01")
        require(reply is not None, case["discovery_reply"], "simulator discovery gate")
        if reply is not None:
            fields = parse_control(parse_sentence(reply[:-2]))[1]
            request = parse_control(parse_sentence(data[:-2]))[1]
            require(fields["nonce"], request["nonce"], "discovery nonce echo")


def run_imu(case, base):
    data = (base / case["file"]).read_bytes()
    # Exercise both applications' framing, then the checker clock interval path.
    run_stream({**case, "kind": "stream"}, base)
    decoder = StreamDecoder()
    lines = decoder.feed(data)
    clock = ImuClock()
    states = [clock.accept(line, arrival) for line, arrival in zip(lines, case["arrival_ms"])]
    require(states, case["states"], "IMU segmentation")


def run_udp(case, base):
    events = json.loads((base / case["file"]).read_text(encoding="utf-8"))
    # Each parser consumer gets a new anti-replay window; no state leaks across tests.
    for role in ("simulator reference primitives", "checker reference primitives"):
        window = PushWindow(tuple(case["peer"]), case["session"], case["boot"], now=0)
        outcomes = []
        for event in events:
            if "renew_at" in event:
                window.renew(event["renew_at"])
            packet = (base / event["packet"]).read_bytes()
            outcomes.append(list(window.accept(packet, tuple(event["peer"]), now=event.get("now", 0))))
        require(outcomes, case["outcomes"], role)


def main():
    base = Path(__file__).resolve().parent.parent / "test-vectors"
    manifest = json.loads((base / "expected.json").read_text(encoding="utf-8"))
    require(manifest["schema"], 1, "manifest schema")
    failed = 0
    for case in manifest["cases"]:
        try:
            {"stream": run_stream, "imu": run_imu, "udp": run_udp}[case["kind"]](case, base)
            print("PASS " + case["name"])
        except (AssertionError, ValueError, KeyError) as exc:
            failed += 1
            print(f"FAIL {case['name']}: {exc}")
    print(f"{len(manifest['cases']) - failed}/{len(manifest['cases'])} vectors passed")
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
