#pragma once

// Arduino-independent wire/math primitives, also compiled by the host checks.
#include <cmath>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstring>

namespace openrx {

constexpr float kGravity = 9.80665f;
constexpr uint32_t kLegacyImuDelayMs = 5000;

constexpr int imuRateHz(int requested) {
  return requested < 10 ? 10 : (requested > 100 ? 100 : requested);
}

struct ImuSample {
  uint32_t ms = 0;
  float gyro[3] = {};
  float accel[3] = {};
};

struct AxisMap {
  uint8_t index[3] = {};
  int8_t sign[3] = {};

  bool parse(const char* text) {
    if (std::strlen(text) != 6) return false;
    unsigned seen = 0;
    int handedness = 1;
    for (size_t i = 0; i < 3; ++i) {
      const char direction = text[2 * i];
      const char axis = text[2 * i + 1];
      if ((direction != '+' && direction != '-') || axis < 'X' || axis > 'Z') return false;
      index[i] = static_cast<uint8_t>(axis - 'X');
      sign[i] = direction == '+' ? 1 : -1;
      if ((seen & (1U << index[i])) != 0) return false;
      seen |= 1U << index[i];
      handedness *= sign[i];
      for (size_t j = 0; j < i; ++j) {
        if (index[j] > index[i]) handedness = -handedness;
      }
    }
    return seen == 7 && handedness == 1;
  }

  ImuSample apply(const ImuSample& sensor) const {
    ImuSample vehicle;
    vehicle.ms = sensor.ms;
    for (size_t i = 0; i < 3; ++i) {
      vehicle.gyro[i] = sign[i] * sensor.gyro[index[i]];
      vehicle.accel[i] = sign[i] * sensor.accel[index[i]];
    }
    return vehicle;
  }
};

inline int16_t signedBigEndian(const uint8_t* bytes) {
  return static_cast<int16_t>((static_cast<uint16_t>(bytes[0]) << 8U) | bytes[1]);
}

inline int16_t signedLittleEndian(const uint8_t* bytes) {
  return static_cast<int16_t>((static_cast<uint16_t>(bytes[1]) << 8U) | bytes[0]);
}

inline bool burstSaturated(const uint8_t* burst, bool mpu) {
  for (size_t i = 0; i < 6; ++i) {
    const int value = mpu ? signedBigEndian(burst + (i < 3 ? 2 * i : 2 * i + 2))
                          : signedLittleEndian(burst + 2 * i);
    // A rail reading cannot describe the real motion beyond the sensor range.
    if (value >= 32760 || value <= -32760) return true;
  }
  return false;
}

inline ImuSample decodeMpu(const uint8_t* burst, uint32_t ms) {
  ImuSample sample;
  sample.ms = ms;
  for (size_t i = 0; i < 3; ++i) {
    sample.accel[i] = signedBigEndian(burst + 2 * i) * (kGravity / 8192.0f);
    sample.gyro[i] = signedBigEndian(burst + 8 + 2 * i) / 65.5f;
  }
  return sample;
}

inline ImuSample decodeLsm(const uint8_t* burst, uint32_t ms) {
  ImuSample sample;
  sample.ms = ms;
  for (size_t i = 0; i < 3; ++i) {
    sample.gyro[i] = signedLittleEndian(burst + 2 * i) * 0.0175f;
    sample.accel[i] = signedLittleEndian(burst + 6 + 2 * i) * (0.000122f * kGravity);
  }
  return sample;
}

inline bool inImuRange(const ImuSample& sample) {
  double normSquared = 0;
  for (size_t i = 0; i < 3; ++i) {
    if (!std::isfinite(sample.gyro[i]) || !std::isfinite(sample.accel[i]) ||
        std::fabs(sample.gyro[i]) > 500.0f) return false;
    normSquared += static_cast<double>(sample.accel[i]) * sample.accel[i];
  }
  return normSquared <= 2500.0f;
}

inline float clampHundredths(float value, float limit) {
  value = std::round(value * 100.0f) / 100.0f;
  if (value > limit) value = limit;
  if (value < -limit) value = -limit;
  return value == 0 ? 0.0f : value;  // Avoid a negative zero on the wire.
}

inline size_t formatPcgbi(char* output, size_t capacity, ImuSample sample) {
  // Reject real outliers before rounding; clipping must not disguise bad data.
  if (!inImuRange(sample)) return 0;
  int32_t normHundredthsSquared = 0;
  for (size_t i = 0; i < 3; ++i) {
    sample.gyro[i] = clampHundredths(sample.gyro[i], 500.0f);
    sample.accel[i] = clampHundredths(sample.accel[i], 50.0f);
    const int32_t hundredths = static_cast<int32_t>(std::round(sample.accel[i] * 100.0f));
    normHundredthsSquared += hundredths * hundredths;
  }
  if (normHundredthsSquared > 25000000) {
    // Rounding all three components can cross the norm limit. Leave enough
    // margin for another 0.005 per component, then round again.
    const float factor = 4999.0f / std::sqrt(static_cast<float>(normHundredthsSquared));
    for (float& value : sample.accel) value = clampHundredths(value * factor, 50.0f);
  }
  char body[128] = {};
  const int count = std::snprintf(body, sizeof(body),
                                "PCGBI,%lu,%.2f,%.2f,%.2f,%.2f,%.2f,%.2f",
                                static_cast<unsigned long>(sample.ms),
                                static_cast<double>(sample.gyro[2]),
                                static_cast<double>(sample.gyro[0]),
                                static_cast<double>(sample.gyro[1]),
                                static_cast<double>(sample.accel[0]),
                                static_cast<double>(sample.accel[1]),
                                static_cast<double>(sample.accel[2]));
  if (count < 0 || static_cast<size_t>(count) >= sizeof(body)) return 0;
  uint8_t checksum = 0;
  for (int i = 0; i < count; ++i) checksum ^= static_cast<uint8_t>(body[i]);
  const int length = std::snprintf(output, capacity, "$%s*%02X\r\n", body,
                                   static_cast<unsigned>(checksum));
  return length > 0 && static_cast<size_t>(length) < capacity ? static_cast<size_t>(length) : 0;
}

struct ImuGate {
  bool advertised = false;
  bool accepted = false;
  bool selected = false;
  bool stopped = false;
  uint32_t helloMs = 0;

  void accept(bool useImu) {
    accepted = true;
    if (!useImu) stopped = true;
    selected = useImu && advertised && !stopped;
  }

  bool allows(bool present, bool protocolHello, uint32_t now) const {
    if (!present) return false;
    if (stopped) return false;
    if (!protocolHello) return true;
    if (!advertised) return false;
    return accepted ? selected : static_cast<uint32_t>(now - helloMs) >= kLegacyImuDelayMs;
  }
};

// A finite boot-only observation. Runtime code never calls add after this window.
struct BootBias {
  uint32_t count = 0;
  double mean[6] = {};
  double m2[6] = {};
  bool plausible = true;

  void add(const ImuSample& sample) {
    ++count;
    float normSquared = 0;
    for (size_t i = 0; i < 3; ++i) {
      normSquared += sample.accel[i] * sample.accel[i];
      if (std::fabs(sample.gyro[i]) > 5.0f) plausible = false;
      const float values[2] = {sample.gyro[i], sample.accel[i]};
      for (size_t group = 0; group < 2; ++group) {
        const size_t axis = i + 3 * group;
        const double delta = values[group] - mean[axis];
        mean[axis] += delta / count;
        m2[axis] += delta * (values[group] - mean[axis]);
      }
    }
    if (!inImuRange(sample) || std::fabs(std::sqrt(normSquared) - kGravity) > 0.75f) {
      plausible = false;
    }
  }

  bool finish(float* bias) const {
    if (!plausible || count < 16) return false;
    for (size_t i = 0; i < 6; ++i) {
      if (m2[i] / (count - 1U) > (i < 3 ? 0.25 : 0.04)) return false;
    }
    float normSquared = 0;
    for (size_t i = 3; i < 6; ++i) normSquared += static_cast<float>(mean[i] * mean[i]);
    if (std::fabs(std::sqrt(normSquared) - kGravity) > 0.5f) return false;
    for (size_t i = 0; i < 3; ++i) bias[i] = static_cast<float>(mean[i]);
    return true;
  }
};

}  // namespace openrx
