"""Deterministic public-source checks that do not require an ESP32 board."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "src" / "main.cpp").read_text(encoding="utf-8")
CONFIG = (ROOT / "include" / "config.h").read_text(encoding="utf-8")
IMU_HEADER = (ROOT / "include" / "imu.h").read_text(encoding="utf-8")
IMU_SOURCE = (ROOT / "src" / "imu.cpp").read_text(encoding="utf-8")
PLATFORMIO = (ROOT / "platformio.ini").read_text(encoding="utf-8")
README = (ROOT / "README.md").read_text(encoding="utf-8")
PUBLIC_SOURCES = [path for folder in (ROOT / "include", ROOT / "src")
                  for path in folder.glob("*")
                  if path.name != "config.local.h" and path.suffix in {".h", ".hpp", ".c", ".cpp"}]
TEST_SOURCES = [path for path in (ROOT / "tests").glob("*")
                if path.suffix in {".py", ".h", ".hpp", ".c", ".cpp"}]


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
    config_defaults = {
        "OPENRX_ENABLE_PROTOCOL_HELLO": "1",
        "OPENRX_ENABLE_IMU": "1",
        "OPENRX_IMU_RATE_HZ": "50",
        "OPENRX_IMU_AXIS_MAP": '"+X+Y+Z"',
    }
    for macro, default in config_defaults.items():
        require(re.search(rf"#define\s+{macro}\s+{re.escape(default)}(?:\s|$)", CONFIG) is not None,
                f"public default missing for {macro}")
    for macro in ("OPENRX_IMU_SDA_PIN", "OPENRX_IMU_SCL_PIN"):
        require(f"#ifndef {macro}" in CONFIG and re.search(rf"#define\s+{macro}\s+\d+", CONFIG),
                f"public pin default missing for {macro}")
    require(re.search(r"CONFIG_IDF_TARGET_ESP32S3.*?OPENRX_IMU_SDA_PIN\s+8.*?"
                      r"OPENRX_IMU_SDA_PIN\s+21", CONFIG, re.DOTALL) is not None,
            "S3/non-S3 SDA defaults changed")
    require(re.search(r"CONFIG_IDF_TARGET_ESP32S3.*?OPENRX_IMU_SCL_PIN\s+9.*?"
                      r"OPENRX_IMU_SCL_PIN\s+22", CONFIG, re.DOTALL) is not None,
            "S3/non-S3 SCL defaults changed")

    imu_code = strip_cpp_comments(IMU_SOURCE)
    imu_header = strip_cpp_comments(IMU_HEADER)
    require(re.search(r"#if\s+OPENRX_ENABLE_IMU\s+#include\s+<Wire\.h>\s+#endif", IMU_SOURCE) is not None,
            "Wire include is not confined to the optional IMU build")
    require("Wire.setTimeOut(2)" in imu_code and
            "Wire.begin(OPENRX_IMU_SDA_PIN, OPENRX_IMU_SCL_PIN, 400000)" in imu_code,
            "bounded 400 kHz Wire setup missing")
    for identity in ("0x68", "0x70", "0x71", "0x73", "0x69", "0x6a", "0x6c"):
        require(identity in imu_code, f"supported IMU WHO_AM_I missing: {identity}")
    require("mpu_ ? 0x75 : 0x0f" in imu_code, "WHO_AM_I register selection missing")
    require(re.search(r"readRegisters\(mpu_\s*\?\s*0x3b\s*:\s*0x22,\s*burst,\s*"
                      r"mpu_\s*\?\s*14U\s*:\s*12U\)", imu_code) is not None,
            "single bounded MPU/LSM burst read missing")
    require("burstSaturated(burst, mpu_)" in imu_code and
            imu_code.index("burstSaturated(burst, mpu_)") < imu_code.index("latest_ = vehicle"),
            "raw burst rail rejection must precede publication")
    begin_body = imu_code[imu_code.index("void Imu::begin()"):
                          imu_code.index("bool Imu::present()")]
    require(begin_body.index("axes_.parse(OPENRX_IMU_AXIS_MAP)") < begin_body.index("Wire.begin(") and
            "return;" in begin_body[:begin_body.index("Wire.begin(")],
            "invalid axis map must disable IMU before opening Wire")
    require("kMaxFailures = 5" in imu_header and "kRetryMs = 30000" in imu_header,
            "failure threshold or retry interval changed")
    require(re.search(r"if\s*\(\+\+failures_\s*>=\s*kMaxFailures\)\s*retry\(message\)",
                      imu_code) is not None and
            "nextActionMs_ = millis() + kRetryMs" in imu_code,
            "five-error transition to the 30-second retry path missing")
    require("kCalibrationMs = 2000" in imu_header and "calibration_.add(vehicle)" in imu_code and
            "calibration_.finish(bias_)" in imu_code,
            "finite two-second boot bias path missing")
    require(re.search(r"Stage::Running.*?millis\(\)\s*-\s*calibrationStartMs_.*?"
                      r">=\s*kCalibrationMs.*?finishCalibration\(\)", imu_code, re.DOTALL) is not None,
            "service must close boot calibration even while samples are rejected")
    require("verifyConfig_ = true" in imu_code and
            re.search(r"readRegisters\(setting\.reg,\s*&value,\s*1\).*?setting\.mask",
                      imu_code, re.DOTALL) is not None,
            "configuration write/readback verification path missing")
    require("who_ == 0x68 ? 6U : 7U" in imu_code,
            "MPU6050 must omit the newer-family register 0x1d write")

    main_code = strip_cpp_comments(SOURCE)
    require("client.imuGate.advertised = imu.present()" in main_code,
            "per-client IMU advertisement snapshot missing")
    require(re.search(r"validAccept\(client\.input,\s*client\.inputLength,\s*"
                      r"client\.imuGate\.advertised,\s*selectedImu\).*?"
                      r"client\.imuGate\.accept\(selectedImu\)", main_code, re.DOTALL) is not None,
            "valid ACCEPT selection is not applied to the per-client IMU gate")
    require("client.imuGate.allows(imu.present(), OPENRX_ENABLE_PROTOCOL_HELLO != 0, millis())" in main_code,
            "per-client PCGBI gate missing")
    require(main_code.index("while (popEpoch(epoch))") < main_code.index("if (imu.takeSample(sample))"),
            "PCGBI must be submitted only after the complete epoch loop")

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

    public_text = "\n".join([PLATFORMIO, README] +
                            [path.read_text(encoding="utf-8")
                             for path in PUBLIC_SOURCES + TEST_SOURCES])
    require("delay(" not in strip_cpp_comments("\n".join(
        path.read_text(encoding="utf-8") for path in PUBLIC_SOURCES
        if path.name.startswith("imu"))), "IMU implementation must remain nonblocking")
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

