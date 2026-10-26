#!/usr/bin/env python3
"""Regenerate synthetic fixtures, never capture real device/location traffic.

The textbook checksums below are literal reference values. Other fixtures use
the public encoder for maintenance; expected outcomes are specified explicitly,
not inferred by the parser under test.
"""

import json
from pathlib import Path

from protocol import DISCOVERY_PORT, TCP_PORT, checksum, control, sentence


def main():
    base = Path(__file__).resolve().parent.parent / "test-vectors"
    base.mkdir(exist_ok=True)
    cases = []

    def put(name, data):
        (base / name).write_bytes(data)
        return name

    def stream(name, data, names, **kwargs):
        cases.append(dict(name=name, kind="stream", file=put(name + ".nmea", data), names=names, **kwargs))

    gga = b"$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47\r\n"
    gst = b"$GPGST,123519.000,1.0,2.0,1.5,0.0,1.5,2.0,3.0*68\r\n"
    rmc = b"$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A\r\n"
    epoch, names = gga + gst + rmc, ["GPGGA", "GPGST", "GPRMC"]
    stream("textbook", epoch, names, valid_fix_epochs=1,
           must_pass=["checksums", "line_length", "epoch_match", "epoch_order", "gst", "fix_validity"])
    stream("byte-by-byte", epoch, names, chunks=[1], valid_fix_epochs=1, must_pass=["framing", "epoch_match"])
    stream("ble-mtu23", epoch, names, chunks=[20, 1, 7, 13], valid_fix_epochs=1, must_pass=["framing", "epoch_match"])
    stream("bad-checksum", gga.replace(b"*47", b"*00") + gst + rmc, ["GPGST", "GPRMC"],
           errors=["checksums"], must_fail=["checksums", "mandatory_rmc_gga", "epoch_match"])
    for length in (512, 513):
        prefix = "PCGBO,STATUS,level=info,code=TEST,msg="
        body = prefix + "x" * (length - 4 - len(prefix))
        raw = ("$" + body + "*%02X" % checksum(body)).encode() + b"\r\n"
        assert len(raw) - 2 == length
        stream("line-" + str(length), raw + gga, (["PCGBO"] if length == 512 else []) + ["GPGGA"],
               errors=[] if length == 512 else ["line_length"],
               must_pass=["checksums"] if length == 512 else [],
               must_fail=["line_length"] if length == 513 else [])
    invalid = sentence("GPGGA,123519,4807.038,N,01131.000,E,0,00,0.9,545.4,M,46.9,M,,") + sentence(
        "GPRMC,123519,V,4807.038,N,01131.000,E,0.0,0.0,230394,,,N")
    stream("rmc-void", invalid, ["GPGGA", "GPRMC"], valid_fix_epochs=0, must_pass=["fix_validity"])
    stream("cached-valid-repeat", epoch + epoch, names * 2, must_fail=["no_stale_repeats"])
    stream("rmc-before-gga", rmc + gga + gst, ["GPRMC", "GPGGA", "GPGST"], valid_fix_epochs=1,
           must_fail=["epoch_order"], must_pass=["epoch_match", "fix_validity"])
    def epoch_at(stamp):
        output = []
        for line in epoch.splitlines():
            body = line[1:-3].decode("ascii")
            fields = body.split(",")
            fields[1] = stamp
            output.append(sentence(",".join(fields)))
        return b"".join(output)
    next_epoch = epoch_at("123519.200")
    stream("stationary-fresh-epochs", epoch + next_epoch, names * 2, valid_fix_epochs=2,
           must_pass=["epoch_rate", "no_stale_repeats", "epoch_match"])
    stream("slow-cached-delivery", epoch + next_epoch, names * 2,
           arrival_ms=[0, 0, 0, 4900, 4900, 4900], must_fail=["epoch_rate"])
    stream("timed-cutoff-mid-line", epoch + b"$GPGGA,1235", names, eof=False,
           must_pass=["framing"])
    stream("epoch-mismatch", gga + rmc.replace(b"123519", b"123520").replace(b"*6A", b"*60"),
           ["GPGGA", "GPRMC"], must_fail=["epoch_match"])
    hello = control("HELLO", pv="1.0", id="demo01", boot="00000001", caps="nmea")
    stream("hello-unknown-field", control("HELLO", pv="1.0", id="demo01", boot="00000001",
           caps="nmea+future", name="DIY%20GNSS", future="allowed"), ["PCGBO"],
           must_pass=["control_grammar", "version", "capabilities"])
    stream("unknown-long-key", control("ACCEPT", pv="1.0", use="nmea",
           **{"future_optional_key_longer_than_twenty_four": "a+b"}), ["PCGBO"],
           device_accepted=True, must_pass=["control_grammar"])
    stream("unknown-list-duplicate", control("HELLO", pv="1.0", id="demo01", boot="00000001",
           caps="nmea", future="a+%61"), ["PCGBO"], reply_codes=["BAD_REQUEST"],
           must_fail=["control_grammar"])
    stream("unknown-minor", control("HELLO", pv="1.7", id="demo01", boot="00000001", caps="nmea+future"), ["PCGBO"],
           heartbeat_enabled=False, must_pass=["version", "capabilities"])
    stream("accept-unknown-optional-cap", control("ACCEPT", pv="1.0", use="nmea+future-cap"), ["PCGBO"],
           device_accepted=True, heartbeat_enabled=True, must_pass=["version", "capabilities"])
    stream("unknown-major", control("ACCEPT", pv="2.0", use="nmea"), ["PCGBO"],
           reply_codes=["UNSUPPORTED_VERSION"], heartbeat_enabled=False, must_fail=["version"])
    stream("duplicate-key", sentence("PCGBO,ACCEPT,pv=1.0,use=nmea,pv=1.0"), ["PCGBO"],
           reply_codes=["BAD_REQUEST"], must_fail=["control_grammar"])
    stream("accept-unselected-minor", control("ACCEPT", pv="1.7", use="nmea"), ["PCGBO"],
           reply_codes=["UNSUPPORTED_VERSION"], must_fail=["version"])
    stream("accept-unadvertised", control("ACCEPT", pv="1.0", use="nmea+wheel"), ["PCGBO"],
           reply_codes=["UNSUPPORTED_CAPABILITY"], heartbeat_enabled=False)
    stream("reserved-write-cap", control("ACCEPT", pv="1.0", use="nmea+cmd_pair"), ["PCGBO"],
           reply_codes=["BAD_REQUEST"], heartbeat_enabled=False, must_fail=["capabilities"])
    stream("encoded-reserved-cap", control("ACCEPT", pv="1.0", use="nmea+cmd%5Fpair"), ["PCGBO"],
           reply_codes=["BAD_REQUEST"], heartbeat_enabled=False, must_fail=["capabilities"])
    stream("encoded-unadvertised-cap", control("ACCEPT", pv="1.0", use="nmea+%77heel"), ["PCGBO"],
           reply_codes=["UNSUPPORTED_CAPABILITY"], heartbeat_enabled=False)
    stream("no-hb-before-accept", hello + epoch, ["PCGBO"] + names, heartbeat_enabled=False)
    stream("heartbeat-without-gnss", hello + control("HB", boot="00000001", seq=0, ms=1000, fix=0) + control(
        "HB", boot="00000001", seq=1, ms=2000, fix=0), ["PCGBO"] * 3, profile="extended", accepted=True,
        must_fail=["mandatory_rmc_gga", "fresh_epochs"], must_pass=["hb_timing", "hb_identity"],
        arrival_ms=[0, 1000, 2000], valid_fix_epochs=0)
    stream("heartbeat-fake-fix", hello + control("HB", boot="00000001", seq=0, ms=1000, fix=1),
           ["PCGBO"] * 2, profile="extended", accepted=True, must_fail=["fix_validity", "mandatory_rmc_gga"])
    nonce = "0123456789abcdef"
    offer = control("OFFER", pv="1.0", nonce=nonce, id="demo01", tcp=TCP_PORT,
                    caps="nmea", name="Synthetic-GNSS")
    stream("discover", control("DISCOVER", pv=1, nonce=nonce), ["PCGBO"],
           discovery_reply=True, offer_file=put("discover-offer.nmea", offer))
    stream("discover-unknown-major", control("DISCOVER", pv=2, nonce=nonce), ["PCGBO"],
           discovery_reply=False, reply_codes=["UNSUPPORTED_VERSION"])
    stream("supplemental-valid", control("WHEEL", ms=1000, v_mps="4.20", dir="fwd", sigma_mps="0.10") + control(
        "BATT", ms=1000, mv=4080, pct="78.5", charging=0) + control(
        "TIME", ms=1000, utc_ms=1735689600000, sigma_ms=20, src="gnss"), ["PCGBO"] * 3,
        must_pass=["field_values", "control_grammar"])
    stream("battery-no-measurement", control("BATT", ms=1000, charging=1), ["PCGBO"],
           reply_codes=["BAD_REQUEST"], must_fail=["field_values"])
    stream("bad-percent", control("HELLO", pv="1.0", id="demo01", boot="00000001", caps="nmea", name="bad%GG"),
           ["PCGBO"], reply_codes=["BAD_REQUEST"], must_fail=["field_values"])
    for name, extras in (("raw-display-plus", {"name": "DIY+GNSS"}),
                         ("bad-talker", {"talkers": "garbage"}),
                         ("bad-utf8", {"name": "%FF"})):
        stream(name, control("HELLO", pv="1.0", id="demo01", boot="00000001", caps="nmea", **extras),
               ["PCGBO"], reply_codes=["BAD_REQUEST"], must_fail=["field_values"])
    for name, body in (("imu-intermediate", "PCGBI,1000,1.0,0.0"),
                       ("imu-gyro-range", "PCGBI,1000,501"),
                       ("imu-accel-range", "PCGBI,1000,1,0,0,0,0,51")):
        stream(name, sentence(body), ["PCGBI"], must_fail=["imu"])
    samples = [4294967280, 4, 4, 24, 2, 22, 2022]
    file = put("imu-rollover-reboot.nmea", b"".join(sentence(f"PCGBI,{s},1.0") for s in samples))
    cases.append(dict(name="imu-rollover-reboot", kind="imu", file=file, names=["PCGBI"] * 7,
                      arrival_ms=[0, 20, 21, 40, 60, 80, 2100],
                      states=["boundary", "step", "duplicate", "step", "boundary", "step", "boundary"]))
    stream("imu-full-form", b"$PCGBI,123456,12.5,0.1,-0.2,0.00,0.00,9.81*5C\r\n", ["PCGBI"], must_pass=["imu"])
    full_samples = [4294967276, 0, 20, 20, 40, 1060, 1080]
    file = put("imu-full-50hz-edge.nmea", b"".join(
        sentence(f"PCGBI,{ms},1.00,0.10,-0.20,0.00,0.00,9.81") for ms in full_samples))
    cases.append(dict(name="imu-full-50hz-edge", kind="imu", file=file, names=["PCGBI"] * 7,
                      arrival_ms=[0, 20, 40, 41, 60, 1080, 1100],
                      states=["boundary", "step", "step", "duplicate", "step", "boundary", "step"]))

    def ubx(payload, bad=False):
        inner = bytes([1, 7]) + len(payload).to_bytes(2, "little") + payload
        a = b = 0
        for value in inner:
            a = (a + value) & 255
            b = (b + a) & 255
        return b"\xb5\x62" + inner + bytes([a ^ (1 if bad else 0), b])

    for name, packet, count, errors in (
        ("ubx-dollar", ubx(rmc), 1, []),
        ("ubx-bad-checksum", ubx(rmc, True), 0, ["binary_frames"]),
        ("ubx-max4096", ubx((rmc + b"x" * 4096)[:4088]), 1, []),
        ("ubx-over4096", ubx(b"x" * 4089), 0, ["binary_frames"]),
    ):
        cases.append(dict(name=name, kind="stream", file=put(name + ".bin", packet + epoch), names=names,
                          chunks=[1, 7, 20], binary_frames=count, errors=errors, valid_fix_epochs=1))
    stream("bare-lf", gga.replace(b"\r\n", b"\n") + rmc, ["GPRMC"], errors=["framing"], must_fail=["framing"])
    peer, session, boot = ["127.0.0.1", DISCOVERY_PORT], "0123456789abcdef", "00000001"

    def push(name, seq, **kwargs):
        fields = dict(session=session, boot=boot, seq=seq, ms=1000, age_ms=10)
        fields.update(kwargs)
        return put(name + ".nmea", control("DATA", **fields) + epoch)

    p10, p9, p11 = push("udp-seq10", 10), push("udp-seq9", 9), push("udp-seq11", 11)
    foreign = push("udp-foreign-session", 12, session="fedcba9876543210")
    oldboot, stale = push("udp-foreign-boot", 12, boot="00000002"), push("udp-stale", 12, age_ms=1001)
    events = [dict(packet=p10, peer=peer), dict(packet=p10, peer=peer), dict(packet=p9, peer=peer),
              dict(packet=foreign, peer=peer), dict(packet=p11, peer=["127.0.0.2", DISCOVERY_PORT]),
              dict(packet=oldboot, peer=peer), dict(packet=stale, peer=peer), dict(packet=p11, peer=peer)]
    file = put("udp-replay.json", (json.dumps(events, indent=2) + "\n").encode())
    cases.append(dict(name="udp-duplicate-reorder-foreign", kind="udp", file=file, peer=peer, session=session,
                      boot=boot, outcomes=[[True, "accepted"], [False, "duplicate_or_old"],
                      [False, "duplicate_or_old"], [False, "foreign_session"], [False, "foreign_sender"],
                      [False, "foreign_boot"], [False, "stale"], [True, "accepted"]]))
    packets = [push("udp-rollover-last", 4294967295), push("udp-rollover-zero", 0)]
    file = put("udp-rollover.json", (json.dumps([dict(packet=p, peer=peer) for p in packets], indent=2) + "\n").encode())
    cases.append(dict(name="udp-sequence-rollover", kind="udp", file=file, peer=peer, session=session,
                      boot=boot, outcomes=[[True, "accepted"], [True, "accepted"]]))
    header = control("DATA", session=session, boot=boot, seq=20, ms=1000, age_ms=10)
    bad_checksum = put("udp-bad-checksum.nmea", header + gga.replace(b"*47", b"*00"))
    proprietary = put("udp-proprietary-payload.nmea", header + control("STATUS", level="info", code="TEST"))
    oversized = put("udp-oversized.nmea", header + b"x" * 1200 + b"\r\n")
    partial = put("udp-partial-line.nmea", header + gga[:-2])
    events = [dict(packet=bad_checksum, peer=peer), dict(packet=proprietary, peer=peer),
              dict(packet=oversized, peer=peer), dict(packet=partial, peer=peer),
              dict(packet=p10, peer=peer, now=10.0),
              dict(packet=p10, peer=["127.0.0.1", 12345], now=11.0, renew_at=5.0)]
    file = put("udp-limits-lease.json", (json.dumps(events, indent=2) + "\n").encode())
    cases.append(dict(name="udp-limits-and-lease", kind="udp", file=file, peer=peer, session=session,
                      boot=boot, outcomes=[[False, "checksums"], [False, "non_nmea_payload"],
                      [False, "datagram_length"], [False, "datagram_length"],
                      [False, "lease_expired"], [True, "accepted"]]))
    # These are complete MUST-failure sets for short/negative stream fixtures,
    # not a claim that an isolated one-epoch fixture is a conforming device.
    no_gnss = {"mandatory_rmc_gga", "epoch_rate", "fresh_epochs"}
    no_valid = no_gnss | {"link_activity"}
    one_epoch = {"epoch_rate"}
    failures = {
        "textbook": one_epoch, "byte-by-byte": one_epoch, "ble-mtu23": one_epoch,
        "bad-checksum": {"checksums", "mandatory_rmc_gga", "epoch_match", "epoch_rate"},
        "line-512": no_gnss, "line-513": no_gnss | {"line_length"},
        "rmc-void": one_epoch, "cached-valid-repeat": one_epoch | {"no_stale_repeats"},
        "rmc-before-gga": one_epoch, "stationary-fresh-epochs": set(),
        "slow-cached-delivery": {"epoch_rate"}, "timed-cutoff-mid-line": one_epoch,
        "epoch-mismatch": {"epoch_match", "epoch_rate"},
        "hello-unknown-field": no_gnss, "unknown-minor": no_gnss,
        "unknown-long-key": no_gnss | {"control_direction"},
        "unknown-list-duplicate": no_valid | {"control_grammar"},
        "accept-unknown-optional-cap": no_gnss | {"control_direction"},
        "unknown-major": no_valid | {"version"},
        "duplicate-key": no_valid | {"control_grammar"},
        "accept-unselected-minor": no_valid | {"version"},
        "accept-unadvertised": no_gnss | {"control_direction"},
        "reserved-write-cap": no_valid | {"capabilities"},
        "encoded-reserved-cap": no_valid | {"capabilities"},
        "encoded-unadvertised-cap": no_gnss | {"control_direction"},
        "no-hb-before-accept": one_epoch,
        "heartbeat-without-gnss": no_gnss | {"discovery_nonce"},
        "heartbeat-fake-fix": no_gnss | {"fix_validity", "hb_timing", "discovery_nonce"},
        "discover": no_gnss | {"control_direction"},
        "discover-unknown-major": no_valid | {"version"},
        "supplemental-valid": no_gnss,
        "battery-no-measurement": no_valid | {"field_values"},
        "bad-percent": no_valid | {"field_values"},
        "raw-display-plus": no_valid | {"field_values"},
        "bad-talker": no_valid | {"field_values"},
        "bad-utf8": no_valid | {"field_values"},
        "imu-intermediate": no_valid | {"imu"}, "imu-gyro-range": no_valid | {"imu"},
        "imu-accel-range": no_valid | {"imu"},
        "imu-rollover-reboot": no_gnss, "imu-full-form": no_gnss,
        "imu-full-50hz-edge": no_gnss,
        "ubx-dollar": one_epoch, "ubx-bad-checksum": one_epoch | {"binary_frames"},
        "ubx-max4096": one_epoch, "ubx-over4096": one_epoch | {"binary_frames"},
        "bare-lf": {"framing", "mandatory_rmc_gga", "epoch_match", "epoch_rate"},
    }
    for case in cases:
        if case["kind"] != "udp":
            case["expected_must_failures"] = sorted(failures[case["name"]])
    (base / "expected.json").write_text(json.dumps(dict(schema=1, cases=cases), indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"Created {len(cases)} vector cases")


if __name__ == "__main__":
    main()
