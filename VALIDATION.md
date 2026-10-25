# Reference implementation validation

This is evidence for the initial **1.0 DRAFT**, not certification of a device
or a promise of extended support in the bridge.

## ESP32 compilation

PlatformIO, pinned `espressif32@6.9.0`, Arduino framework:

| Environment | Result | RAM | Flash |
|---|---|---:|---:|
| `esp32dev` | PASS | 70,832 / 327,680 bytes (21.6%) | 786,365 / 1,310,720 bytes (60.0%) |
| `esp32-s3-devkitc-1` | PASS | 70,352 / 327,680 bytes (21.5%) | 736,401 / 3,342,336 bytes (22.0%) |

Reproduce from `examples/esp32-arduino` with `pio run`. The source static
review also passes with `python tests/static_review.py`. No host C++ runtime
test was performed. The builds and source checks do not demonstrate physical
UART pin state, board compatibility, sustained Wi-Fi throughput or GNSS RF
performance. Generated build caches, configured credentials, binaries and raw
local logs are not part of this public source repository.

## Python and wire fixtures

Python 3.11 standard library:

- `python tools/run_vectors.py`: **49/49 PASS**. Every stream fixture compares
  both applications' actual input paths, explicit rejection categories and
  the complete expected MUST-failure set. A negative fixture passing means
  its expected rejection occurred.
- Two simultaneous 30-second loopback checker runs, baseline and extended:
  **PASS**, 151 baseline and 150 extended complete, valid GNSS epochs. All observed MUST and
  SHOULD checks passed; unexercised rules were explicitly SKIP.
- Socket smoke check: two simultaneous clients each receive one HELLO; a
  third connection is refused; the client without ACCEPT receives no HB,
  while an accepted client receives 1 Hz HB.

The PC run used TCP `49161` because the operating system reserved the default
PC port. The source/firmware device default is still `49761`. Discovery used
UDP `49762`; the checker proves unicast discovery to a known local IP, not
broadcast discovery across a real hotspot.

## Boundaries

The Python tools share wire primitives. Their tests are regression checks,
not independent interoperability certification. UDP anti-replay/lease and
binary/IMU edge cases are fixture tests, not a live UDP-push, Bluetooth or
physical IMU test. No physical receiver, either hotspot topology, vehicle,
receiver UART electrical behavior, Bluetooth link, extended bridge profile,
or future pairing/command profile was tested here.

The released bridge's baseline TCP behavior and the DRAFT extended profile
are separate. No extended bridge support is claimed by these results.

The requested baseline check with the released bridge in a shared Android
emulator remains **unverified**: the emulator stayed reserved by another job.
No emulator receiver preferences or reverse mappings were changed, and no
Fix screenshot was produced. This application interoperability check is
outstanding for the publication review.
