# CGB Open Receiver Protocol: ESP32 Arduino example

This public PlatformIO example receives NMEA from a GNSS receiver over an RX-only physical connection and serves complete GNSS epochs on the local Wi-Fi network. It is intentionally a small baseline implementation: `caps=nmea`, no receiver commands, no RTCM input, no cloud service, and no Internet request.

## Wiring and configuration

Connect only three signals:

| GNSS receiver | ESP32 |
|---|---|
| TX (3.3 V logic) | GPIO configured as `OPENRX_GNSS_RX_PIN` |
| GND | GND |
| Power | A supply appropriate for the receiver |

Do **not** connect an ESP32 UART TX signal to the receiver. The firmware configures only the UART1 RX route and passes `GPIO_NUM_NC` for TX, RTS, and CTS. In the pinned ESP-IDF version that value means “leave unchanged,” not “detach an existing route”; this program never maps those outputs and has no receiver-UART write path. The physical safety boundary is the omitted TX wire. Confirm voltage levels and power requirements in the receiver and board manuals. Never connect a 5 V UART output directly to a 3.3 V-only input.

Create an ignored `include/config.local.h` containing only the settings you override. The tracked `include/config.h` supplies placeholder/default values under `#ifndef`, so credentials do not need to be written into a tracked file. Configure:

```cpp
#pragma once
#define OPENRX_WIFI_SSID "YOUR_WIFI"
#define OPENRX_WIFI_PASSWORD "YOUR_WIFI_PASSWORD"
#define OPENRX_DEVICE_ID "my-openrx-1"
```

- replace `YOUR_WIFI` and `YOUR_WIFI_PASSWORD`;
- assign a stable pseudonymous device ID and mDNS host name (do not use a MAC address, SSID, serial number, or personal name);
- select `OPENRX_GNSS_BAUD` as 9600, 38400, or 115200;
- select the RX pin for the board and wiring;
- optionally disable protocol HELLO for a pure NMEA stream, or disable mDNS.

The normative defaults are `OPENRX_TCP_PORT=28495` and
`OPENRX_DISCOVERY_PORT=28496`; a deployment-specific override belongs in the
ignored local configuration.

`id` must be 1-32 unreserved ASCII characters and must be user-assigned. Display fields in the local configuration are wire values: percent-encode UTF-8 bytes outside the unreserved set and encode a literal plus as `%2B`. Invalid configuration disables network services instead of falling back to a hardware-derived name.

Configure the receiver itself to emit checksum-valid `GGA` and `RMC`, with `GST` recommended, for the same UTC epoch. Either GGA-before-RMC or RMC-before-GGA input is accepted; the TCP bundle is normalized to `GGA -> [GST] -> RMC`. Standard five-character GNSS NMEA sentence identifiers (for example GSA, GSV, GNS, GLL, VTG, and ZDA) are retained as epoch metadata and follow those core lines. An output rate of 1-10 Hz is supported; 5 Hz is a practical starting point.

## Build and upload

From this directory:

```text
pio run -e esp32dev
pio run -e esp32-s3-devkitc-1
pio run -e esp32dev -t upload
python tests/static_review.py
```

Select the upload environment and serial port for the board you actually use. The two build environments are compile checks, not a claim that one binary fits every ESP32 board. The host-side static review checks the public placeholders, build targets, documented bounds, known checksum fixtures, absence of a receiver-UART write API, and absence of private path markers; it is not a runtime or hardware test.

## Network behavior

- TCP `28495`: at most two same-subnet clients. NMEA starts without `ACCEPT`.
- UDP `28496`: accepts checksum-valid same-subnet `DISCOVER` requests and returns a unicast `OFFER`.
- Optional mDNS: `_cgbgnss._tcp.local` with `pv`, `id`, and `name` TXT values.
- Wi-Fi reconnects are non-blocking. A lost or changed STA address closes clients and restarts local services.

When enabled, each TCP connection receives exactly one `HELLO`. A valid `ACCEPT` selecting `nmea` enables a one-hertz heartbeat for that connection. Heartbeat `fix=1` requires a current checksum-valid matching GGA/RMC epoch with positive GGA quality, RMC status `A`, and age no greater than 1500 ms. A connection by itself never implies a fix.

All control sentences are printable ASCII, end in CRLF, are at most 512 bytes before CRLF, and use the NMEA XOR of bytes between `$` and `*`. For example:

```text
$PCGBO,ACCEPT,pv=1.0,use=nmea*38\r\n
$PCGBO,DISCOVER,pv=1,nonce=0123456789abcdef*3E\r\n
```

`ACCEPT` is recognized only from a connected same-subnet TCP client. It requires protocol major 1 and `nmea`, rejects duplicate keys and known capabilities that this example did not advertise, and ignores unknown future capability names. `DISCOVER` is recognized only on UDP 28496, requires a 16-32 character lowercase hexadecimal nonce, and produces a unicast reply to that same local sender. No UDP control is sent to the GNSS receiver.

The UART scanner handles fragmented input, rejects non-ASCII and overlength lines, and consumes complete length-framed UBX packets without treating `$` bytes inside their payload as NMEA. Valid NMEA is assembled by UTC, independent of GGA/RMC arrival order; a complete bundle is flushed on the next UTC epoch or after 300 ms without another sentence. Each bundle is limited to 24 lines and 4096 bytes. Overflow or a missing GGA/RMC pair drops that entire epoch, never a partial sentence or partial epoch. The global four-epoch queue drops the oldest whole epoch when full, rejects bundles older than 1500 ms, and is cleared across network restarts. Clients do not have a historical backlog: each HELLO, heartbeat, or epoch uses one `MSG_DONTWAIT` socket send through the public `WiFiClient::fd()`. A partial send or temporary full socket closes that slow client; the firmware never retries into a per-client backlog. The pinned Arduino core's inherited `availableForWrite()` always returns zero and is deliberately not used.

## Safety and verification boundary

Successful compilation proves only that the source builds for the selected PlatformIO board definitions. It does not prove receiver electrical compatibility, correct pin routing on a particular board, RF/GNSS performance, network isolation behavior of a hotspot, sustained output-rate capacity, vehicle behavior, or safe installation. Verify the RX-only pin state with the actual board, test all parser and reconnect cases on a bench, and validate GNSS results independently before any vehicle use.

This example deliberately contains placeholder Wi-Fi credentials and a pseudonymous example ID. Do not publish configured credentials or hardware-derived identifiers.
