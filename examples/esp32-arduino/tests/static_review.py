"""Deterministic public-source checks that do not require an ESP32 board."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "src" / "main.cpp").read_text(encoding="utf-8")
CONFIG = (ROOT / "include" / "config.h").read_text(encoding="utf-8")
PLATFORMIO = (ROOT / "platformio.ini").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def checksum(body: str) -> int:
    value = 0
    for byte in body.encode("ascii"):
        value ^= byte
    return value


def strip_cpp_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)
    return re.sub(r"//.*", "", text)


def main() -> None:
    code = strip_cpp_comments(SOURCE)

    require('"YOUR_WIFI"' in CONFIG, "Wi-Fi SSID must remain a placeholder")
    require('"YOUR_WIFI_PASSWORD"' in CONFIG, "Wi-Fi password must remain a placeholder")
    require("[env:esp32dev]" in PLATFORMIO, "esp32dev environment missing")
    require("[env:esp32-s3-devkitc-1]" in PLATFORMIO, "ESP32-S3 environment missing")

    require("uart_set_pin(kGnssUart, GPIO_NUM_NC" in code, "RX-only UART pin configuration is missing")
    for forbidden in (r"\buart_write_bytes\s*\(", r"\buart_write_chars\s*\(",
                      r"\bSerial1\b", r"\bHardwareSerial\b"):
        require(re.search(forbidden, code) is None, f"receiver write surface found: {forbidden}")
    require("availableForWrite" not in code,
            "WiFiClient inherits an unusable zero-return availableForWrite method")
    require("lwip_send(client.socket.fd(), data, length, MSG_DONTWAIT)" in code,
            "single nonblocking socket send guard is missing")

    require("constexpr size_t kMaxLine = 512" in code, "512-byte line limit changed")
    require("constexpr size_t kMaxEpochBytes = 4096" in code, "epoch byte bound changed")
    require("constexpr size_t kMaxEpochLines = 24" in code, "epoch line bound changed")
    require("constexpr size_t kMaxClients = 2" in code, "client bound changed")
    require("GGA -> [GST] -> RMC" in README, "normalized epoch order is undocumented")

    public_text = "\n".join((SOURCE, CONFIG, PLATFORMIO, README))
    private_path_patterns = (
        r"[A-Za-z]:[\\/](?:Users|Documents and Settings)[\\/]",
        r"(?:file|vscode)://",
        r"/(?:home|Users)/[^/\s]+/",
    )
    for pattern in private_path_patterns:
        require(re.search(pattern, public_text, re.IGNORECASE) is None,
                f"private path pattern found: {pattern}")

    vectors = {
        "GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,": 0x47,
        "GPGST,123519.000,1.0,2.0,1.5,0.0,1.5,2.0,3.0": 0x68,
        "GPRMC,123519,A,4807.038,N,01131.000,E,022.4,084.4,230394,003.1,W": 0x6A,
    }
    for body, expected in vectors.items():
        require(checksum(body) == expected, f"fixture checksum mismatch for {body[:5]}")

    print("static review: PASS")


if __name__ == "__main__":
    main()

