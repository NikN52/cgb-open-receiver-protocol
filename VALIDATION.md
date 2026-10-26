# Reference implementation validation

This is evidence for the initial **1.0 DRAFT**, not certification of a device
or a promise of extended support in the bridge.

The port-migration checks below were rerun on **2026-10-25 (UTC)**. The profile
uses TCP `28495` for the device stream, UDP `28496` for discovery and
subscription, and optional UDP `28497` for bridge-side push.

The optional ESP32 IMU changes below were checked on **2026-10-26**. Older
loopback/emulator results retain their original scope and were not rerun as
physical IMU tests.

## ESP32 compilation

PlatformIO, pinned `espressif32@6.9.0`, Arduino framework:

| Environment | Result | RAM | Flash |
|---|---|---:|---:|
| `esp32dev` | PASS | 71,420 / 327,680 bytes (21.8%) | 812,885 / 1,310,720 bytes (62.0%) |
| `esp32-s3-devkitc-1` | PASS | 70,924 / 327,680 bytes (21.6%) | 762,909 / 3,342,336 bytes (22.8%) |

Reproduce from `examples/esp32-arduino` with `pio run`. The source static
review also passes with `python tests/static_review.py`. The actual portable
firmware primitives are compiled and executed by `python tests/host_review.py`
using a host C++ compiler with `-std=c++11 -Wall -Wextra -Werror`, and their
wire output is checked by the Python protocol decoder. The builds and source
checks do not demonstrate physical
UART pin state, board compatibility, sustained Wi-Fi throughput or GNSS RF
performance. Generated build caches, configured credentials, binaries and raw
local logs are not part of this public source repository.

## Optional IMU checklist

- [x] Both board environments build with the default `OPENRX_ENABLE_IMU=1`,
  `OPENRX_ENABLE_PROTOCOL_HELLO=1`; `-Wall`, `-Wextra`, and
  `-Werror=return-type` remain enabled. No compiler warnings were emitted.
- [x] Additional `esp32dev` builds pass without warnings for IMU enabled with
  HELLO disabled, and for both IMU and HELLO disabled.
- [x] Static review covers public defaults (SDA/SCL 21/22 and 8/9), Wire-only
  optional compilation, WHO/burst selection, bounded I2C timeout, configuration
  readback, invalid-map handling, 2-second boot window, five-error/30-second
  retry paths, and PCGBI placement after complete epoch sends.
- [x] Host C++ checks cover MPU big-endian and LSM little-endian conversion,
  24 proper signed rotations, invalid maps, raw sensor rails, finite values,
  gyro/acceleration limits, decimal rounding and exact checksums/field order.
- [x] Host C++ checks cover stationary versus changing/moving boot observations,
  separate connection gates, the 5-second legacy timeout, permanent late-ACCEPT
  stop, pure streams, and `uint32` timer rollover. These are portable-primitives
  tests; I2C state-machine failures are source-reviewed, not runtime-injected.
- [x] The seven-field 50 Hz fixture covers timestamp rollover, duplicate `ms`,
  and a gap greater than one second; existing two-field fixtures are retained.
  All 65 fixture-directory files reproduce byte-for-byte after regeneration.
- [x] All 16 new/changed public files pass a source scan for local private
  paths, MAC addresses and private-key/token markers; Wi-Fi placeholders remain.
- [x] English/Russian negotiation and section 10.1 describe the same legacy
  exception and plain-stream option; example wiring/mounting/calibration/rate
  instructions explain the absence of absolute north.
- [ ] Physical I2C identification, voltage/pin routing, sensor filter behavior,
  axis/yaw signs, real stillness calibration and failure/reconnect timing.
- [ ] Sustained UART/Wi-Fi/IMU delivery and heading use in an actual vehicle or
  the released bridge. No such hardware/vehicle result is claimed here.

## Python and wire fixtures

Python 3.11 standard library:

- `python tools/run_vectors.py`: **50/50 PASS** (2026-10-26). Every stream fixture compares
  both applications' actual input paths, explicit rejection categories and
  the complete expected MUST-failure set. A negative fixture passing means
  its expected rejection occurred. Fixtures were regenerated with
  `python tools/generate_vectors.py`. The discovery case compares the complete
  generated OFFER, including its checksum and TCP `28495`, and separately
  verifies an advertised TCP-port override.
- Two simultaneous 30-second loopback checker runs, baseline and extended:
  **PASS**, with the following measured results. All observed MUST and SHOULD
  checks passed; unexercised rules were explicitly SKIP.
- Socket smoke check: two simultaneous clients each receive one HELLO; a
  third connection is refused; the client without ACCEPT receives no HB,
  while an accepted client receives two HB in a 2.2-second observation.
- Bounded shutdown with an active client: baseline and accepted extended
  connections each received 14 RMC epochs during a three-second run, then
  clean EOF. Both simulator processes exited 0 with no stderr. Active-client
  cancellation is handled as normal shutdown.

| Profile | Observation | Complete epochs | Valid-fix epochs | PASS rules | SKIP rules | FAIL rules |
|---|---:|---:|---:|---:|---:|---:|
| baseline | 30.093 s | 131 | 131 | 18 | 17 | 0 |
| extended | 30.016 s | 130 | 130 | 24 | 11 | 0 |

The PC simulator and both checkers used TCP `28495`, with discovery on UDP
`28496`; no port override was needed. On this run,
`netsh interface ipv4 show excludedportrange protocol=tcp` did not list TCP
`28495`, and the simulator successfully bound it. The tools retain `--port`
for local overrides. The checker proves unicast discovery to a known local
IP, not broadcast discovery across a real hotspot. UDP `28497` behavior
remains covered by fixtures rather than a live push test.

On 2026-10-25, all 64 fixture-directory files reproduced byte-for-byte on a
second generator run. A source scan of all 88 then-intended public files passed checks for retired
ports, private local paths, known local job identifiers, private-key/token
patterns, and the retired Russian multimedia terminology. These pattern
checks are a scoped source review, not a guarantee against every possible
disclosure.

## Released bridge baseline TCP

An exclusive test reservation on the shared Android 9 emulator was acquired
after approximately seven minutes of waiting. The installed release,
Car GNSS Bridge **0.27.90**
(`versionCode=171`), was tested without replacing the APK.

- The PC ran `python tools/simulator.py --host 127.0.0.1 --port 28495 --nmea-only`.
- `adb reverse tcp:28495 tcp:28495` connected the bridge's existing TCP Network
  receiver at `127.0.0.1:28495` to that simulator.
- **PASS for TCP ingestion and a parsed raw Fix:** the bridge's system-info
  screen showed receiver 1 as TCP/NMEA, `FIX`, **8/8 satellites**, and a current
  observation age of **0.2–0.3 seconds**. A local screenshot,
  `baseline-tcp28495-fix.png`, records these indicators. Receiver-only mode
  was selected temporarily for this diagnostic observation.
- The synthetic constellation also caused application trust warnings. This
  is raw-input interoperability evidence; trusted navigation under normal
  filters and delivery to a separate navigator remain unverified.
- All **six** saved preference files were restored byte-for-byte. The test's
  reverse mapping was removed, the bridge service was stopped as it had been
  before the test, and the task's emulator reservation was released. Runtime
  permissions and app-op settings were not manually changed.

Screenshots, preference backups and raw local logs remain outside this public
source repository. No real receiver or vehicle was involved.

## Boundaries

The Python tools share wire primitives. Their tests are regression checks,
not independent interoperability certification. UDP anti-replay/lease and
binary/IMU edge cases are fixture tests, not a live UDP-push, Bluetooth or
physical IMU test. No physical receiver, either hotspot topology, vehicle,
receiver UART electrical behavior, Bluetooth link, extended bridge profile,
or future pairing/command profile was tested here.

The released bridge's baseline TCP behavior and the DRAFT extended profile
are separate. No extended bridge support is claimed by these results.

The emulator result above covers the released bridge's existing baseline TCP
input only. It does not demonstrate ORP discovery, ACCEPT/HB negotiation, or
any extended profile in that application.
