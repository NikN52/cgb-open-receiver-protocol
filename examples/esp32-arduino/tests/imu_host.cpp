#include "imu_math.h"

#include <algorithm>
#include <cmath>
#include <cstdint>
#include <cstdlib>
#include <iostream>
#include <limits>
#include <string>

using openrx::AxisMap;
using openrx::BootBias;
using openrx::ImuGate;
using openrx::ImuSample;

namespace {

void check(bool condition, const char* message) {
  if (!condition) {
    std::cerr << "FAIL: " << message << '\n';
    std::exit(1);
  }
}

bool near(float actual, float expected, float tolerance = 0.001f) {
  return std::fabs(actual - expected) <= tolerance;
}

void putBe(uint8_t* output, int16_t value) {
  const uint16_t bits = static_cast<uint16_t>(value);
  output[0] = static_cast<uint8_t>(bits >> 8U);
  output[1] = static_cast<uint8_t>(bits);
}

void putLe(uint8_t* output, int16_t value) {
  const uint16_t bits = static_cast<uint16_t>(value);
  output[0] = static_cast<uint8_t>(bits);
  output[1] = static_cast<uint8_t>(bits >> 8U);
}

unsigned xorBody(const std::string& line) {
  unsigned value = 0;
  for (size_t i = 1; i < line.find('*'); ++i) value ^= static_cast<unsigned char>(line[i]);
  return value;
}

void testAxisMaps() {
  const int permutations[6][3] = {
      {0, 1, 2}, {0, 2, 1}, {1, 0, 2}, {1, 2, 0}, {2, 0, 1}, {2, 1, 0}};
  const char axes[] = "XYZ";
  int accepted = 0;
  for (const auto& permutation : permutations) {
    int inversions = 0;
    for (int i = 0; i < 3; ++i) {
      for (int j = i + 1; j < 3; ++j) inversions += permutation[i] > permutation[j];
    }
    for (int mask = 0; mask < 8; ++mask) {
      std::string text;
      int determinant = (inversions & 1) ? -1 : 1;
      for (int i = 0; i < 3; ++i) {
        const bool negative = (mask & (1 << i)) != 0;
        determinant *= negative ? -1 : 1;
        text += negative ? '-' : '+';
        text += axes[permutation[i]];
      }
      AxisMap map;
      check(map.parse(text.c_str()) == (determinant == 1), "axis handedness classification");
      accepted += map.parse(text.c_str()) ? 1 : 0;
    }
  }
  check(accepted == 24, "exactly 24 proper signed axis rotations");
  for (const char* invalid : {"+X+X+Z", "+X+Y", "+X+Y+z", "X+Y+Z", "+X+Y-Z"}) {
    AxisMap map;
    check(!map.parse(invalid), "invalid axis map rejected");
  }
  AxisMap map;
  check(map.parse("-Y+X+Z"), "known right-handed rotation accepted");
  ImuSample input{};
  input.ms = 7;
  input.gyro[0] = 1; input.gyro[1] = 2; input.gyro[2] = 3;
  input.accel[0] = 4; input.accel[1] = 5; input.accel[2] = 6;
  const ImuSample output = map.apply(input);
  check(output.ms == 7 && output.gyro[0] == -2 && output.gyro[1] == 1 &&
        output.gyro[2] == 3 && output.accel[0] == -5 && output.accel[1] == 4 &&
        output.accel[2] == 6, "axis map applies signs and permutation");
}

void testDecode() {
  uint8_t mpu[14] = {};
  putBe(mpu + 0, 8192); putBe(mpu + 2, -8192); putBe(mpu + 4, 4096);
  putBe(mpu + 6, 1234);  // Temperature is between accel and gyro in the burst.
  putBe(mpu + 8, 655); putBe(mpu + 10, -655); putBe(mpu + 12, 131);
  const ImuSample a = openrx::decodeMpu(mpu, 0xffffffffU);
  check(a.ms == 0xffffffffU && near(a.accel[0], openrx::kGravity) &&
        near(a.accel[1], -openrx::kGravity) && near(a.accel[2], openrx::kGravity / 2) &&
        near(a.gyro[0], 10) && near(a.gyro[1], -10) && near(a.gyro[2], 2),
        "MPU big-endian burst conversion and temperature skip");

  uint8_t lsm[12] = {};
  putLe(lsm + 0, 1000); putLe(lsm + 2, -1000); putLe(lsm + 4, 2000);
  putLe(lsm + 6, 8192); putLe(lsm + 8, -8192); putLe(lsm + 10, 4096);
  const ImuSample b = openrx::decodeLsm(lsm, 42);
  check(b.ms == 42 && near(b.gyro[0], 17.5f) && near(b.gyro[1], -17.5f) &&
        near(b.gyro[2], 35) && near(b.accel[0], 8192 * 0.000122f * openrx::kGravity) &&
        near(b.accel[1], -8192 * 0.000122f * openrx::kGravity),
        "LSM little-endian interleaved burst conversion");
  check(openrx::imuRateHz(1) == 10 && openrx::imuRateHz(50) == 50 &&
        openrx::imuRateHz(1000) == 100, "sample-rate clamp");

  const size_t mpuOffsets[] = {0, 2, 4, 8, 10, 12};
  for (size_t offset : mpuOffsets) {
    uint8_t burst[14] = {};
    putBe(burst + offset, 32759);
    check(!openrx::burstSaturated(burst, true), "MPU value below positive rail accepted");
    putBe(burst + offset, 32760);
    check(openrx::burstSaturated(burst, true), "MPU positive rail detected on every axis");
    putBe(burst + offset, -32759);
    check(!openrx::burstSaturated(burst, true), "MPU value above negative rail accepted");
    putBe(burst + offset, -32760);
    check(openrx::burstSaturated(burst, true), "MPU negative rail detected on every axis");
  }
  uint8_t mpuTemperatureOnly[14] = {};
  putBe(mpuTemperatureOnly + 6, std::numeric_limits<int16_t>::min());
  check(!openrx::burstSaturated(mpuTemperatureOnly, true),
        "MPU temperature word is excluded from six-axis rail detection");

  for (size_t offset = 0; offset < 12; offset += 2) {
    uint8_t burst[12] = {};
    putLe(burst + offset, 32759);
    check(!openrx::burstSaturated(burst, false), "LSM value below positive rail accepted");
    putLe(burst + offset, 32760);
    check(openrx::burstSaturated(burst, false), "LSM positive rail detected on every axis");
    putLe(burst + offset, -32759);
    check(!openrx::burstSaturated(burst, false), "LSM value above negative rail accepted");
    putLe(burst + offset, std::numeric_limits<int16_t>::min());
    check(openrx::burstSaturated(burst, false), "LSM minimum negative rail detected");
  }
}

void testRangeAndFormat() {
  ImuSample sample{};
  sample.ms = 0xffffffffU;
  sample.gyro[0] = 1.25f; sample.gyro[1] = -2.5f; sample.gyro[2] = 3.75f;
  sample.accel[0] = 4; sample.accel[1] = 5; sample.accel[2] = 6;
  check(openrx::inImuRange(sample), "ordinary sample accepted");
  for (int axis = 0; axis < 3; ++axis) {
    ImuSample bad = sample;
    bad.gyro[axis] = 500.01f;
    check(!openrx::inImuRange(bad), "gyro outlier rejected on every axis");
  }
  ImuSample bad = sample;
  bad.accel[1] = std::numeric_limits<float>::infinity();
  check(!openrx::inImuRange(bad), "non-finite sample rejected");
  bad = sample;
  bad.accel[0] = 30; bad.accel[1] = 30; bad.accel[2] = 30;
  check(!openrx::inImuRange(bad), "acceleration norm above 50 rejected");

  char line[160] = {};
  const size_t length = openrx::formatPcgbi(line, sizeof(line), sample);
  check(length > 0 && std::string(line) == "$PCGBI,4294967295,3.75,1.25,-2.50,4.00,5.00,6.00*54\r\n",
        "seven-field order, precision, checksum, and uint32 output");
  check(xorBody(line) == 0x54, "wire checksum independently verified");
  std::cout << line;
  char shortLine[20] = {};
  check(openrx::formatPcgbi(shortLine, sizeof(shortLine), sample) == 0,
        "short output buffer fails instead of truncating");
  sample.gyro[0] = -0.0001f;
  sample.ms = 0;
  check(openrx::formatPcgbi(line, sizeof(line), sample) > 0 &&
        std::string(line).find("-0.00") == std::string::npos, "negative zero removed");
  std::cout << line;

  sample = {};
  sample.accel[0] = 28.867f; sample.accel[1] = 28.867f; sample.accel[2] = 28.867f;
  check(openrx::inImuRange(sample), "pre-round sphere-boundary sample accepted");
  check(openrx::formatPcgbi(line, sizeof(line), sample) > 0, "sphere-boundary sample formatted");
  float x = 0, y = 0, z = 0;
  check(std::sscanf(line, "$PCGBI,%*u,%*f,%*f,%*f,%f,%f,%f", &x, &y, &z) == 3 &&
        x * x + y * y + z * z <= 2500.0f, "rounded wire acceleration remains within sphere");

  // A float sum can round this wire norm down to exactly 2500 even though
  // 0.01^2 + 50.00^2 exceeds the limit. Check the decimal output as doubles.
  sample = {};
  sample.ms = 20;
  sample.accel[0] = 0.01f;
  sample.accel[2] = std::nextafter(50.0f, 0.0f);
  check(openrx::inImuRange(sample), "tiny sphere-boundary sample accepted before rounding");
  check(openrx::formatPcgbi(line, sizeof(line), sample) > 0, "tiny sphere-boundary sample formatted");
  double preciseX = 0, preciseY = 0, preciseZ = 0;
  check(std::sscanf(line, "$PCGBI,%*u,%*f,%*f,%*f,%lf,%lf,%lf", &preciseX, &preciseY, &preciseZ) == 3 &&
        preciseX * preciseX + preciseY * preciseY + preciseZ * preciseZ <= 2500.0,
        "decimal norm cannot escape through float precision");
  std::cout << line;
}

void testGate() {
  ImuGate gate;
  gate.advertised = true;
  gate.helloMs = 1000;
  check(!gate.allows(true, true, 5999) && gate.allows(true, true, 6000),
        "HELLO fallback starts at five seconds");
  gate.accept(false);
  check(!gate.allows(true, true, 9000) && !gate.allows(true, false, 9000),
        "late ACCEPT omission permanently stops both stream modes");
  gate.accept(true);
  check(!gate.allows(true, true, 9001), "stopped connection cannot re-enable IMU");

  ImuGate selected;
  selected.advertised = true;
  selected.accept(true);
  check(selected.allows(true, true, 0), "selected advertised IMU allowed");
  check(!selected.allows(false, true, 0), "absent IMU never allowed");
  ImuGate plain;
  check(plain.allows(true, false, 0), "HELLO-free stream allows present IMU immediately");
  ImuGate wrap;
  wrap.advertised = true;
  wrap.helloMs = 0xfffffff0U;
  check(!wrap.allows(true, true, 4983U) && wrap.allows(true, true, 4984U),
        "five-second gate handles uint32 rollover");
}

ImuSample still(float gyroX = 0.2f, float accelX = 0.0f) {
  ImuSample sample{};
  sample.gyro[0] = gyroX;
  sample.accel[0] = accelX;
  sample.accel[2] = openrx::kGravity;
  return sample;
}

void testBootBias() {
  float bias[3] = {};
  BootBias stable;
  for (int i = 0; i < 20; ++i) stable.add(still(0.25f));
  check(stable.finish(bias) && near(bias[0], 0.25f), "stable boot gyro bias accepted");
  BootBias gyroMotion;
  for (int i = 0; i < 20; ++i) gyroMotion.add(still(i & 1 ? 1.0f : -1.0f));
  check(!gyroMotion.finish(bias), "changing gyro rejects boot bias");
  BootBias accelMotion;
  for (int i = 0; i < 20; ++i) accelMotion.add(still(0.1f, i & 1 ? 1.0f : -1.0f));
  check(!accelMotion.finish(bias), "changing acceleration rejects boot bias");
  BootBias movingMagnitude;
  for (int i = 0; i < 20; ++i) movingMagnitude.add(still(0.1f, 4.0f));
  check(!movingMagnitude.finish(bias), "moving acceleration magnitude rejects boot bias");
}

}  // namespace

int main() {
  testAxisMaps();
  testDecode();
  testRangeAndFormat();
  testGate();
  testBootBias();
  std::cout << "imu host review: PASS\n";
}
