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
- WHEEL, BATT, and diagnostic TIME messages.
- Minimal RX-only ESP32 UART-to-Wi-Fi example and Python standard-library validation helpers.
- Privacy, local-route, freshness, conformance, and physical-validation boundaries.

### Changed

- Selected draft defaults outside the common Android and Windows ephemeral ranges:
  TCP 28495, discovery/subscription UDP 28496, and bridge-side push UDP 28497.
  Reference tools retain their TCP-port override.
- Clarified that devices may additionally offer conventional NMEA-0183 on TCP 10110.
- Standardized Russian terminology for in-car multimedia.
- Closed active simulator clients cleanly when a bounded run ends.

### Reserved

- `rtcm_in` and `cmd_pair` are reserved and must not be advertised or accepted by protocol 1.0 implementations.

### Compatibility note

- Ordinary NMEA TCP is available in Car GNSS Bridge 0.27.90 through the existing TCP Network receiver at a configured IP and port 28495. Dedicated Open Protocol support is described as “soon” and has no promised version or date.
