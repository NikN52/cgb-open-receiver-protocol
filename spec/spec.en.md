# CGB Open Receiver Protocol 1.0

Status: **DRAFT — proposal for public discussion**  
Normative language: English. The Russian edition has equal standing: [spec.ru.md](spec.ru.md).

## 1. Purpose and scope

CGB Open Receiver Protocol (ORP) is an open local protocol through which a third-party GNSS receiver or controller supplies NMEA and negotiated supplemental data to a bridge. The baseline profile requires no author-supplied firmware, cloud service, account, or Internet access.

Version 1.0 defines:

- a mandatory NMEA stream;
- device identification and capability negotiation;
- local-network discovery;
- optional UDP push, Bluetooth SPP, and Bluetooth LE NUS;
- optional UBX, IMU, wheel speed, battery state, and diagnostic time;
- security boundaries for command capabilities, which are reserved in 1.0.

The words **MUST**, **MUST NOT**, **SHOULD**, and **MAY** indicate requirement levels.

## 2. Roles and topology

The **device** is a GNSS receiver, UART-to-network board, or application that publishes data. The **bridge** is the client that consumes the data and supplies it to the navigation system.

In the primary profile, the device listens on TCP and the bridge connects to it. Both parties are on the same selected local Wi-Fi/Ethernet network. Typical arrangements are a phone hotspot joined by the device and head unit, or a head-unit hotspot joined by the device. Where client isolation is enabled, use a manual endpoint, another local topology, or Bluetooth.

The protocol defines no cloud relay and does not permit an automatic fallback to cellular, VPN, or a global address.

## 3. Transports and ports

| Purpose | Port / profile | Direction | 1.0 status |
|---|---:|---|---|
| Primary stream | `49761/TCP` | bridge → device connection; data in both directions only as defined below | mandatory baseline transport |
| Discovery and subscription | `49762/UDP` | broadcast request or unicast request to a known selected IP; unicast response and `SUBSCRIBE` | defined |
| UDP push | `49763/UDP` | device → bridge | optional, `udp_push` capability |
| Bluetooth Classic | SPP UUID `00001101-0000-1000-8000-00805F9B34FB` | bidirectional byte stream | optional |
| Bluetooth LE | Nordic UART Service `6E400001-B5A3-F393-E0A9-E50E24DCCA9E` | stream over the characteristics below | optional |
| BLE TX | `6E400003-B5A3-F393-E0A9-E50E24DCCA9E` | device → bridge, notify | required by the BLE profile |
| BLE RX | `6E400002-B5A3-F393-E0A9-E50E24DCCA9E` | bridge → device, write | required by the BLE profile |

TCP segment, SPP packet, and BLE notification boundaries are not message boundaries. A receiver assembles the stream through `CR LF`; this applies even at the minimum BLE MTU.

A device MAY advertise the mDNS service `_cgbgnss._tcp.local.` with an SRV record for the TCP port and TXT keys `pv`, `id`, and `name`. Coordinates, keys, and other secrets MUST NOT be placed in mDNS.

## 4. Common grammar

### 4.1 Lines

All control messages and NMEA sentences are ASCII lines:

```text
$<body>*HH\r\n
```

`HH` is exactly two hexadecimal digits containing the XOR of all ASCII bytes between `$` and `*`, excluding those delimiters. A receiver MUST accept `A–F` in either case; a sender SHOULD use upper case.

A line from `$` through the final `HH` digit is at most **512 ASCII bytes**; `CR LF` is excluded from the limit. A line with a bad checksum, a non-printable byte, missing `CR LF`, or excess length is discarded in full. A new `$` marker before the current line is complete starts a new candidate line.

### 4.2 PCGBO

A control line has this form:

```text
$PCGBO,<TYPE>,key=value,key=value*HH\r\n
```

- `<TYPE>` is an upper-case ASCII token listed below.
- A key consists of `a–z`, `0–9`, and `_`, and starts with a letter.
- A key occurs at most once in a message. Any duplicate invalidates the message.
- Key order is insignificant; examples use a readable order only.
- An unknown optional field is ignored. A missing, duplicate, or invalid required field invalidates the message.
- A simple value uses the unreserved bytes `A–Z a–z 0–9 - . _ ~`. Other data is encoded as `%HH` over its UTF-8 bytes. A decode that is not valid UTF-8 is an error.
- The `id` field is an intentional exception: it is a simple 1–32 character ASCII pseudonym matching `[A-Za-z0-9._~-]`, without percent encoding. Display fields including `name`, `maker`, `fw`, and `msg` support percent-encoded UTF-8.
- `+` separates list elements. In a scalar display field (`name`, `maker`, `fw`, `msg`) and inside a list element, a literal plus is encoded as `%2B`. Raw `:` is not unreserved and is encoded as `%3A`. Empty or repeated list elements are forbidden.
- Numbers use ASCII decimal notation with a dot as the decimal separator and no exponent. Where a field is defined as an integer, signs and fractional parts are forbidden.

Version `pv=1.0` means major 1, minor 0. A discovery request uses `pv=1`, meaning the supported major. HELLO and OFFER MAY advertise a future compatible `1.x`; the receiver ignores unknown optional fields and capabilities. In this edition, ACCEPT and SUBSCRIBE MUST select exactly `pv=1.0`. An unknown major does not enable extensions; the user MAY still explicitly select ordinary NMEA-only TCP without ORP negotiation.

## 5. Identifiers and privacy

| Field | Format | Rule |
|---|---|---|
| `id` | 1–32 ASCII characters `[A-Za-z0-9._~-]`, without percent encoding | configurable device pseudonym; stable across boots |
| `boot` | exactly 8 lower-case hex digits | random value for each device boot |
| `nonce` | 16–32 lower-case hex digits | cryptographically random discovery request identifier |
| `session` | 16–32 lower-case hex digits | cryptographically random UDP subscription identifier |
| `seq` | `uint32`, `0…4294967295` | sequence within one `boot` |
| `ms` | `uint32`, `0…4294967295` | device monotonic milliseconds; rollover is allowed |

`id` MUST NOT be a MAC address, VIN, license plate, human identifier, or other persistent vehicle identifier. The user must be able to change or reset `id`. `boot`, `nonce`, and `session` are not reused as persistent identifiers.

`uint32 seq` comparisons use serial arithmetic: `b` is newer than `a` if `0 < (b-a) mod 2^32 < 2^31`. A zero delta is a duplicate; a delta of at least `2^31` is old or ambiguous.

## 6. TCP/SPP/BLE session states

1. **Disconnected.** The bridge resolves a saved `id` and route or uses a manual endpoint.
2. **Transport connected.** The device sends one valid `HELLO`. It MAY send baseline NMEA at the same time so NMEA-only clients continue to work.
3. **Negotiating.** The bridge validates the major and sends `ACCEPT`, selecting only advertised capabilities it supports. `nmea` is always selected.
4. **Active.** The device sends negotiated data and a 1 Hz `HB`. Unadvertised or unselected extensions are ignored.
5. **Link lost.** Five seconds without any valid message means the session is lost. The transport reconnects and negotiates a new session.

A `boot` change terminates the old logical session. It requires a new `HELLO`; UDP push additionally requires a new `OFFER` binding and a new `session`. A heartbeat shows only that the channel is alive and NEVER establishes GNSS epoch or Fix freshness.

## 7. Negotiation

### 7.1 HELLO

```text
$PCGBO,HELLO,pv=1.0,id=demo01,boot=1a2b3c4d,caps=nmea,name=DIY-GNSS,maker=Example,fw=0.1.0,talkers=GP+GN*2D
```

| Field | Req. | Value |
|---|---|---|
| `pv` | yes | advertised compatible `1.x`; this edition defines `1.0` |
| `id` | yes | configurable pseudonym |
| `boot` | yes | current boot identifier |
| `caps` | yes | list of capabilities actually implemented |
| `name` | no | display name |
| `maker` | no | maker/project |
| `fw` | no | display firmware version |
| `talkers` | no | supported NMEA talker IDs, each exactly `[A-Z]{2}`, such as `GP+GN+GL+GA+GB` |

The device MUST advertise only capabilities it actually implements. Unknown optional capabilities are ignored.

### 7.2 ACCEPT

```text
$PCGBO,ACCEPT,pv=1.0,use=nmea*38
```

| Field | Req. | Value |
|---|---|---|
| `pv` | yes | selected version, `1.0` in this document |
| `use` | yes | non-empty list of selected capabilities; `nmea` is mandatory |

The bridge MUST select only capabilities both advertised by the device and supported by the bridge. Advertising a capability in `HELLO` does not activate it. `rtcm_in` and `cmd_pair` must not be advertised or selected in 1.0.

### 7.3 HB, STATUS, and ERROR

```text
$PCGBO,HB,boot=1a2b3c4d,seq=18,ms=18000,fix=0*67
$PCGBO,STATUS,level=warn,code=GNSS_NO_FIX,ms=18000*79
$PCGBO,ERROR,code=BAD_CHECKSUM*24
```

| Type | Required fields | Optional fields | Rules |
|---|---|---|---|
| `HB` | `boot`, `seq` (`uint32`), `ms` (`uint32`), `fix` (`0` or `1`) | — | 1 Hz after `ACCEPT` only; `fix=1` is device state, not a new epoch |
| `STATUS` | `level` (`info`, `warn`, `error`), `code` | `msg`, `ms` (`uint32`) | state/diagnostics; `msg` is display-only |
| `ERROR` | `code` | `msg` | error for the current request/session |

`code` is a stable upper-case ASCII token with `_`, such as `GNSS_NO_FIX`, `UART_OVERRUN`, `BAD_CHECKSUM`, or `UNSUPPORTED_VERSION`. Logic must not depend on the free-form `msg`.

## 8. Capabilities

| Capability | Status | Data |
|---|---|---|
| `nmea` | mandatory | NMEA 0183 ASCII lines |
| `ubx_rx` | optional | complete inbound UBX frames, at most 4096 bytes |
| `imu_pcgbi` | optional | `$PCGBI` lines |
| `wheel` | optional | `PCGBO,WHEEL` |
| `batt` | optional | `PCGBO,BATT` |
| `time` | optional | `PCGBO,TIME` |
| `udp_push` | optional | subscription and push from section 11 |
| `rtcm_in` | **RESERVED** | MUST NOT be advertised or accepted in 1.0 |
| `cmd_pair` | **RESERVED** | MUST NOT be advertised or accepted in 1.0 |

UBX is permitted only after `ubx_rx` is selected. The receiver reconstructs complete UBX frames using their own length and checksum; `$` inside a payload does not start an ASCII line. A UBX-only profile is not defined: `nmea` remains mandatory.

## 9. NMEA navigation data

| Sentence | Requirement / rate | Bridge semantics |
|---|---|---|
| RMC + GGA | mandatory, 1–10 Hz; 5 Hz recommended | RMC creates a new Fix epoch; GGA adds quality, satellites, HDOP, and altitude |
| GST | strongly recommended for every epoch | solution error estimate |
| GSA, GSV | recommended, normally 1 Hz | used systems and satellite sky view |
| GNS | optional supplement | solution metadata |
| VTG, ZDA | optional | do not replace RMC and do not create a Fix themselves |

Principal NMEA units:

| Field | Format / unit | Rule |
|---|---|---|
| Latitude / longitude | `ddmm.mmmm` / `dddmm.mmmm` plus `N/S` / `E/W` | WGS 84 geographic degrees after conversion |
| RMC UTC / date | `hhmmss.sss` / `ddmmyy` | measurement epoch in UTC |
| RMC speed | knots | multiply by `0.514444` to obtain m/s |
| RMC course | true degrees, `0 ≤ course < 360` | course relative to true north |
| GGA altitude | metres | altitude above mean sea level (MSL) |
| GGA geoid separation | metres | ellipsoid height = MSL altitude + geoid separation |
| GGA HDOP | dimensionless | horizontal dilution of precision |
| GST latitude / longitude sigma | metres, 1σ | horizontal 1σ = `hypot(latitude_sigma, longitude_sigma)` |

The recommended order for one epoch is **GGA → GST → RMC**, with the same, advancing UTC. For an invalid solution, the device sends RMC status `V` and GGA quality `0`. It MUST NOT publish a cached valid position as a new measurement. Identical coordinates while stationary are valid when UTC/epoch advances.

Historical test epoch:

```text
$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47
$GPGST,123519.000,1.0,2.0,1.5,0.0,1.5,2.0,3.0*68
$GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W*6A
```

Expected result: `48.1173, 11.5166667`, 8 satellites, quality 1, altitude 545.4 m, and 2.5 m horizontal GST accuracy. The example date is historical and not current time.

## 10. Supplemental measurements

### 10.1 IMU: PCGBI

The grammar has **exactly 2 or 7 numeric fields** after `PCGBI`:

```text
$PCGBI,<ms>,<gz>*HH\r\n
$PCGBI,<ms>,<gz>,<gx>,<gy>,<ax>,<ay>,<az>*HH\r\n
```

| Field | Unit / range | Rule |
|---|---|---|
| `ms` | `uint32`, milliseconds | device clock interval; rollover is allowed |
| `gx`,`gy`,`gz` | degrees/s, each `-500…+500` | with Z up, positive `gz` is counter-clockwise |
| `ax`,`ay`,`az` | m/s² including gravity; vector norm `≤50` | at rest with Z up, `az≈+9.81` is expected |

Examples:

```text
$PCGBI,123456,12.5*40
$PCGBI,123456,12.5,0.1,-0.2,0.00,0.00,9.81*5C
```

10–100 samples/s are allowed; 50 is recommended. A repeated `ms` is discarded. A backwards move of half the `uint32` range or more is treated as a reboot; a gap over 1000 ms in `ms` or arrival time starts a new integration segment. Vehicle heading additionally uses a 2 rad/s (about 114.6°/s) vertical-rate limit; a faster valid sample may remain diagnostic but does not affect heading. A gyroscope alone cannot provide absolute north.

### 10.2 WHEEL

```text
$PCGBO,WHEEL,ms=1000,v_mps=4.20,dir=fwd,sigma_mps=0.10*33
```

| Field | Req. | Unit / value |
|---|---|---|
| `ms` | yes | `uint32`, device milliseconds |
| `v_mps` | yes | finite non-negative number, m/s |
| `dir` | yes | `fwd`, `rev`, or `unknown` |
| `sigma_mps` | no | finite non-negative 1σ, m/s |

WHEEL initially remains a separate measurement/diagnostic. Use for dead reckoning requires independent source selection and validation; the capability alone is not proof.

### 10.3 BATT

```text
$PCGBO,BATT,ms=1000,mv=4080,pct=78,charging=0*39
```

| Field | Req. | Unit / value |
|---|---|---|
| `ms` | yes | `uint32`, device milliseconds |
| `mv` | conditional | integer `0…100000`, mV |
| `pct` | conditional | number `0…100`, % |
| `charging` | no | `0` or `1` |

At least one measurement, `mv` or `pct`, must be present. A missing value remains unknown rather than zero.

### 10.4 TIME

```text
$PCGBO,TIME,ms=1000,utc_ms=1735689600000,sigma_ms=20,src=gnss*34
```

| Field | Req. | Unit / value |
|---|---|---|
| `ms` | yes | `uint32`, device milliseconds |
| `utc_ms` | yes | non-negative Unix UTC milliseconds |
| `sigma_ms` | yes | finite non-negative 1σ, ms |
| `src` | yes | `gnss` or `rtc` |

TIME is diagnostic clock correlation only. Navigation UTC comes from GNSS RMC; TIME does not set bridge time and is not proof of Fix freshness. Local measurement age is determined from monotonic `ms`, `boot`, and arrival time, not by directly subtracting unsynchronized system clocks.

## 11. Discovery and UDP push

### 11.1 DISCOVER / OFFER

The bridge sends DISCOVER to `49762/UDP`: broadcast on each explicitly selected eligible local interface, or unicast to a known selected IP. The device replies by unicast to the source IP and source port:

```text
$PCGBO,DISCOVER,pv=1,nonce=0123456789abcdef*3E
$PCGBO,OFFER,pv=1.0,nonce=0123456789abcdef,id=demo01,tcp=49761,name=DIY-GNSS,caps=nmea*4F
```

| Type | Required fields | Optional fields |
|---|---|---|
| `DISCOVER` | `pv=1`, `nonce` | — |
| `OFFER` | compatible `pv=1.x`, matching `nonce`, `id`, `tcp` (`1…65535`) | `name`, `caps` |

The device address comes from the received OFFER source IP and is not carried in a field. The bridge matches the nonce, presents candidates to the user, and saves the selected `id` and route; it resolves the IP again after a network change. OFFER establishes neither trust nor command authority.

### 11.2 SUBSCRIBE

When `udp_push` is negotiated, the bridge sends this unicast message to device port `49762/UDP`:

```text
$PCGBO,SUBSCRIBE,pv=1.0,session=0123456789abcdef,port=49763,lease=10*35
```

All fields are required: `pv=1.0`, random `session`, `port=49763`, and `lease=10` seconds. The bridge renews the lease every 5 seconds. The device stops push when the lease expires. `SUBSCRIBE` does not allow reverse commands and cannot redirect traffic to another IP: the device sends only to the request source IP.

### 11.3 DATA

Every UDP datagram starts with a checksummed header and contains only complete checksummed NMEA lines:

```text
$PCGBO,DATA,session=0123456789abcdef,boot=1a2b3c4d,seq=19,ms=18200,age_ms=12*71\r\n
$GPGGA,...*HH\r\n
$GPGST,...*HH\r\n
$GPRMC,...*HH\r\n
```

| Field | Rule |
|---|---|
| `session` | must match the active subscription |
| `boot` | must match the selected device/boot |
| `seq` | `uint32`, serial arithmetic; duplicates and old values are discarded |
| `ms` | `uint32`, device measurement creation time |
| `age_ms` | `uint32`, age of the oldest included epoch at send time |

The complete datagram is at most **1200 bytes**. Source IP, `session`, and `boot` must match the selected binding; the source UDP port is not an identity and MAY differ. A datagram with `age_ms > 1000`, an invalid header, a partial line, or a bad checksum is discarded in full. A `boot` change requires a new HELLO/OFFER binding and a new session.

## 12. Security

- The bridge MUST bind its socket to the selected local interface and validate source IP/subnet. An unexpected cellular/VPN/global route is forbidden.
- Received `name`, `maker`, `fw`, `msg`, and unknown fields are untrusted input: the common line limit applies and display output is escaped.
- A baseline implementation writes nothing to the GNSS receiver UART. HELLO, ACCEPT, discovery, subscription, and heartbeat terminate at the network controller/application.
- Version 1.0 provides no authentication or encryption, so it is intended only for a selected trusted local network or Bluetooth link. It must not be exposed to the Internet.
- `rtcm_in` and `cmd_pair` are reserved. RTCM input, arbitrary configuration commands, OTA, and UBX writes are forbidden until a separate secure-pairing specification exists. A future profile must use a short-lived code, PAKE, an authenticated session, sequence numbers, and replay protection; it is not part of 1.0.

## 13. Bridge actions and support status

This specification defines a wire protocol; it does not promise every extension in a particular build.

**Available now:** Car GNSS Bridge 0.27.90 accepts ordinary NMEA TCP through the existing “TCP Network” receiver: Settings → Receiver → Receivers… → TCP Network, then enter the device IP and port `49761`. ORP discovery, HELLO/ACCEPT, and extensions are not required in this mode.

**DRAFT / soon:** a dedicated “Open Protocol” receiver and negotiated extensions are under discussion. An implementation must consume PCGBO as control data, keep separate indicators for “channel alive,” “GNSS epoch received,” and “new Fix received,” and never treat HB or STATUS as navigation freshness. IMU, WHEEL, and BATT remain separate sensor/diagnostic data until their application is validated independently.

## 14. Minimal UART-to-Wi-Fi example requirements

A minimal board:

1. joins a configured Wi-Fi STA network;
2. listens for at most two TCP clients on `49761`;
3. sends each client HELLO with `caps=nmea` and supports ACCEPT/HB;
4. reads UART in the receive direction only, assembles a whole NMEA epoch, verifies XOR, and queues whole epochs in a bounded queue;
5. drops the oldest whole epochs, not individual lines, on overflow;
6. replies to a valid DISCOVER with a unicast OFFER;
7. MAY advertise mDNS;
8. writes nothing to UART and advertises no capability it has not implemented.

## 15. Conformance checklist

- [ ] Every line is ASCII, CRLF-terminated, at most 512 bytes excluding CRLF, and has a correct XOR.
- [ ] Duplicate keys and invalid percent encoding are rejected; `id` matches `[A-Za-z0-9._~-]{1,32}` and is not percent encoded.
- [ ] HELLO includes `pv,id,boot,caps`; ACCEPT always includes `nmea` and selects nothing unadvertised.
- [ ] `rtcm_in` and `cmd_pair` are neither advertised nor accepted.
- [ ] HB is 1 Hz after ACCEPT; 5 seconds of total silence loses the session; HB does not update GNSS freshness.
- [ ] RMC and GGA are 1–10 Hz with advancing UTC; invalid means RMC `V` and GGA quality `0`; a cached valid fix is not replayed as new.
- [ ] Bad checksum, a 513-byte line, byte-wise TCP/BLE fragmentation, unknown field/minor/major, and HB-only input are tested.
- [ ] UDP duplicate, reorder, foreign source IP/session/boot, `age_ms>1000`, reboot, and the 1200-byte limit are tested.
- [ ] Both exact IMU forms (2/7 fields), rollover, duplicate, reboot, >1 s gap, and unit limits are tested.
- [ ] A complete UBX frame up to 4096 bytes, bad checksum, and `$` inside binary payload are tested.
- [ ] Both hotspot topologies, manual IP, network change, and no cellular/VPN escape are tested.
- [ ] Failure of one device does not reset other working sources.

## 16. Compatibility and protocol changes

An incompatible change requires a new major. A new minor MAY add only optional fields, types, or capabilities that a 1.x receiver can safely ignore. Normative changes are made in the Russian and English editions together and recorded in the [CHANGELOG](../CHANGELOG.md).

## 17. History

- **1.0 DRAFT:** initial public proposal: baseline NMEA, HELLO/ACCEPT/HB, discovery, UDP push, UBX RX, PCGBI, WHEEL, BATT, TIME, and reserved secure pairing.

## 18. License

The specification text is licensed under [Creative Commons Attribution 4.0 International](../LICENSE-SPEC.md). Copyright © 2026 Nikolay Pavlov.
