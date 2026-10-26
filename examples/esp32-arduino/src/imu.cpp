#include <Arduino.h>

#include "imu.h"

#if OPENRX_ENABLE_IMU
#include <Wire.h>
#endif

namespace openrx {

#if OPENRX_ENABLE_IMU

void Imu::begin() {
  if (!axes_.parse(OPENRX_IMU_AXIS_MAP)) {
    Serial.println("IMU disabled: invalid OPENRX_IMU_AXIS_MAP (use a right-handed signed XYZ permutation)");
    return;
  }
  if (OPENRX_IMU_SDA_PIN < 0 || OPENRX_IMU_SCL_PIN < 0 ||
      OPENRX_IMU_SDA_PIN == OPENRX_IMU_SCL_PIN ||
      OPENRX_IMU_SDA_PIN == OPENRX_GNSS_RX_PIN || OPENRX_IMU_SCL_PIN == OPENRX_GNSS_RX_PIN) {
    Serial.println("IMU disabled: invalid/conflicting I2C pins");
    return;
  }
  if (!Wire.begin(OPENRX_IMU_SDA_PIN, OPENRX_IMU_SCL_PIN, 400000)) {
    retry("IMU I2C setup failed");
    return;
  }
  Wire.setTimeOut(2);  // Bound each synchronous I2C transaction to 2 ms.
  stage_ = Stage::Probe;
}

bool Imu::present() const { return stage_ == Stage::Running; }

bool Imu::takeSample(ImuSample& output) {
  if (!pending_ || !present()) return false;
  output = latest_;
  pending_ = false;
  return true;
}

bool Imu::readRegisters(uint8_t reg, uint8_t* bytes, size_t length) {
  Wire.beginTransmission(address_);
  Wire.write(reg);
  if (Wire.endTransmission(false) != 0) return false;
  if (Wire.requestFrom(address_, length, true) != length) return false;
  for (size_t i = 0; i < length; ++i) {
    const int value = Wire.read();
    if (value < 0) return false;
    bytes[i] = static_cast<uint8_t>(value);
  }
  return true;
}

bool Imu::writeRegister(uint8_t reg, uint8_t value) {
  Wire.beginTransmission(address_);
  Wire.write(reg);
  Wire.write(value);
  return Wire.endTransmission(true) == 0;
}

void Imu::retry(const char* message) {
  Serial.printf("%s; IMU unavailable, detection retry in 30 s\n", message);
  stage_ = Stage::Retry;
  pending_ = false;
  filterValid_ = false;
  calibrating_ = false;
  bootWindow_ = false;  // Reconnecting hardware must never learn a runtime bias.
  nextActionMs_ = millis() + kRetryMs;
}

void Imu::failed(const char* message) {
  pending_ = false;
  filterValid_ = false;
  if (++failures_ >= kMaxFailures) retry(message);
}

void Imu::probe() {
  static const uint8_t addresses[] = {0x68, 0x69, 0x6a, 0x6b};
  address_ = addresses[probe_];
  mpu_ = probe_ < 2;
  uint8_t who = 0;
  const bool found = readRegisters(mpu_ ? 0x75 : 0x0f, &who, 1) &&
                     (mpu_ ? (who == 0x68 || who == 0x70 || who == 0x71 || who == 0x73)
                           : (who == 0x69 || who == 0x6a || who == 0x6c));
  if (found) {
    // Reuse a frozen boot bias only for the same sensor identity. A different
    // sensor starts at zero, without collecting a new runtime calibration.
    if (who != biasWho_ || address_ != biasAddress_ || mpu_ != biasMpu_) {
      for (float& value : bias_) value = 0;
    }
    who_ = who;
    failures_ = 0;
    stage_ = Stage::Reset;
    return;
  }
  if (++probe_ == sizeof(addresses)) retry("IMU not detected");
}

void Imu::configure() {
  struct Setting { uint8_t reg; uint8_t value; uint8_t mask; };
  const Setting mpuSettings[] = {
      {0x6b, 0x01, 0x7f},  // PLL clock, awake.
      {0x6c, 0x00, 0x3f},  // All six axes enabled.
      {0x1a, 0x04, 0x3f},  // Gyro DLPF ~20 Hz; MPU6050 accel DLPF ~21 Hz.
      {0x1b, 0x08, 0x1b},  // +/-500 deg/s; DLPF path on newer MPU parts too.
      {0x1c, 0x08, 0x18},  // +/-4 g.
      {0x19, 0x04, 0xff},  // 200 Hz internal output; application reads at 10-100 Hz.
      {0x1d, 0x04, 0x0f},  // Newer MPU accel DLPF ~20 Hz; never write on MPU6050.
  };
  const Setting lsmSettings[] = {
      {0x12, 0x44, 0x47},  // BDU, auto-increment, little endian, reset finished.
      {0x10, static_cast<uint8_t>(who_ == 0x69 ? 0x4b : 0x4a), 0xff},
      // 104 Hz, +/-4 g. Original DS3: 50 Hz anti-alias BW; DSL: LPF1 26 Hz;
      // DSO: LPF2 enabled, with ODR/4 (26 Hz) selected in CTRL8_XL.
      {0x11, 0x44, 0xff},  // 104 Hz, +/-500 deg/s.
      {0x13, static_cast<uint8_t>(who_ == 0x69 ? 0x80 : (who_ == 0x6c ? 0x02 : 0x00)), 0xff},
      {0x15, static_cast<uint8_t>(who_ == 0x6c ? 0x06 : 0x00), 0xff},  // DSO gyro LPF1 19 Hz.
      {0x17, 0x00, 0xff},  // No high-pass/gravity removal. DSO LPF2 cutoff ODR/4.
  };
  const size_t count = mpu_ ? (who_ == 0x68 ? 6U : 7U) : 6U;
  const Setting& setting = mpu_ ? mpuSettings[configStep_] : lsmSettings[configStep_];
  if (!verifyConfig_) {
    if (writeRegister(setting.reg, setting.value)) {
      verifyConfig_ = true;  // Read back on the next loop, not in this transaction.
      return;
    }
    failed("IMU disabled after 5 consecutive I2C configuration errors");
    nextActionMs_ = millis() + (stage_ == Stage::Retry ? kRetryMs : 10U);
    return;
  }
  uint8_t value = 0;
  if (!readRegisters(setting.reg, &value, 1) ||
      (value & setting.mask) != (setting.value & setting.mask)) {
    failed("IMU disabled after 5 consecutive configuration readback errors");
    nextActionMs_ = millis() + (stage_ == Stage::Retry ? kRetryMs : 10U);
    return;
  }
  failures_ = 0;
  verifyConfig_ = false;
  if (++configStep_ == count) {
    stage_ = Stage::Settle;
    nextActionMs_ = millis() + 100U;  // Gyro and filter settling, with UART still serviced.
  }
}

void Imu::printLevel(const ImuSample& sensor, const ImuSample& vehicle) {
  size_t sensorAxis = 0;
  size_t vehicleAxis = 0;
  float normSquared = 0;
  for (size_t i = 0; i < 3; ++i) {
    if (std::fabs(sensor.accel[i]) > std::fabs(sensor.accel[sensorAxis])) sensorAxis = i;
    if (std::fabs(vehicle.accel[i]) > std::fabs(vehicle.accel[vehicleAxis])) vehicleAxis = i;
    normSquared += vehicle.accel[i] * vehicle.accel[i];
  }
  Serial.printf("IMU level check: gravity on sensor %c%c, vehicle %c%c; |a|=%.2f m/s^2 (level expects vehicle +Z)\n",
                sensor.accel[sensorAxis] < 0 ? '-' : '+', 'X' + static_cast<int>(sensorAxis),
                vehicle.accel[vehicleAxis] < 0 ? '-' : '+', 'X' + static_cast<int>(vehicleAxis),
                static_cast<double>(std::sqrt(normSquared)));
  levelPrinted_ = true;
}

void Imu::sample() {
  const uint32_t nowUs = micros();
  if (static_cast<int32_t>(nowUs - nextSampleUs_) < 0) return;
  nextSampleUs_ += kPeriodUs;
  if (static_cast<int32_t>(nowUs - nextSampleUs_) >= 0) nextSampleUs_ = nowUs + kPeriodUs;
  uint8_t burst[14] = {};
  const uint32_t sampleMs = millis();  // Local uint32 clock at acquisition, never GNSS UTC.
  if (!readRegisters(mpu_ ? 0x3b : 0x22, burst, mpu_ ? 14U : 12U)) {
    failed("IMU disabled after 5 consecutive I2C sample errors");
    return;
  }
  failures_ = 0;
  const ImuSample sensor = mpu_ ? decodeMpu(burst, sampleMs) : decodeLsm(burst, sampleMs);
  ImuSample vehicle = axes_.apply(sensor);
  if (!levelPrinted_) printLevel(sensor, vehicle);
  if (burstSaturated(burst, mpu_)) {
    // Do not allow clipped +/-4 g data to pass as an in-range acceleration.
    if (calibrating_) calibration_.plausible = false;
    pending_ = false;
    filterValid_ = false;
    return;
  }
  if (calibrating_) {
    calibration_.add(vehicle);
    return;
  }
  // Reject outliers before either bias correction or the low-pass filter can
  // hide them. A second check covers a corrected rate that exceeds the limit.
  if (!inImuRange(vehicle)) {
    pending_ = false;
    filterValid_ = false;
    return;
  }
  for (size_t i = 0; i < 3; ++i) vehicle.gyro[i] -= bias_[i];
  if (!inImuRange(vehicle)) {
    pending_ = false;
    filterValid_ = false;
    return;
  }
  // Supplement the different hardware filters with a time-based one-pole LPF.
  // At 50 Hz the cutoff is 20 Hz; lower publish rates reduce it to 0.4*rate.
  const float cutoff = imuRateHz(OPENRX_IMU_RATE_HZ) < 50
                           ? 0.4f * imuRateHz(OPENRX_IMU_RATE_HZ) : 20.0f;
  if (filterValid_ && static_cast<uint32_t>(nowUs - lastFilterUs_) <= 1000000U) {
    const float dt = static_cast<uint32_t>(nowUs - lastFilterUs_) / 1000000.0f;
    const float alpha = 1.0f - std::exp(-6.2831853f * cutoff * dt);
    for (size_t i = 0; i < 3; ++i) {
      vehicle.gyro[i] = filtered_.gyro[i] + alpha * (vehicle.gyro[i] - filtered_.gyro[i]);
      vehicle.accel[i] = filtered_.accel[i] + alpha * (vehicle.accel[i] - filtered_.accel[i]);
    }
  }
  lastFilterUs_ = nowUs;
  filtered_ = vehicle;
  filterValid_ = true;
  latest_ = vehicle;
  pending_ = true;  // A single latest sample; never build an IMU backlog.
}

void Imu::finishCalibration() {
  calibrating_ = false;
  if (calibration_.finish(bias_)) {
    biasWho_ = who_;
    biasAddress_ = address_;
    biasMpu_ = mpu_;
    Serial.println("IMU boot calibration: still for 2 s; gyro bias frozen until reboot");
  } else {
    Serial.println("IMU boot calibration skipped: motion/variance or gravity check failed; zero gyro bias");
  }
}

void Imu::service() {
  if (stage_ == Stage::Disabled) return;
  if (stage_ == Stage::Running) {
    if (calibrating_ && static_cast<uint32_t>(millis() - calibrationStartMs_) >= kCalibrationMs) {
      finishCalibration();  // Close the window even if samples are saturated.
    }
    sample();
    return;
  }
  const uint32_t now = millis();
  if (static_cast<int32_t>(now - nextActionMs_) < 0) return;
  switch (stage_) {
    case Stage::Probe:
      probe();  // At most one address per loop; NACK during discovery is normal.
      break;
    case Stage::Reset:
      if (!writeRegister(mpu_ ? 0x6b : 0x12, mpu_ ? 0x80 : 0x01)) {
        failed("IMU disabled after 5 consecutive I2C reset errors");
        nextActionMs_ = now + (stage_ == Stage::Retry ? kRetryMs : 10U);
        break;
      }
      failures_ = 0;
      stage_ = Stage::WaitReset;
      nextActionMs_ = now + 100U;
      resetDeadlineMs_ = now + 500U;
      break;
    case Stage::WaitReset: {
      uint8_t value = 0;
      if (!readRegisters(mpu_ ? 0x6b : 0x12, &value, 1)) {
        failed("IMU disabled after 5 consecutive I2C reset-read errors");
        nextActionMs_ = now + (stage_ == Stage::Retry ? kRetryMs : 10U);
      } else if ((value & (mpu_ ? 0x80 : 0x01)) != 0) {
        failures_ = 0;
        if (static_cast<int32_t>(now - resetDeadlineMs_) >= 0) retry("IMU reset timed out");
        else nextActionMs_ = now + 10U;
      } else {
        failures_ = 0;
        configStep_ = 0;
        verifyConfig_ = false;
        stage_ = Stage::Configure;
      }
      break;
    }
    case Stage::Configure:
      configure();  // One write or readback per loop, never a calibration wait.
      break;
    case Stage::Settle:
      stage_ = Stage::Running;
      calibrating_ = bootWindow_;
      bootWindow_ = false;
      calibration_ = BootBias{};
      calibrationStartMs_ = now;
      nextSampleUs_ = micros();
      levelPrinted_ = false;
      Serial.printf("IMU detected: %s (WHO=0x%02X, I2C=0x%02X); map=%s; output=%d Hz; %s\n",
                    mpu_ ? "MPU6050/6500/925x" : "LSM6DS3/DSL/DSO",
                    static_cast<unsigned>(who_), static_cast<unsigned>(address_),
                    OPENRX_IMU_AXIS_MAP, imuRateHz(OPENRX_IMU_RATE_HZ),
                    calibrating_ ? "boot stillness check for 2 s" : "no runtime bias calibration");
      break;
    case Stage::Retry:
      // Reinitialize the local bus before probing again, including begin failures.
      Wire.end();
      if (!Wire.begin(OPENRX_IMU_SDA_PIN, OPENRX_IMU_SCL_PIN, 400000)) {
        retry("IMU I2C restart failed");
        break;
      }
      Wire.setTimeOut(2);
      probe_ = 0;
      failures_ = 0;
      stage_ = Stage::Probe;
      break;
    case Stage::Disabled:
    case Stage::Running:
      break;
  }
}

#else

void Imu::begin() { Serial.println("IMU disabled by OPENRX_ENABLE_IMU=0"); }
void Imu::service() {}
bool Imu::present() const { return false; }
bool Imu::takeSample(ImuSample&) { return false; }

#endif

}  // namespace openrx
