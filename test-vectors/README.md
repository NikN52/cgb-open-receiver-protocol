# Synthetic wire fixtures

Run `python tools/run_vectors.py` from the repository root. No hardware,
network, bridge application or third-party package is required.

All location-bearing fixtures use the standard historical NMEA textbook
example. IDs, boot values, sessions and nonces are synthetic examples, not
credentials or identifiers collected from a device. Binary UBX fixtures are
artificial frames, including an embedded complete `$GPRMC` sentence that must
never escape the binary parser as navigation text.

`expected.json` schema 1 lists each case:

- `file`: wire bytes or a JSON list of UDP events;
- `names`: checksum-valid sentences that both application input paths see;
- `chunks`: repeating byte-fragment sizes (including one-byte and BLE MTU 23);
- `errors`: exact framing/checksum/length rejection categories;
- `device_accepted`, `reply_codes`, `heartbeat_enabled`: simulator session state;
- `must_fail`, `must_pass`, `valid_fix_epochs`: checker outcomes;
- `expected_must_failures`: complete explicit MUST-failure set for that short
  or negative stream fixture; any unexpected MUST failure fails the vector;
- `arrival_ms`: optional arrival timeline, or IMU interval timeline;
- `eof`: whether the stream truly ends rather than reaching a timed cutoff;
- UDP cases include selected peer/session/boot, packet files, explicit
  outcomes, optional event `now` and subscription `renew_at` times.

`.nmea` files contain literal ASCII CRLF wire bytes. `.bin` files preserve
binary bytes. Git text conversion is disabled for both formats. A 512-byte
line is accepted; the 513-byte fixture is rejected. Negative fixtures are
intentionally invalid and must not be 'fixed' by an editor or formatter.

The cases cover bad checksum, line bounds, fragmentation, invalid fix,
stationary fresh epochs, stale repeats, mismatched epochs, unknown fields,
unknown minor/major, duplicate keys, unadvertised/reserved capabilities,
percent encoding, HB without GNSS, both exact IMU forms and reboot/rollover,
UBX framing and bounds, UDP duplicate/reorder/foreign session/source/boot,
age/size/partial/checksum rejection and subscription expiry/renewal.

To regenerate the published fixture bytes intentionally:

```sh
python tools/generate_vectors.py
python tools/run_vectors.py
```

The literal textbook XORs are independent reference values. Other generated
checksums use the reference encoder, while expected outcomes are specified
explicitly. Passing these shared-code vectors does not certify an independent
device or a physical receiver and does not demonstrate extended bridge support.
