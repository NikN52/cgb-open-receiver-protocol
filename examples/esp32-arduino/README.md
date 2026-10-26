# CGB Open Receiver Protocol: ESP32 Arduino example

This public PlatformIO example receives NMEA from a GNSS receiver over an RX-only physical connection and serves complete GNSS epochs on the local Wi-Fi network. An optional I2C gyroscope/accelerometer adds proprietary `$PCGBI` samples. It advertises `caps=nmea+imu_pcgbi` when the IMU is present, otherwise `caps=nmea`. There are no receiver commands, RTCM input, cloud services, or Internet requests.

## Wiring and configuration

For GNSS, connect only three signals:

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
- optionally disable protocol HELLO for a plain stream, disable mDNS, or set `OPENRX_ENABLE_IMU=0` to omit the IMU.

The normative defaults are `OPENRX_TCP_PORT=28495` and
`OPENRX_DISCOVERY_PORT=28496`; a deployment-specific override belongs in the
ignored local configuration.

`id` must be 1-32 unreserved ASCII characters and must be user-assigned. Display fields in the local configuration are wire values: percent-encode UTF-8 bytes outside the unreserved set and encode a literal plus as `%2B`. Invalid configuration disables network services instead of falling back to a hardware-derived name.

Configure the receiver itself to emit checksum-valid `GGA` and `RMC`, with `GST` recommended, for the same UTC epoch. Either GGA-before-RMC or RMC-before-GGA input is accepted; the TCP bundle is normalized to `GGA -> [GST] -> RMC`. Standard five-character GNSS NMEA sentence identifiers (for example GSA, GSV, GNS, GLL, VTG, and ZDA) are retained as epoch metadata and follow those core lines. An output rate of 1-10 Hz is supported; 5 Hz is a practical starting point.

## Optional I2C IMU

`OPENRX_ENABLE_IMU` defaults to `1`: the example automatically probes the
following devices using Arduino `Wire`, with no external IMU library:

| Family | I2C addresses | Accepted WHO_AM_I |
|---|---|---|
| MPU-6050 / MPU-6500 / MPU-9250-compatible | `0x68`, `0x69` | `0x68`, `0x70`, `0x71`, `0x73` |
| LSM6DS3 / LSM6DSL / LSM6DSO | `0x6A`, `0x6B` | `0x69`, `0x6A`, `0x6C` |

Connect a **3.3 V-compatible module**, checking its supply requirements:

| IMU module | `esp32dev` | `esp32-s3-devkitc-1` |
|---|---|---|
| VCC / 3V3 | 3.3 V | 3.3 V |
| GND | GND | GND |
| SDA | GPIO21 | GPIO8 |
| SCL | GPIO22 | GPIO9 |

Override `OPENRX_IMU_SDA_PIN` and `OPENRX_IMU_SCL_PIN` for other wiring.
Pull-ups are usually already fitted to the module; they must pull SDA/SCL to
3.3 V. Never pull these ESP32 pins to 5 V. Keep wires short and check that the
selected pins do not conflict with the GNSS RX pin or other board functions.
No IMU interrupt or magnetometer connection is needed.

Mount the IMU rigidly to the car body. Any axis-aligned mounting orientation
can be described with `OPENRX_IMU_AXIS_MAP`, whose three signed sensor axes
become vehicle **X forward, Y left, Z up**, respectively. For example,
`"+Y-X+Z"` means vehicle X = sensor Y, vehicle Y = negative sensor X, and
vehicle Z = sensor Z. Each axis must appear once and the map must preserve
right-handedness. An invalid map disables the IMU with a serial message;
GNSS continues. A signed axis map cannot correct an arbitrary tilted mounting,
so align the sensor axes with the body axes before choosing the map.
Positive `gz` means counter-clockwise rotation viewed from above.

At startup the serial **level check** prints which signed sensor axis and
which mapped vehicle axis see gravity, plus the acceleration norm. On a level
stationary car the mapped reading should be approximately **+Z, 9.81 m/s²**.
Check this and the yaw sign with the actual installation before using heading.

The first successful boot detection collects about **2 seconds** of gyro and
acceleration observations in `loop()`. Keep the device still. A bias is used
only if gyro/accel variance is small, gyro readings are near zero, and gravity
is plausible. Motion or an implausible gravity check leaves zero bias and
prints a serial note. This is a stillness heuristic, not proof of no motion.
The bias is never learned or adapted while running; a reconnect to the same
sensor retains a successful boot bias. Detection after a failed boot probe
does not recalibrate. The bridge performs its own standstill calibration.

`OPENRX_IMU_RATE_HZ` defaults to **50 Hz** and is clamped to **10-100 Hz**.
The sensor ranges are ±500 deg/s and ±4 g; gyro and acceleration are read in
one burst. Hardware filtering is configured per sensor (the original DS3 has
a different register layout from DSL/DSO), supplemented by a one-pole software
low-pass at 20 Hz for the default rate. The software cutoff is reduced to
0.4 times the output rate below 50 Hz. Gravity is retained. The seven-field
form contains `millis()` at sample acquisition, `gz,gx,gy` in deg/s and
`ax,ay,az` in m/s², all with two decimal places and a valid NMEA XOR checksum.
Samples exceeding ±500 deg/s on any gyro axis or an acceleration norm of
50 m/s² are dropped before bias correction/filtering; rounded output is also
bounded. Samples at a raw sensor rail are also dropped because clipped
acceleration cannot describe motion beyond ±4 g. `$PCGBI` is written only
between complete GNSS epoch bundles.

IMU reset, settling, calibration, and sample timing use `millis()`/`micros()`
deadlines; they do not wait in a delay loop. Initialization reads configured
registers back before making the IMU available. Each synchronous I2C transaction
has a 2 ms timeout, and the UART is drained before and after IMU service.
After **5 consecutive I2C failures** the IMU becomes unavailable and a serial
message is printed. A missing or failed IMU is probed again every **30 s**.
GNSS forwarding continues; stale IMU samples are not queued for later replay.

Car GNSS Bridge **0.27.x** already parses `$PCGBI` from its plain TCP/NMEA
receiver, but that receiver does not send ORP `ACCEPT`. The example therefore
starts PCGBI **5 seconds after HELLO** if no valid ACCEPT has arrived and the
IMU was advertised on that connection. A negotiated connection receives it
only if `use` includes `imu_pcgbi`. A later valid ACCEPT omitting that
capability stops PCGBI permanently for that connection. If detection succeeds
after a connection's HELLO omitted the capability, reconnect to advertise it.
With `OPENRX_ENABLE_PROTOCOL_HELLO=0`, PCGBI is sent as soon as a present IMU
has completed its boot check. A receiver that does not understand PCGBI
ignores the proprietary sentence.

The bridge can use yaw rate to propagate heading **between GNSS fixes**.
A gyroscope alone gives **no absolute north**, and heading integration drifts;
physical installation and vehicle behavior still need independent validation.

Register references: [MPU-6050](https://invensense.tdk.com/wp-content/uploads/2015/02/MPU-6000-Register-Map1.pdf),
[MPU-6500](https://invensense.tdk.com/wp-content/uploads/2015/02/MPU-6500-Register-Map2.pdf),
[MPU-9250](https://invensense.tdk.com/wp-content/uploads/2015/02/RM-MPU-9250A-00-v1.6.pdf),
[original LSM6DS3](https://content.arduino.cc/assets/st_imu_lsm6ds3_datasheet.pdf),
[LSM6DSL](https://www.st.com/resource/en/datasheet/lsm6dsl.pdf),
[LSM6DSO](https://www.st.com/resource/en/datasheet/lsm6dso.pdf).

## Build and upload

From this directory:

```text
pio run -e esp32dev
pio run -e esp32-s3-devkitc-1
pio run -e esp32dev -t upload
python tests/static_review.py
python tests/host_review.py
```

Select the upload environment and serial port for the board you actually use. The two build environments are compile checks, not a claim that one binary fits every ESP32 board. The static review checks public defaults, bounds, IMU code paths, checksums, and the RX-only UART boundary. `host_review.py` needs a host C++ compiler (`--compiler` or `CXX` can select it). It compiles the actual portable firmware primitives, checks conversion, axes, range/rounding/checksum, boot bias and per-connection gates, then parses the emitted sentences with the Python wire validator. These checks do not exercise physical I2C, sensors or a vehicle.

## Network behavior

- TCP `28495`: at most two same-subnet clients. NMEA starts without `ACCEPT`.
- UDP `28496`: accepts checksum-valid same-subnet `DISCOVER` requests and returns a unicast `OFFER`.
- Optional mDNS: `_cgbgnss._tcp.local` with `pv`, `id`, and `name` TXT values.
- Wi-Fi reconnects are non-blocking. A lost or changed STA address closes clients and restarts local services.

When enabled, each TCP connection receives exactly one `HELLO`, advertising IMU availability at that time. A valid `ACCEPT` selecting `nmea` enables a one-hertz heartbeat for that connection; `imu_pcgbi` is optional. Heartbeat `fix=1` requires a current checksum-valid matching GGA/RMC epoch with positive GGA quality, RMC status `A`, and age no greater than 1500 ms. A connection by itself never implies a fix.

All control sentences are printable ASCII, end in CRLF, are at most 512 bytes before CRLF, and use the NMEA XOR of bytes between `$` and `*`. For example:

```text
$PCGBO,ACCEPT,pv=1.0,use=nmea*38\r\n
$PCGBO,DISCOVER,pv=1,nonce=0123456789abcdef*3E\r\n
```

`ACCEPT` is recognized only from a connected same-subnet TCP client. It requires protocol major 1 and `nmea`, rejects duplicate keys and known capabilities that this example did not advertise, and ignores unknown future capability names. `DISCOVER` is recognized only on UDP 28496, requires a 16-32 character lowercase hexadecimal nonce, and produces a unicast reply to that same local sender. No UDP control is sent to the GNSS receiver.

The UART scanner handles fragmented input, rejects non-ASCII and overlength lines, and consumes complete length-framed UBX packets without treating `$` bytes inside their payload as NMEA. Valid NMEA is assembled by UTC, independent of GGA/RMC arrival order; a complete bundle is flushed on the next UTC epoch or after 300 ms without another sentence. Each bundle is limited to 24 lines and 4096 bytes. Overflow or a missing GGA/RMC pair drops that entire epoch, never a partial sentence or partial epoch. The global four-epoch queue drops the oldest whole epoch when full, rejects bundles older than 1500 ms, and is cleared across network restarts. Clients do not have a historical backlog: each HELLO, heartbeat, PCGBI sample, or epoch uses one `MSG_DONTWAIT` socket send through the public `WiFiClient::fd()`. A partial send or temporary full socket closes that slow client; the firmware never retries into a per-client backlog. The pinned Arduino core's inherited `availableForWrite()` always returns zero and is deliberately not used.

## Safety and verification boundary

Successful compilation proves only that the source builds for the selected PlatformIO board definitions. It does not prove receiver electrical compatibility, correct pin routing on a particular board, RF/GNSS performance, network isolation behavior of a hotspot, sustained output-rate capacity, vehicle behavior, or safe installation. Verify the RX-only pin state with the actual board, test all parser and reconnect cases on a bench, and validate GNSS results independently before any vehicle use.

This example deliberately contains placeholder Wi-Fi credentials and a pseudonymous example ID. Do not publish configured credentials or hardware-derived identifiers.
