#pragma once

#include "config.h"
#include "imu_math.h"

namespace openrx {

class Imu {
 public:
  void begin();
  void service();
  bool present() const;
  bool takeSample(ImuSample& sample);

 private:
#if OPENRX_ENABLE_IMU
  enum class Stage { Disabled, Probe, Reset, WaitReset, Configure, Settle, Running, Retry };
  static constexpr uint32_t kRetryMs = 30000;
  static constexpr uint8_t kMaxFailures = 5;
  static constexpr uint32_t kCalibrationMs = 2000;
  static constexpr uint32_t kPeriodUs = 1000000U / imuRateHz(OPENRX_IMU_RATE_HZ);

  Stage stage_ = Stage::Disabled;
  AxisMap axes_;
  uint8_t address_ = 0;
  uint8_t who_ = 0;
  uint8_t probe_ = 0;
  uint8_t configStep_ = 0;
  uint8_t failures_ = 0;
  uint8_t biasWho_ = 0;
  uint8_t biasAddress_ = 0;
  bool biasMpu_ = false;
  bool mpu_ = false;
  bool verifyConfig_ = false;
  bool bootWindow_ = true;
  bool calibrating_ = false;
  bool levelPrinted_ = false;
  bool pending_ = false;
  bool filterValid_ = false;
  uint32_t nextActionMs_ = 0;
  uint32_t resetDeadlineMs_ = 0;
  uint32_t nextSampleUs_ = 0;
  uint32_t calibrationStartMs_ = 0;
  uint32_t lastFilterUs_ = 0;
  BootBias calibration_;
  float bias_[3] = {};
  ImuSample filtered_;
  ImuSample latest_;

  bool readRegisters(uint8_t reg, uint8_t* bytes, size_t length);
  bool writeRegister(uint8_t reg, uint8_t value);
  void failed(const char* message);
  void retry(const char* message);
  void probe();
  void configure();
  void sample();
  void finishCalibration();
  void printLevel(const ImuSample& sensor, const ImuSample& vehicle);
#endif
};

}  // namespace openrx
