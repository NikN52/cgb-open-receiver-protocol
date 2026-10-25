#include <Arduino.h>
#include <WiFi.h>
#include <WiFiUdp.h>
#include <ESPmDNS.h>
#include <driver/uart.h>
#include <esp_system.h>
#include <lwip/sockets.h>

#include "config.h"

namespace {

constexpr uint16_t kTcpPort = OPENRX_TCP_PORT;
constexpr uint16_t kDiscoveryPort = OPENRX_DISCOVERY_PORT;
constexpr size_t kMaxLine = 512;  // Excludes CRLF.
constexpr size_t kMaxEpochBytes = 4096;
constexpr size_t kMaxEpochLines = 24;
constexpr size_t kEpochQueueDepth = 4;
constexpr size_t kMaxClients = 2;
constexpr size_t kMaxTcpInputBytesPerLoop = 256;
constexpr uint32_t kHeartbeatIntervalMs = 1000;
constexpr uint32_t kFixFreshMs = 1500;
constexpr uint32_t kEpochMaxAgeMs = 1500;
constexpr uint32_t kEpochFlushDelayMs = 300;
constexpr uint32_t kWifiRetryMs = 5000;
constexpr uart_port_t kGnssUart = UART_NUM_1;

static_assert(OPENRX_GNSS_BAUD == 9600 || OPENRX_GNSS_BAUD == 38400 ||
                  OPENRX_GNSS_BAUD == 115200,
              "OPENRX_GNSS_BAUD must be 9600, 38400, or 115200");
static_assert(OPENRX_GNSS_RX_PIN >= 0, "OPENRX_GNSS_RX_PIN must name an input pin");

WiFiServer tcpServer(kTcpPort, kMaxClients);
WiFiUDP discoveryUdp;

char bootId[9] = {};
bool servicesRunning = false;
IPAddress activeLocalIp;
IPAddress activeSubnetMask;
uint32_t nextWifiAttemptMs = 0;
bool configurationValid = true;

uint8_t nmeaXor(const char* data, size_t length) {
  uint8_t value = 0;
  for (size_t i = 0; i < length; ++i) {
    value ^= static_cast<uint8_t>(data[i]);
  }
  return value;
}

char hexDigit(uint8_t nibble) {
  return nibble < 10 ? static_cast<char>('0' + nibble)
                     : static_cast<char>('A' + nibble - 10);
}

int hexValue(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'A' && c <= 'F') return c - 'A' + 10;
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  return -1;
}

bool isPrintableAscii(const char* text, size_t length) {
  for (size_t i = 0; i < length; ++i) {
    const uint8_t c = static_cast<uint8_t>(text[i]);
    if (c < 0x20 || c > 0x7e) return false;
  }
  return true;
}

bool validNmeaLine(const char* line, size_t length) {
  if (length < 7 || length > kMaxLine || line[0] != '$' ||
      !isPrintableAscii(line, length)) {
    return false;
  }
  const char* star = static_cast<const char*>(memchr(line, '*', length));
  if (star == nullptr || static_cast<size_t>(star - line) + 3U != length) {
    return false;
  }
  const int high = hexValue(star[1]);
  const int low = hexValue(star[2]);
  if (high < 0 || low < 0) return false;
  return nmeaXor(line + 1, static_cast<size_t>(star - line - 1)) ==
         static_cast<uint8_t>((high << 4) | low);
}

size_t makeSentence(char* output, size_t capacity, const char* body) {
  const size_t bodyLength = strlen(body);
  if (bodyLength + 6U > capacity) return 0;
  output[0] = '$';
  memcpy(output + 1, body, bodyLength);
  output[bodyLength + 1] = '*';
  const uint8_t checksum = nmeaXor(body, bodyLength);
  output[bodyLength + 2] = hexDigit(checksum >> 4);
  output[bodyLength + 3] = hexDigit(checksum & 0x0f);
  output[bodyLength + 4] = '\r';
  output[bodyLength + 5] = '\n';
  return bodyLength + 6U;
}

bool sameLocalSubnet(const IPAddress& peer) {
  const auto isLocalOnly = [](const IPAddress& address) {
    return address[0] == 10 ||
           (address[0] == 172 && address[1] >= 16 && address[1] <= 31) ||
           (address[0] == 192 && address[1] == 168) ||
           (address[0] == 169 && address[1] == 254);
  };
  if (!servicesRunning || peer == IPAddress(0, 0, 0, 0) ||
      peer == IPAddress(255, 255, 255, 255) ||
      activeSubnetMask == IPAddress(0, 0, 0, 0) || !isLocalOnly(activeLocalIp) ||
      !isLocalOnly(peer)) {
    return false;
  }
  for (size_t i = 0; i < 4; ++i) {
    if ((peer[i] & activeSubnetMask[i]) != (activeLocalIp[i] & activeSubnetMask[i])) {
      return false;
    }
  }
  return true;
}

struct Epoch {
  char bytes[kMaxEpochBytes];
  uint16_t length = 0;
  bool fix = false;
  uint32_t capturedMs = 0;
};

Epoch epochQueue[kEpochQueueDepth];
size_t epochHead = 0;
size_t epochCount = 0;
uint32_t lastFixEpochMs = 0;
bool lastEpochHadFix = false;

void pushEpoch(const Epoch& epoch) {
  if (epochCount == kEpochQueueDepth) {
    epochHead = (epochHead + 1U) % kEpochQueueDepth;
    --epochCount;
  }
  const size_t tail = (epochHead + epochCount) % kEpochQueueDepth;
  epochQueue[tail] = epoch;
  ++epochCount;
}

bool popEpoch(Epoch& epoch) {
  if (epochCount == 0) return false;
  epoch = epochQueue[epochHead];
  epochHead = (epochHead + 1U) % kEpochQueueDepth;
  --epochCount;
  return true;
}

void clearEpochQueue() {
  epochHead = 0;
  epochCount = 0;
}

bool fieldAt(const char* line, size_t length, size_t wanted, const char*& value,
             size_t& valueLength) {
  size_t field = 0;
  size_t start = 0;
  for (size_t i = 0; i <= length; ++i) {
    if (i == length || line[i] == ',' || line[i] == '*') {
      if (field == wanted) {
        value = line + start;
        valueLength = i - start;
        return true;
      }
      if (i < length && line[i] == '*') return false;
      ++field;
      start = i + 1U;
    }
  }
  return false;
}

enum class SentenceKind { Other, Gga, Gst, Rmc };

SentenceKind sentenceKind(const char* line, size_t length) {
  const char* comma = static_cast<const char*>(memchr(line, ',', length));
  if (comma == nullptr) return SentenceKind::Other;
  const size_t idLength = static_cast<size_t>(comma - line);
  if (idLength < 4) return SentenceKind::Other;
  const char* suffix = comma - 3;
  if (memcmp(suffix, "GGA", 3) == 0) return SentenceKind::Gga;
  if (memcmp(suffix, "GST", 3) == 0) return SentenceKind::Gst;
  if (memcmp(suffix, "RMC", 3) == 0) return SentenceKind::Rmc;
  return SentenceKind::Other;
}

bool parseUtcMillis(const char* line, size_t length, uint32_t& utcMs) {
  const char* value = nullptr;
  size_t valueLength = 0;
  if (!fieldAt(line, length, 1, value, valueLength) || valueLength < 6) return false;
  for (size_t i = 0; i < 6; ++i) {
    if (value[i] < '0' || value[i] > '9') return false;
  }
  const uint32_t hh = static_cast<uint32_t>((value[0] - '0') * 10 + value[1] - '0');
  const uint32_t mm = static_cast<uint32_t>((value[2] - '0') * 10 + value[3] - '0');
  const uint32_t ss = static_cast<uint32_t>((value[4] - '0') * 10 + value[5] - '0');
  if (hh > 23 || mm > 59 || ss > 60) return false;
  uint32_t fraction = 0;
  if (valueLength > 6) {
    if (value[6] != '.' || valueLength == 7) return false;
    uint32_t scale = 100;
    for (size_t i = 7; i < valueLength && scale > 0; ++i) {
      if (value[i] < '0' || value[i] > '9') return false;
      fraction += static_cast<uint32_t>(value[i] - '0') * scale;
      scale /= 10;
    }
    for (size_t i = 10; i < valueLength; ++i) {
      if (value[i] < '0' || value[i] > '9') return false;
    }
  }
  utcMs = ((hh * 60U + mm) * 60U + ss) * 1000U + fraction;
  return true;
}

bool positiveIntegerField(const char* line, size_t length, size_t index) {
  const char* value = nullptr;
  size_t valueLength = 0;
  if (!fieldAt(line, length, index, value, valueLength) || valueLength == 0) return false;
  bool nonzero = false;
  for (size_t i = 0; i < valueLength; ++i) {
    if (value[i] < '0' || value[i] > '9') return false;
    nonzero = nonzero || value[i] != '0';
  }
  return nonzero;
}

bool rmcIsValid(const char* line, size_t length) {
  const char* value = nullptr;
  size_t valueLength = 0;
  return fieldAt(line, length, 2, value, valueLength) && valueLength == 1 && value[0] == 'A';
}

bool leapYear(int year) {
  return year % 4 == 0 && (year % 100 != 0 || year % 400 == 0);
}

int64_t daysFromCivil(int year, unsigned month, unsigned day) {
  year -= month <= 2;
  const int era = (year >= 0 ? year : year - 399) / 400;
  const unsigned yearOfEra = static_cast<unsigned>(year - era * 400);
  const unsigned adjustedMonth = month > 2 ? month - 3U : month + 9U;
  const unsigned dayOfYear = (153U * adjustedMonth + 2U) / 5U + day - 1U;
  const unsigned dayOfEra = yearOfEra * 365U + yearOfEra / 4U - yearOfEra / 100U + dayOfYear;
  return static_cast<int64_t>(era) * 146097 + static_cast<int64_t>(dayOfEra) - 719468;
}

bool rmcEpochKey(const char* line, size_t length, uint32_t utcMs, uint64_t& key) {
  const char* value = nullptr;
  size_t valueLength = 0;
  if (!fieldAt(line, length, 9, value, valueLength) || valueLength != 6) return false;
  for (size_t i = 0; i < valueLength; ++i) {
    if (value[i] < '0' || value[i] > '9') return false;
  }
  const unsigned day = static_cast<unsigned>((value[0] - '0') * 10 + value[1] - '0');
  const unsigned month = static_cast<unsigned>((value[2] - '0') * 10 + value[3] - '0');
  const unsigned shortYear = static_cast<unsigned>((value[4] - '0') * 10 + value[5] - '0');
  const int year = shortYear >= 80 ? 1900 + static_cast<int>(shortYear)
                                   : 2000 + static_cast<int>(shortYear);
  static const uint8_t monthDays[] = {31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31};
  if (month < 1 || month > 12) return false;
  const unsigned maximumDay = monthDays[month - 1U] + (month == 2 && leapYear(year) ? 1U : 0U);
  if (day < 1 || day > maximumDay) return false;
  const int64_t days = daysFromCivil(year, month, day);
  if (days < 0) return false;
  key = static_cast<uint64_t>(days) * 86400000ULL + utcMs;
  return true;
}

bool isStandardGnssNmea(const char* line, size_t length) {
  const char* comma = static_cast<const char*>(memchr(line, ',', length));
  if (comma == nullptr || comma - line != 6 || line[0] != '$' || line[1] == 'P') return false;
  for (size_t i = 1; i < 6; ++i) {
    const char c = line[i];
    if (c < 'A' || c > 'Z') return false;
  }
  return true;
}

struct StoredLine {
  char bytes[kMaxLine + 2] = {};
  uint16_t length = 0;

  void clear() { length = 0; }

  void set(const char* line, size_t lineLength) {
    memcpy(bytes, line, lineLength);
    bytes[lineLength] = '\r';
    bytes[lineLength + 1U] = '\n';
    length = static_cast<uint16_t>(lineLength + 2U);
  }
};

struct EpochAssembler {
  StoredLine gga;
  StoredLine gst;
  StoredLine rmc;
  char metadata[kMaxEpochBytes] = {};
  uint16_t metadataLength = 0;
  uint8_t metadataLines = 0;
  uint32_t utcMs = 0;
  uint32_t lastInputMs = 0;
  bool active = false;
  bool invalid = false;
  bool haveLastEmittedEpoch = false;
  uint64_t lastEmittedEpoch = 0;

  size_t lineCount() const {
    return metadataLines + (gga.length != 0 ? 1U : 0U) + (gst.length != 0 ? 1U : 0U) +
           (rmc.length != 0 ? 1U : 0U);
  }

  size_t byteCount() const {
    return metadataLength + gga.length + gst.length + rmc.length;
  }

  void reset(bool keepMetadata = false) {
    gga.clear();
    gst.clear();
    rmc.clear();
    if (!keepMetadata) {
      metadataLength = 0;
      metadataLines = 0;
    }
    utcMs = 0;
    lastInputMs = millis();
    active = false;
    invalid = false;
  }

  bool canStore(size_t oldLength, size_t newLength, bool addsLine) const {
    const size_t prospectiveBytes = byteCount() - oldLength + newLength + 2U;
    const size_t prospectiveLines = lineCount() + (addsLine ? 1U : 0U);
    return prospectiveBytes <= kMaxEpochBytes && prospectiveLines <= kMaxEpochLines;
  }

  void storeTimed(StoredLine& destination, const char* line, size_t length) {
    if (!canStore(destination.length, length, destination.length == 0)) {
      invalid = true;
      return;
    }
    destination.set(line, length);
  }

  void storeMetadata(const char* line, size_t length) {
    if (!canStore(0, length, true)) {
      if (active) {
        invalid = true;
        return;
      }
      // Before an epoch starts, retain a latest bounded snapshot rather than
      // carrying an unbounded history into the next epoch.
      reset();
    }
    if (!canStore(0, length, true)) return;
    memcpy(metadata + metadataLength, line, length);
    metadataLength += static_cast<uint16_t>(length);
    metadata[metadataLength++] = '\r';
    metadata[metadataLength++] = '\n';
    ++metadataLines;
    lastInputMs = millis();
  }

  void appendStored(Epoch& output, const StoredLine& line) const {
    if (line.length == 0) return;
    memcpy(output.bytes + output.length, line.bytes, line.length);
    output.length += line.length;
  }

  void finish() {
    if (!active) return;
    if (!invalid && gga.length != 0 && rmc.length != 0) {
      uint64_t measurementEpoch = 0;
      if (!rmcEpochKey(rmc.bytes, rmc.length - 2U, utcMs, measurementEpoch) ||
          (haveLastEmittedEpoch && measurementEpoch <= lastEmittedEpoch)) {
        reset();
        return;
      }
      Epoch output;
      appendStored(output, gga);
      appendStored(output, gst);
      appendStored(output, rmc);
      if (metadataLength != 0) {
        memcpy(output.bytes + output.length, metadata, metadataLength);
        output.length += metadataLength;
      }
      output.fix = positiveIntegerField(gga.bytes, gga.length - 2U, 6) &&
                   rmcIsValid(rmc.bytes, rmc.length - 2U);
      output.capturedMs = millis();
      lastEpochHadFix = output.fix;
      lastFixEpochMs = millis();
      pushEpoch(output);
      lastEmittedEpoch = measurementEpoch;
      haveLastEmittedEpoch = true;
    }
    reset();
  }

  void accept(const char* line, size_t length) {
    if (!isStandardGnssNmea(line, length)) return;
    const SentenceKind kind = sentenceKind(line, length);
    if (kind == SentenceKind::Other) {
      storeMetadata(line, length);
      return;
    }
    uint32_t sentenceUtcMs = 0;
    if (!parseUtcMillis(line, length, sentenceUtcMs)) return;
    if (active && sentenceUtcMs != utcMs) finish();
    if (!active) {
      active = true;
      utcMs = sentenceUtcMs;
    }
    lastInputMs = millis();
    if (kind == SentenceKind::Gga) storeTimed(gga, line, length);
    else if (kind == SentenceKind::Gst) storeTimed(gst, line, length);
    else storeTimed(rmc, line, length);
  }

  void service(uint32_t now) {
    if (active && gga.length != 0 && rmc.length != 0 &&
        static_cast<uint32_t>(now - lastInputMs) >= kEpochFlushDelayMs) {
      finish();
    }
  }
} epochAssembler;

class UartScanner {
 public:
  void feed(uint8_t byte) {
    switch (state_) {
      case State::Idle:
        if (byte == '$') startNmea();
        else if (byte == 0xb5) state_ = State::UbxSync2;
        break;
      case State::UbxSync2:
        if (byte == 0x62) {
          ubxHeaderRead_ = 0;
          state_ = State::UbxHeader;
        } else if (byte == '$') {
          startNmea();
        } else {
          state_ = (byte == 0xb5) ? State::UbxSync2 : State::Idle;
        }
        break;
      case State::UbxHeader:
        ubxHeader_[ubxHeaderRead_++] = byte;
        if (ubxHeaderRead_ == sizeof(ubxHeader_)) {
          const uint32_t payloadLength = static_cast<uint32_t>(ubxHeader_[2]) |
                                         (static_cast<uint32_t>(ubxHeader_[3]) << 8U);
          ubxRemaining_ = payloadLength + 2U;  // Payload plus CK_A and CK_B.
          state_ = ubxRemaining_ == 0 ? State::Idle : State::UbxBody;
        }
        break;
      case State::UbxBody:
        if (--ubxRemaining_ == 0) state_ = State::Idle;
        break;
      case State::Nmea:
        feedNmea(byte);
        break;
      case State::DropLine:
        if (byte == '$') startNmea();
        else if (byte == 0xb5) state_ = State::UbxSync2;
        else if (byte == '\n') state_ = State::Idle;
        break;
    }
  }

 private:
  enum class State { Idle, UbxSync2, UbxHeader, UbxBody, Nmea, DropLine };
  State state_ = State::Idle;
  char line_[kMaxLine + 1] = {};
  size_t lineLength_ = 0;
  bool sawCr_ = false;
  uint8_t ubxHeader_[4] = {};
  size_t ubxHeaderRead_ = 0;
  uint32_t ubxRemaining_ = 0;

  void startNmea() {
    state_ = State::Nmea;
    lineLength_ = 1;
    line_[0] = '$';
    sawCr_ = false;
  }

  void feedNmea(uint8_t byte) {
    if (sawCr_) {
      if (byte == '\n' && validNmeaLine(line_, lineLength_)) {
        epochAssembler.accept(line_, lineLength_);
      }
      state_ = State::Idle;
      sawCr_ = false;
      if (byte == '$') startNmea();
      return;
    }
    if (byte == '\r') {
      sawCr_ = true;
      return;
    }
    if (byte == '$') {
      startNmea();
      return;
    }
    if (byte == 0xb5) {
      state_ = State::UbxSync2;
      return;
    }
    if (byte < 0x20 || byte > 0x7e || lineLength_ >= kMaxLine) {
      state_ = State::DropLine;
      return;
    }
    line_[lineLength_++] = static_cast<char>(byte);
  }
} uartScanner;

struct ClientSlot {
  WiFiClient socket;
  char input[kMaxLine + 1] = {};
  size_t inputLength = 0;
  bool sawCr = false;
  bool dropping = false;
  bool accepted = false;
  uint32_t heartbeatSeq = 0;
  uint32_t lastHeartbeatMs = 0;
};

ClientSlot clients[kMaxClients];

void closeClient(ClientSlot& client) {
  client.socket.stop();
  client = ClientSlot{};
}

bool sendAtomic(ClientSlot& client, const uint8_t* data, size_t length) {
  if (!client.socket.connected() || length == 0 || client.socket.fd() < 0) {
    closeClient(client);
    return false;
  }
  // WiFiClient inherits Print::availableForWrite(), which always returns zero
  // in the pinned Arduino core. Submit exactly once to the public socket fd;
  // a partial send or EAGAIN closes the client instead of building a backlog.
  const ssize_t written = lwip_send(client.socket.fd(), data, length, MSG_DONTWAIT);
  if (written != static_cast<ssize_t>(length)) {
    closeClient(client);
    return false;
  }
  return true;
}

bool isUnreserved(uint8_t c) {
  return (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
         (c >= '0' && c <= '9') || c == '-' || c == '.' || c == '_' || c == '~';
}

bool validUtf8(const uint8_t* bytes, size_t length) {
  size_t i = 0;
  while (i < length) {
    const uint8_t first = bytes[i++];
    if (first < 0x80) {
      if (first < 0x20 || first == 0x7f) return false;
      continue;
    }
    size_t continuation = 0;
    uint8_t secondMinimum = 0x80;
    uint8_t secondMaximum = 0xbf;
    if (first >= 0xc2 && first <= 0xdf) {
      continuation = 1;
    } else if (first >= 0xe0 && first <= 0xef) {
      continuation = 2;
      if (first == 0xe0) secondMinimum = 0xa0;
      if (first == 0xed) secondMaximum = 0x9f;
    } else if (first >= 0xf0 && first <= 0xf4) {
      continuation = 3;
      if (first == 0xf0) secondMinimum = 0x90;
      if (first == 0xf4) secondMaximum = 0x8f;
    } else {
      return false;
    }
    if (i + continuation > length || bytes[i] < secondMinimum || bytes[i] > secondMaximum) {
      return false;
    }
    ++i;
    for (size_t remaining = 1; remaining < continuation; ++remaining, ++i) {
      if (bytes[i] < 0x80 || bytes[i] > 0xbf) return false;
    }
  }
  return true;
}

size_t decodeValue(const char* value, size_t length, uint8_t* output) {
  size_t decodedLength = 0;
  for (size_t i = 0; i < length;) {
    if (value[i] == '%') {
      output[decodedLength++] = static_cast<uint8_t>(
          (hexValue(value[i + 1U]) << 4) | hexValue(value[i + 2U]));
      i += 3U;
    } else {
      output[decodedLength++] = static_cast<uint8_t>(value[i++]);
    }
  }
  return decodedLength;
}

bool decodedEqual(const char* first, size_t firstLength,
                  const char* second, size_t secondLength) {
  uint8_t firstDecoded[kMaxLine] = {};
  uint8_t secondDecoded[kMaxLine] = {};
  const size_t firstDecodedLength = decodeValue(first, firstLength, firstDecoded);
  const size_t secondDecodedLength = decodeValue(second, secondLength, secondDecoded);
  return firstDecodedLength == secondDecodedLength &&
         memcmp(firstDecoded, secondDecoded, firstDecodedLength) == 0;
}

bool validEncodedValue(const char* value, size_t length, bool allowRawPlus) {
  if (length == 0 || length > kMaxLine) return false;
  if (allowRawPlus && (value[0] == '+' || value[length - 1U] == '+')) return false;
  uint8_t decoded[kMaxLine] = {};
  size_t decodedLength = 0;
  for (size_t i = 0; i < length;) {
    const uint8_t c = static_cast<uint8_t>(value[i]);
    if (c == '%') {
      if (i + 2U >= length) return false;
      const int high = hexValue(value[i + 1U]);
      const int low = hexValue(value[i + 2U]);
      if (high < 0 || low < 0) return false;
      decoded[decodedLength++] = static_cast<uint8_t>((high << 4) | low);
      i += 3U;
    } else {
      if (!isUnreserved(c) && !(allowRawPlus && c == '+')) return false;
      decoded[decodedLength++] = c;
      ++i;
    }
  }
  if (!validUtf8(decoded, decodedLength)) return false;
  if (allowRawPlus) {
    const char* end = value + length;
    for (const char* item = value; item < end;) {
      const char* itemEnd = static_cast<const char*>(memchr(item, '+', static_cast<size_t>(end - item)));
      if (itemEnd == nullptr) itemEnd = end;
      if (itemEnd == item) return false;
      for (const char* prior = value; prior < item;) {
        const char* priorEnd = static_cast<const char*>(memchr(prior, '+', static_cast<size_t>(item - prior)));
        if (priorEnd == nullptr) priorEnd = item;
        if (decodedEqual(prior, static_cast<size_t>(priorEnd - prior),
                         item, static_cast<size_t>(itemEnd - item))) return false;
        prior = priorEnd + (priorEnd < item ? 1 : 0);
      }
      item = itemEnd + (itemEnd < end ? 1 : 0);
    }
  }
  return true;
}

bool validDeviceId(const char* value) {
  const size_t length = strlen(value);
  if (length < 1 || length > 32) return false;
  for (size_t i = 0; i < length; ++i) {
    if (!isUnreserved(static_cast<uint8_t>(value[i])) || value[i] == '%') return false;
  }
  return true;
}

bool validTalkers(const char* value) {
  const size_t length = strlen(value);
  if (length < 2) return false;
  for (size_t offset = 0; offset < length;) {
    if (offset + 2U > length || value[offset] < 'A' || value[offset] > 'Z' ||
        value[offset + 1U] < 'A' || value[offset + 1U] > 'Z') return false;
    for (size_t prior = 0; prior < offset; prior += 3U) {
      if (value[prior] == value[offset] && value[prior + 1U] == value[offset + 1U]) return false;
    }
    offset += 2U;
    if (offset == length) return true;
    if (value[offset] != '+') return false;
    ++offset;
  }
  return false;
}

bool validMdnsHost(const char* value) {
  const size_t length = strlen(value);
  if (length < 1 || length > 63 || value[0] == '-' || value[length - 1U] == '-') return false;
  for (size_t i = 0; i < length; ++i) {
    const char c = value[i];
    if (!((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z') ||
          (c >= '0' && c <= '9') || c == '-')) return false;
  }
  return true;
}

bool validControlKey(const char* key, size_t length) {
  if (length == 0 || key[0] < 'a' || key[0] > 'z') return false;
  for (size_t i = 1; i < length; ++i) {
    const char c = key[i];
    if (!((c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || c == '_')) return false;
  }
  return true;
}

bool duplicateEarlierKey(const char* fieldsStart, const char* current,
                         const char* key, size_t keyLength) {
  const char* cursor = fieldsStart;
  while (cursor < current) {
    const char* end = static_cast<const char*>(memchr(cursor, ',', static_cast<size_t>(current - cursor)));
    if (end == nullptr) end = current;
    const char* equals = static_cast<const char*>(memchr(cursor, '=', static_cast<size_t>(end - cursor)));
    if (equals != nullptr && static_cast<size_t>(equals - cursor) == keyLength &&
        memcmp(cursor, key, keyLength) == 0) return true;
    cursor = end + (end < current ? 1 : 0);
  }
  return false;
}

#if OPENRX_ENABLE_PROTOCOL_HELLO
bool makeHello(char* output, size_t capacity, size_t& length) {
  char body[kMaxLine] = {};
  if (!validDeviceId(OPENRX_DEVICE_ID) ||
      !validEncodedValue(OPENRX_DEVICE_NAME, strlen(OPENRX_DEVICE_NAME), false) ||
      !validEncodedValue(OPENRX_MAKER, strlen(OPENRX_MAKER), false) ||
      !validEncodedValue(OPENRX_FIRMWARE_VERSION, strlen(OPENRX_FIRMWARE_VERSION), false) ||
      !validTalkers(OPENRX_TALKERS)) {
    return false;
  }
  const int count = snprintf(body, sizeof(body),
                             "PCGBO,HELLO,pv=1.0,id=%s,boot=%s,name=%s,maker=%s,fw=%s,"
                             "caps=nmea,talkers=%s",
                             OPENRX_DEVICE_ID, bootId, OPENRX_DEVICE_NAME, OPENRX_MAKER,
                             OPENRX_FIRMWARE_VERSION, OPENRX_TALKERS);
  if (count < 0 || static_cast<size_t>(count) >= sizeof(body)) return false;
  length = makeSentence(output, capacity, body);
  return length != 0;
}

bool isKnownCapability(const char* value, size_t length) {
  static const char* const known[] = {"nmea", "ubx_rx", "rtcm_in", "imu_pcgbi",
                                      "wheel", "batt", "time", "udp_push", "cmd_pair"};
  for (const char* item : known) {
    if (decodedEqual(value, length, item, strlen(item))) return true;
  }
  return false;
}

bool validAccept(const char* line, size_t length) {
  if (!validNmeaLine(line, length)) return false;
  const char* star = static_cast<const char*>(memchr(line, '*', length));
  if (star == nullptr) return false;
  const char prefix[] = "$PCGBO,ACCEPT,";
  if (static_cast<size_t>(star - line) <= sizeof(prefix) - 1U ||
      memcmp(line, prefix, sizeof(prefix) - 1U) != 0) {
    return false;
  }

  bool havePv = false;
  bool haveUse = false;
  bool selectedNmea = false;
  const char* const fieldsStart = line + sizeof(prefix) - 1U;
  const char* cursor = fieldsStart;
  while (cursor < star) {
    const char* end = static_cast<const char*>(memchr(cursor, ',', static_cast<size_t>(star - cursor)));
    if (end == nullptr) end = star;
    const char* equals = static_cast<const char*>(memchr(cursor, '=', static_cast<size_t>(end - cursor)));
    if (equals == nullptr || equals == cursor || equals + 1 == end) return false;
    const size_t keyLength = static_cast<size_t>(equals - cursor);
    if (!validControlKey(cursor, keyLength) ||
        duplicateEarlierKey(fieldsStart, cursor, cursor, keyLength)) return false;
    const char* value = equals + 1;
    const size_t valueLength = static_cast<size_t>(end - value);
    if (!validEncodedValue(value, valueLength, true)) return false;
    if (keyLength == 2 && memcmp(cursor, "pv", 2) == 0) {
      if (havePv || valueLength != 3 || memcmp(value, "1.0", 3) != 0) return false;
      havePv = true;
    } else if (keyLength == 3 && memcmp(cursor, "use", 3) == 0) {
      if (haveUse || value[valueLength - 1U] == '+') return false;
      haveUse = true;
      const char* cap = value;
      while (cap < end) {
        const char* capEnd = static_cast<const char*>(memchr(cap, '+', static_cast<size_t>(end - cap)));
        if (capEnd == nullptr) capEnd = end;
        const size_t capLength = static_cast<size_t>(capEnd - cap);
        if (capLength == 0) return false;
        for (size_t i = 0; i < capLength;) {
          const uint8_t c = static_cast<uint8_t>(cap[i]);
          if (c == '%') {
            if (i + 2U >= capLength || hexValue(cap[i + 1U]) < 0 ||
                hexValue(cap[i + 2U]) < 0) return false;
            i += 3U;
          } else {
            if (!isUnreserved(c)) return false;
            ++i;
          }
        }
        const char* prior = value;
        while (prior < cap) {
          const char* priorEnd = static_cast<const char*>(
              memchr(prior, '+', static_cast<size_t>(cap - prior)));
          if (priorEnd == nullptr) priorEnd = cap;
          if (static_cast<size_t>(priorEnd - prior) == capLength &&
              memcmp(prior, cap, capLength) == 0) return false;
          prior = priorEnd + (priorEnd < cap ? 1 : 0);
        }
        if (decodedEqual(cap, capLength, "nmea", 4)) {
          selectedNmea = true;
        } else if (isKnownCapability(cap, capLength)) {
          return false;  // Known but not advertised by this firmware.
        }
        cap = capEnd + (capEnd < end ? 1 : 0);
      }
    }
    cursor = end + (end < star ? 1 : 0);
  }
  return havePv && haveUse && selectedNmea;
}
#endif

void processClientInput(ClientSlot& client) {
#if OPENRX_ENABLE_PROTOCOL_HELLO
  size_t budget = kMaxTcpInputBytesPerLoop;
  while (budget-- > 0 && client.socket.connected() && client.socket.available() > 0) {
    const int received = client.socket.read();
    if (received < 0) break;
    const uint8_t byte = static_cast<uint8_t>(received);
    if (client.dropping) {
      if (byte == '$') {
        client.dropping = false;
        client.input[0] = '$';
        client.inputLength = 1;
        client.sawCr = false;
      } else if (byte == '\n') {
        client.dropping = false;
        client.inputLength = 0;
        client.sawCr = false;
      }
      continue;
    }
    if (client.sawCr) {
      if (byte == '\n' && validAccept(client.input, client.inputLength)) {
        client.accepted = true;
        client.lastHeartbeatMs = millis();
      }
      client.inputLength = 0;
      client.sawCr = false;
      if (byte == '$') {
        client.input[0] = '$';
        client.inputLength = 1;
      }
      continue;
    }
    if (byte == '\r') {
      client.sawCr = true;
    } else if (byte == '$') {
      client.input[0] = '$';
      client.inputLength = 1;
    } else if (byte < 0x20 || byte > 0x7e || client.inputLength >= kMaxLine) {
      client.dropping = byte != '\n';
      client.inputLength = 0;
      client.sawCr = false;
    } else {
      client.input[client.inputLength++] = static_cast<char>(byte);
    }
  }
#else
  size_t budget = kMaxTcpInputBytesPerLoop;
  while (budget-- > 0 && client.socket.available() > 0) client.socket.read();
#endif
}

void acceptClients() {
  WiFiClient incoming = tcpServer.accept();
  if (!incoming) return;
  if (!sameLocalSubnet(incoming.remoteIP())) {
    incoming.stop();
    return;
  }
  for (ClientSlot& client : clients) {
    if (!client.socket.connected()) {
      closeClient(client);
      client.socket = incoming;
      client.socket.setNoDelay(true);
#if OPENRX_ENABLE_PROTOCOL_HELLO
      char hello[kMaxLine + 2] = {};
      size_t helloLength = 0;
      if (!makeHello(hello, sizeof(hello), helloLength) ||
          !sendAtomic(client, reinterpret_cast<const uint8_t*>(hello), helloLength)) {
        closeClient(client);
      }
#endif
      return;
    }
  }
  incoming.stop();
}

void serviceClients() {
  acceptClients();
#if OPENRX_ENABLE_PROTOCOL_HELLO
  const uint32_t now = millis();
#endif
  for (ClientSlot& client : clients) {
    if (!client.socket.connected()) {
      if (client.socket) closeClient(client);
      continue;
    }
    processClientInput(client);
#if OPENRX_ENABLE_PROTOCOL_HELLO
    if (client.socket.connected() && client.accepted &&
        static_cast<uint32_t>(now - client.lastHeartbeatMs) >= kHeartbeatIntervalMs) {
      const bool fixFresh = lastEpochHadFix &&
                            static_cast<uint32_t>(now - lastFixEpochMs) <= kFixFreshMs;
      char body[128] = {};
      const int count = snprintf(body, sizeof(body), "PCGBO,HB,boot=%s,seq=%lu,ms=%lu,fix=%u",
                                 bootId, static_cast<unsigned long>(client.heartbeatSeq++),
                                 static_cast<unsigned long>(now), fixFresh ? 1U : 0U);
      char sentence[160] = {};
      const size_t length = count > 0 && static_cast<size_t>(count) < sizeof(body)
                                ? makeSentence(sentence, sizeof(sentence), body)
                                : 0;
      if (length == 0 || !sendAtomic(client, reinterpret_cast<const uint8_t*>(sentence), length)) {
        continue;
      }
      client.lastHeartbeatMs = now;
    }
#endif
  }

  Epoch epoch;
  while (popEpoch(epoch)) {
    if (static_cast<uint32_t>(millis() - epoch.capturedMs) > kEpochMaxAgeMs) continue;
    for (ClientSlot& client : clients) {
      if (client.socket.connected()) {
        sendAtomic(client, reinterpret_cast<const uint8_t*>(epoch.bytes), epoch.length);
      }
    }
  }
}

bool lowerHexNonce(const char* value, size_t length) {
  if (length < 16 || length > 32) return false;
  for (size_t i = 0; i < length; ++i) {
    if (!((value[i] >= '0' && value[i] <= '9') || (value[i] >= 'a' && value[i] <= 'f'))) {
      return false;
    }
  }
  return true;
}

bool parseDiscover(const char* line, size_t length, char* nonce, size_t nonceCapacity) {
  if (!validNmeaLine(line, length)) return false;
  const char* star = static_cast<const char*>(memchr(line, '*', length));
  const char prefix[] = "$PCGBO,DISCOVER,";
  if (star == nullptr || static_cast<size_t>(star - line) <= sizeof(prefix) - 1U ||
      memcmp(line, prefix, sizeof(prefix) - 1U) != 0) return false;

  bool havePv = false;
  bool haveNonce = false;
  const char* const fieldsStart = line + sizeof(prefix) - 1U;
  const char* cursor = fieldsStart;
  while (cursor < star) {
    const char* end = static_cast<const char*>(memchr(cursor, ',', static_cast<size_t>(star - cursor)));
    if (end == nullptr) end = star;
    const char* equals = static_cast<const char*>(memchr(cursor, '=', static_cast<size_t>(end - cursor)));
    if (equals == nullptr || equals == cursor || equals + 1 == end) return false;
    const size_t keyLength = static_cast<size_t>(equals - cursor);
    if (!validControlKey(cursor, keyLength) ||
        duplicateEarlierKey(fieldsStart, cursor, cursor, keyLength)) return false;
    const char* value = equals + 1;
    const size_t valueLength = static_cast<size_t>(end - value);
    if (!validEncodedValue(value, valueLength, true)) return false;
    if (keyLength == 2 && memcmp(cursor, "pv", 2) == 0) {
      if (havePv || valueLength != 1 || value[0] != '1') return false;
      havePv = true;
    } else if (keyLength == 5 && memcmp(cursor, "nonce", 5) == 0) {
      if (haveNonce || !lowerHexNonce(value, valueLength) || valueLength + 1U > nonceCapacity) {
        return false;
      }
      memcpy(nonce, value, valueLength);
      nonce[valueLength] = '\0';
      haveNonce = true;
    }
    cursor = end + (end < star ? 1 : 0);
  }
  return havePv && haveNonce;
}

void serviceDiscovery() {
  const int packetLength = discoveryUdp.parsePacket();
  if (packetLength <= 0) return;
  const IPAddress remoteIp = discoveryUdp.remoteIP();
  const uint16_t remotePort = discoveryUdp.remotePort();
  if (!sameLocalSubnet(remoteIp) || packetLength > static_cast<int>(kMaxLine + 2U)) {
    while (discoveryUdp.available() > 0) discoveryUdp.read();
    return;
  }
  char packet[kMaxLine + 2] = {};
  const int read = discoveryUdp.read(reinterpret_cast<uint8_t*>(packet), sizeof(packet));
  if (read < 2 || packet[read - 2] != '\r' || packet[read - 1] != '\n') return;
  char nonce[33] = {};
  if (!parseDiscover(packet, static_cast<size_t>(read - 2), nonce, sizeof(nonce))) return;
  if (!validDeviceId(OPENRX_DEVICE_ID) ||
      !validEncodedValue(OPENRX_DEVICE_NAME, strlen(OPENRX_DEVICE_NAME), false)) return;

  char body[256] = {};
  const int count = snprintf(body, sizeof(body),
                             "PCGBO,OFFER,pv=1.0,nonce=%s,id=%s,tcp=%u,caps=nmea,name=%s",
                             nonce, OPENRX_DEVICE_ID, kTcpPort, OPENRX_DEVICE_NAME);
  char offer[320] = {};
  const size_t offerLength = count > 0 && static_cast<size_t>(count) < sizeof(body)
                                 ? makeSentence(offer, sizeof(offer), body)
                                 : 0;
  if (offerLength == 0 || !discoveryUdp.beginPacket(remoteIp, remotePort)) return;
  discoveryUdp.write(reinterpret_cast<const uint8_t*>(offer), offerLength);
  discoveryUdp.endPacket();
}

void stopServices() {
  for (ClientSlot& client : clients) closeClient(client);
  tcpServer.stop();
  discoveryUdp.stop();
  clearEpochQueue();
#if OPENRX_ENABLE_MDNS
  MDNS.end();
#endif
  servicesRunning = false;
  activeLocalIp = IPAddress();
  activeSubnetMask = IPAddress();
}

void startServices() {
  clearEpochQueue();
  activeLocalIp = WiFi.localIP();
  activeSubnetMask = WiFi.subnetMask();
  tcpServer.begin();
  tcpServer.setNoDelay(true);
  if (!discoveryUdp.begin(kDiscoveryPort)) {
    tcpServer.stop();
    return;
  }
#if OPENRX_ENABLE_MDNS
  if (MDNS.begin(OPENRX_MDNS_HOST)) {
    MDNS.addService("cgbgnss", "tcp", kTcpPort);
    MDNS.addServiceTxt("cgbgnss", "tcp", "pv", "1.0");
    MDNS.addServiceTxt("cgbgnss", "tcp", "id", OPENRX_DEVICE_ID);
    MDNS.addServiceTxt("cgbgnss", "tcp", "name", OPENRX_DEVICE_NAME);
  }
#endif
  servicesRunning = true;
}

void serviceWifi() {
  if (!configurationValid) return;
  const wl_status_t status = WiFi.status();
  const uint32_t now = millis();
  if (status == WL_CONNECTED) {
    if (!servicesRunning || WiFi.localIP() != activeLocalIp ||
        WiFi.subnetMask() != activeSubnetMask) {
      if (servicesRunning) stopServices();
      startServices();
    }
    return;
  }
  if (servicesRunning) stopServices();
  if (static_cast<int32_t>(now - nextWifiAttemptMs) >= 0) {
    WiFi.begin(OPENRX_WIFI_SSID, OPENRX_WIFI_PASSWORD);
    nextWifiAttemptMs = now + kWifiRetryMs;
  }
}

void serviceUart() {
  uint8_t bytes[128];
  const int count = uart_read_bytes(kGnssUart, bytes, sizeof(bytes), 0);
  for (int i = 0; i < count; ++i) uartScanner.feed(bytes[i]);
  epochAssembler.service(millis());
}

bool setupGnssRxOnly() {
  uart_config_t config = {};
  config.baud_rate = OPENRX_GNSS_BAUD;
  config.data_bits = UART_DATA_8_BITS;
  config.parity = UART_PARITY_DISABLE;
  config.stop_bits = UART_STOP_BITS_1;
  config.flow_ctrl = UART_HW_FLOWCTRL_DISABLE;
  config.rx_flow_ctrl_thresh = 0;
  config.source_clk = UART_SCLK_APB;
  if (uart_param_config(kGnssUart, &config) != ESP_OK) return false;
  // Only the RX route is configured. GPIO_NUM_NC is UART_PIN_NO_CHANGE in this
  // ESP-IDF version, so safety also relies on never mapping or wiring GNSS TX
  // outputs from the ESP32. There is no receiver-UART write call in this program.
  if (uart_set_pin(kGnssUart, GPIO_NUM_NC, static_cast<gpio_num_t>(OPENRX_GNSS_RX_PIN),
                   GPIO_NUM_NC, GPIO_NUM_NC) != ESP_OK) return false;
  return uart_driver_install(kGnssUart, 2048, 0, 0, nullptr, 0) == ESP_OK;
}

}  // namespace

void setup() {
  Serial.begin(115200);  // USB/debug UART only; never connected to the GNSS receiver.
  snprintf(bootId, sizeof(bootId), "%08lx", static_cast<unsigned long>(esp_random()));
  if (!setupGnssRxOnly()) {
    Serial.println("GNSS RX-only UART setup failed");
  }
  configurationValid = validDeviceId(OPENRX_DEVICE_ID) && validMdnsHost(OPENRX_MDNS_HOST) &&
                       validEncodedValue(OPENRX_DEVICE_NAME, strlen(OPENRX_DEVICE_NAME), false) &&
                       validEncodedValue(OPENRX_MAKER, strlen(OPENRX_MAKER), false) &&
                       validEncodedValue(OPENRX_FIRMWARE_VERSION,
                                         strlen(OPENRX_FIRMWARE_VERSION), false) &&
                       validTalkers(OPENRX_TALKERS);
  if (!configurationValid) {
    Serial.println("Protocol configuration is invalid; Wi-Fi services disabled");
    return;
  }
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(false);
  WiFi.setHostname(OPENRX_MDNS_HOST);
  nextWifiAttemptMs = 0;
}

void loop() {
  serviceUart();
  serviceWifi();
  if (servicesRunning) {
    serviceDiscovery();
    serviceClients();
  }
  delay(1);
}

