// Writes the device serial number and the per-flight token to EEPROM.
//
// Re-flash before EVERY flight: the ground station generates a new random mask when a
// flight is created and only accepts packets whose token = (SN XOR mask) & 0xFFFFFF.
//   1. Create the flight in the web UI and copy its mask.
//   2. Set NEW_SN and KEY_MASK below, upload, check the serial monitor (57600 baud).
//   3. Upload firmware/sonde again.

#include <EEPROM.h>

// ── EEPROM Layout ───────────────────────────────────────────────────────────
// 0–3   : magic (0x55AA1234)
// 4–7   : uint32_t DEVICE_SN
// 8–11  : uint32_t DEVICE_TOKEN
constexpr int   ADDR_MAGIC        = 0;
constexpr int   ADDR_DEVICE_SN    = 4;
constexpr int   ADDR_DEVICE_TOKEN = 8;
constexpr uint32_t MAGIC_VALUE    = 0x55AA1234UL;

// ←– EDIT THESE VALUES ––>
const uint32_t NEW_SN    = 0x11951;    // device serial number (as registered in the web UI)
const uint32_t KEY_MASK  = 0x5B5A5B;   // flight mask shown on the flight page

void setup() {
  Serial.begin(57600);
  while (!Serial);

  // calculate token
  uint32_t newToken = (uint32_t)((NEW_SN ^ KEY_MASK) & 0xFFFFFF);

  Serial.println(F("Writing new device info to EEPROM..."));
  EEPROM.put(ADDR_DEVICE_SN,     NEW_SN);
  EEPROM.put(ADDR_DEVICE_TOKEN,  newToken);
  EEPROM.put(ADDR_MAGIC,         MAGIC_VALUE);

  // verify back
  uint32_t checkMagic = 0;
  uint32_t checkSN    = 0;
  uint32_t checkToken = 0;
  EEPROM.get(ADDR_MAGIC,         checkMagic);
  EEPROM.get(ADDR_DEVICE_SN,     checkSN);
  EEPROM.get(ADDR_DEVICE_TOKEN,  checkToken);

  Serial.print(F("Magic: 0x"));   Serial.println(checkMagic,   HEX);
  Serial.print(F("SN:    0x"));   Serial.println(checkSN,     HEX);
  Serial.print(F("Token: 0x"));   Serial.println(checkToken,  HEX);

  Serial.println(F("Done."));
}

void loop() {
  // nothing here
}
