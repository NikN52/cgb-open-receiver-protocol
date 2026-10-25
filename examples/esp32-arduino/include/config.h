#pragma once

#if __has_include("config.local.h")
#include "config.local.h"
#endif

// Replace these two placeholders before flashing.
#ifndef OPENRX_WIFI_SSID
#define OPENRX_WIFI_SSID "YOUR_WIFI"
#endif
#ifndef OPENRX_WIFI_PASSWORD
#define OPENRX_WIFI_PASSWORD "YOUR_WIFI_PASSWORD"
#endif

// Stable, user-assigned pseudonyms. Do not derive these values from a MAC address.
#ifndef OPENRX_DEVICE_ID
#define OPENRX_DEVICE_ID "openrx-demo"
#endif
#ifndef OPENRX_MDNS_HOST
#define OPENRX_MDNS_HOST "openrx-demo"
#endif

// Optional HELLO fields are protocol wire values. Percent-encode UTF-8 bytes
// outside the unreserved ASCII set; encode a literal plus as %2B.
#ifndef OPENRX_DEVICE_NAME
#define OPENRX_DEVICE_NAME "Open-GNSS"
#endif
#ifndef OPENRX_MAKER
#define OPENRX_MAKER "Example"
#endif
#ifndef OPENRX_FIRMWARE_VERSION
#define OPENRX_FIRMWARE_VERSION "1.0.0"
#endif
#ifndef OPENRX_TALKERS
#define OPENRX_TALKERS "GP+GN"
#endif

// Supported receiver baud rates: 9600, 38400, or 115200.
#ifndef OPENRX_GNSS_BAUD
#define OPENRX_GNSS_BAUD 115200
#endif
#ifndef OPENRX_GNSS_RX_PIN
#define OPENRX_GNSS_RX_PIN 16
#endif

// Normative ORP ports. Override locally only when the deployment requires it.
#ifndef OPENRX_TCP_PORT
#define OPENRX_TCP_PORT 28495
#endif
#ifndef OPENRX_DISCOVERY_PORT
#define OPENRX_DISCOVERY_PORT 28496
#endif

// Disabling HELLO also disables ACCEPT parsing and heartbeat output, leaving a
// pure NMEA-only TCP stream.
#ifndef OPENRX_ENABLE_PROTOCOL_HELLO
#define OPENRX_ENABLE_PROTOCOL_HELLO 1
#endif
#ifndef OPENRX_ENABLE_MDNS
#define OPENRX_ENABLE_MDNS 1
#endif

