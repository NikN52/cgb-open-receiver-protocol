# Changelog

All notable public protocol and reference implementation changes are recorded here. The project is currently a draft; entries do not promise a future application version or release date.

## Unreleased — 1.0 DRAFT

### Added

- Equal Russian and English protocol specifications.
- Baseline NMEA-over-TCP profile on port 28495.
- PCGBO HELLO, ACCEPT, HB, STATUS, and ERROR control messages.
- Local discovery on UDP 28496 and optional leased UDP push to 28497.
- Optional Bluetooth SPP and BLE Nordic UART Service profiles.
- Capabilities `nmea`, `ubx_rx`, `imu_pcgbi`, `wheel`, `batt`, `time`, and `udp_push`.
- Exact two- or seven-field PCGBI IMU grammar and bounds.
- A deterministic seven-field 50 Hz PCGBI fixture covering `uint32` rollover, duplicate timestamps, and a gap over one second, while retaining two-field coverage.
- WHEEL, BATT, and diagnostic TIME messages.
- Minimal RX-only ESP32 UART-to-Wi-Fi example and Python standard-library validation helpers.
- Optional ESP32 `Wire` IMU driver software with auto-detection for MPU6050/6500/925x and LSM6DS3/DSL/DSO families, configurable axis mapping, and nonblocking boot bias, sample-rate setup, and retry handling; this is an implementation feature, not evidence of physical-hardware validation.
- Privacy, local-route, freshness, conformance, and physical-validation boundaries.

### Changed

- Selected draft defaults outside the common Android and Windows ephemeral ranges:
  TCP 28495, discovery/subscription UDP 28496, and bridge-side push UDP 28497.
  Reference tools retain their TCP-port override.
- Clarified that devices may additionally offer conventional NMEA-0183 on TCP 10110.
- Standardized Russian terminology for in-car multimedia.
- Closed active simulator clients cleanly when a bounded run ends.
- Documented the legacy PCGBI fallback after 5 seconds without ACCEPT, immediate PCGBI for deliberately HELLO-free plain streams, and the rule that any valid ACCEPT permanently disables unselected PCGBI for that connection.

### Reserved

- `rtcm_in` and `cmd_pair` are reserved and must not be advertised or accepted by protocol 1.0 implementations.

### Compatibility note

- Ordinary NMEA TCP and proprietary PCGBI parsing are available in Car GNSS Bridge 0.27.x through the existing TCP Network receiver at a configured IP and port 28495. This does not imply HELLO/ACCEPT or other ORP negotiation support; the dedicated Open Protocol receiver remains described as “soon,” with no promised version or date.
