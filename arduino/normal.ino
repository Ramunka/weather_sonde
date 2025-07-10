#include <Wire.h>
#include <SPI.h>
#include <EEPROM.h>
#include <Adafruit_Sensor.h>
#include <Adafruit_SHTC3.h>
#include <Adafruit_TMP117.h>
#include <Adafruit_LPS2X.h>
#include <TinyGPSPlus.h>
#include <RH_RF95.h>

// ── CONFIG ─────────────────────────────────────────────────────────────
#define LPS_CS_PIN      8
#define GPS_BAUD        9600
#define RF95_CS_PIN     10
#define RF95_INT_PIN    2

static const uint8_t  BATCH    = 2;   // send two measurements per packet
static const uint32_t INTERVAL = 2000;

// Shrunk buffer: 64 bytes per record × BATCH
char    batchBuf[BATCH * 90];
uint8_t batchPos = 0, batchIdx = 0;
char    utcBuf[21];

constexpr int      ADDR_MAGIC  = 0;
constexpr uint32_t MAGIC_VALUE = 0x55AA1234UL;
uint32_t DEVICE_SN, DEVICE_TOKEN;

// sensors & radios
Adafruit_SHTC3    shtc3;
Adafruit_TMP117   tmp117;
Adafruit_LPS22    lps;
TinyGPSPlus       gps;
RH_RF95           rf95(RF95_CS_PIN, RF95_INT_PIN);

// shared event structs to minimize RAM
static sensors_event_t evt;
static sensors_event_t pressureEvent;
static sensors_event_t tempEvent;

void loadDeviceInfo() {
  uint32_t magic = 0;
  EEPROM.get(ADDR_MAGIC, magic);
  if (magic != MAGIC_VALUE) while (1);
  EEPROM.get(ADDR_MAGIC + 4, DEVICE_SN);
  EEPROM.get(ADDR_MAGIC + 8, DEVICE_TOKEN);
}

void appendFloat(char*& p, float v, uint8_t prec) {
  char tmp[16];
  dtostrf(v, 0, prec, tmp);
  uint8_t len = strlen(tmp);
  memcpy(p, tmp, len);
  p += len;
  *p++ = ',';
}

void setup() {
  // GPS on hardware Serial
  Serial.begin(GPS_BAUD);

  loadDeviceInfo();
  Wire.begin();
  SPI.begin();

  pinMode(LPS_CS_PIN, OUTPUT);
  digitalWrite(LPS_CS_PIN, HIGH);

  // Sensor initialization
  if (!shtc3.begin())   while (1);
  if (!tmp117.begin())  while (1);
  if (!lps.begin_SPI(LPS_CS_PIN, &SPI)) while (1);

  // LoRa radio initialization
  if (!rf95.init())     while (1);
  rf95.setFrequency(915.0);
  rf95.setTxPower(20, false);
  rf95.sleep();
}

void loop() {
  // feed GPS data into TinyGPS++
  while (Serial.available()) {
    gps.encode(Serial.read());
  }

  static uint32_t lastMeas = 0;
  uint32_t now = millis();
  if (now - lastMeas < INTERVAL) return;
  lastMeas = now;

  // Read sensors
  shtc3.getEvent(&evt, &evt);
  float humidity    = evt.relative_humidity;
  tmp117.getEvent(&evt);
  float temperature = evt.temperature;
  lps.getEvent(&pressureEvent, &tempEvent);
  float pressure    = pressureEvent.pressure;

  // Build UTC timestamp
  if (gps.date.isValid() && gps.time.isValid()) {
    sprintf(utcBuf,
      "%04d-%02d-%02dT%02d:%02d:%02dZ",
      gps.date.year(), gps.date.month(), gps.date.day(),
      gps.time.hour(), gps.time.minute(), gps.time.second());
  } else {
    utcBuf[0] = '\0';
  }

  // Append one record at offset batchPos
  char* p = batchBuf + batchPos;
  batchPos += sprintf(p,
    "%05lX,%06lX,%s,",
     DEVICE_SN, DEVICE_TOKEN, utcBuf);
  p = batchBuf + batchPos;
  appendFloat(p, temperature, 2);
  appendFloat(p, humidity,    2);
  appendFloat(p, pressure,    2);
  appendFloat(p, gps.location.isValid() ? gps.location.lat() : NAN, 5);
  appendFloat(p, gps.location.isValid() ? gps.location.lng() : NAN, 5);
  appendFloat(p, gps.altitude.isValid() ? gps.altitude.meters() : NAN, 2);
  appendFloat(p, gps.hdop.isValid() ? gps.hdop.hdop() : NAN, 2);
  batchPos = p - batchBuf;
  batchPos += sprintf(batchBuf + batchPos,
    "%d\n", gps.satellites.isValid() ? gps.satellites.value() : 0);

  // Send when full
  if (++batchIdx >= BATCH) {
    rf95.init(); delay(10);
    rf95.setFrequency(915.0);
    rf95.setTxPower(14, false);
    rf95.send((uint8_t*)batchBuf, batchPos);
    rf95.waitPacketSent();
    rf95.sleep();
    batchIdx = 0;
    batchPos = 0;
  }
}