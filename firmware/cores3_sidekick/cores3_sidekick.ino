/*
 * cores3_sidekick.ino — Sidekick I/O board (v1)  ·  M5Stack CoreS3
 * ------------------------------------------------------------------
 * v1 SCOPE (per DEV_SPEC_cores3_mg90s.md):
 *   - The CoreS3 is the I/O board only. SERVOS stay on the Arduino R4 (unchanged).
 *   - This version uses ONLY the CoreS3's built-in hardware → the only cable is USB-C.
 *   - Channels handled here:  SCREEN (M5.Display) + SOUND (M5.Speaker chirps) + TOUCH (on-screen buttons).
 *   - ANTENNA (RGB pixel) is NOT wired yet → left as a no-op stub to fill in later.
 *
 * LINK: USB serial @ 115200, line-based, '\n'-terminated.
 *   Laptop → CoreS3 (events):   EVT ARMED | EVT WATCH <text> | EVT PROMPT <text> |
 *                               EVT TRANSCRIPT <text> | EVT SAY <text> | EVT FOUND |
 *                               EVT LOOK | EVT BEEP | EVT BEEPLO | EVT KEEP |
 *                               EVT MODE <EAGER|CALM> | EVT STEP <REST|WATCH|STOP|CONFUSED> | EVT CLEAR
 *   CoreS3 → Laptop (inputs):   IN PTT_DOWN | IN PTT_UP | IN BODYTAP (head-tap = "that's wrong") |
 *                               IN TAP STOP | IN TAP EAGER | IN TAP CALM | IN HELLO ...
 *
 * Board: M5CoreS3   ·   Lib: M5Unified   ·   "USB CDC On Boot: Enabled"
 */

#include <M5Unified.h>

// ---------------- layout ----------------
static const int SCREEN_W = 320, SCREEN_H = 240;
static const int BTN_Y = 168, BTN_H = 64;          // bottom button row
struct Btn { const char* label; int x, w; uint16_t col; };
// 3 buttons: PTT (hold to talk), EAGER/CALM toggle, STOP.
// (NOT is gone — "that's wrong" is now the head-tap / TTP223 → confused + re-search.)
Btn BTN_PTT   = {"PTT",  4,   150, 0x3186};   // dark blue-ish (hold to talk)
Btn BTN_MODE  = {"EAGER",160, 96,  0x4208};   // grey (label toggles CALM/EAGER)
Btn BTN_STOP  = {"STOP", 260, 56,  0x4000};   // red

String statusLine = "REST";
bool   eager = false;          // EAGER/CALM toggle state
int    pressedBtn = -1;        // 0=PTT 1=MODE 2=STOP ; -1 none

// ---------------- ANTENNA: Grove Chainable RGB LED v2.0 (P9813) ----------------
// Set USE_ANTENNA 1 AFTER: (a) install Library Manager "Grove Chainable RGB LED" (ChainableLED),
// (b) plug the LED into Grove Port B. Until then it stays a no-op so the sketch still compiles.
#define USE_ANTENNA 1
// mode: 0=solid 1=breathe 2=pulse(fast) 3=flutter/blink
#if USE_ANTENNA
  #include <ChainableLED.h>
  static const int LED_CLK = 8, LED_DATA = 9;        // Grove Port B: CLK=G8, DATA=G9
  ChainableLED leds(LED_CLK, LED_DATA, 1);           // 1 LED (not chained); ctor sets up the pins
  int aR = 255, aG = 150, aB = 60, aMode = 1;        // current target (warm breathe at boot)
  void antennaInit() { }                             // this library inits in the constructor (no init())
  void setAntenna(int r, int g, int b, int mode) { aR = r; aG = g; aB = b; aMode = mode; }
  void antennaTick() {
    uint32_t m = millis(); float s;
    if      (aMode == 0) s = 0.7f;
    else if (aMode == 1) s = 0.25f + 0.55f * (0.5f + 0.5f * sinf(m / 950.0f));   // breathe
    else if (aMode == 2) s = 0.40f + 0.60f * (0.5f + 0.5f * sinf(m / 170.0f));   // pulse (fast)
    else                 s = ((m / 80) % 2) ? 1.0f : 0.25f;                       // flutter/blink
    leds.setColorRGB(0, (uint8_t)(aR * s), (uint8_t)(aG * s), (uint8_t)(aB * s));
  }
#else
  void antennaInit() {}
  void setAntenna(int, int, int, int) {}
  void antennaTick() {}
#endif
// named states (used in handleLine)
inline void antRest()  { setAntenna(255, 150, 60, 1); }   // warm breathe
inline void antDuty()  { setAntenna(60, 150, 230, 1); }   // cool breathe
inline void antFound() { setAntenna(255, 255, 255, 3); }  // bright flutter
inline void antLook()  { setAntenna(255, 255, 255, 2); }  // bright pulse
inline void antKeep()  { setAntenna(255, 150, 60, 2); }   // warm pulse
inline void antAmber() { setAntenna(240, 140, 20, 1); }   // amber (unsure)
inline void antAmberBlink() { setAntenna(240, 140, 20, 3); }

// ---------------- BODY TAP sensor (TTP223 capacitive: SIG/VCC/GND) ----------------
// Wire SIG->TAP_PIN, VCC->3V3 (NOT 5V — ESP32 GPIO isn't 5V-tolerant), GND->GND.
// Set USE_TAP 1 after wiring.  A touch sends "IN BODYTAP" (= the in-the-moment "not that").
#define USE_TAP   1         // set 1 to enable a body-tap -> "IN BODYTAP"
#define TAP_SRC   1         // 1 = external TTP223 on TAP_PIN ; 2 = onboard IMU "knock" (NO wiring)
static const int TAP_PIN = 17;        // (TAP_SRC 1) free GPIO, Grove Port C; avoid 8/9 (antenna)
static const float TAP_G = 1.4f;      // (TAP_SRC 2) accel-jump threshold for a tap (g)
void tapInit() {
#if USE_TAP && TAP_SRC == 1
  pinMode(TAP_PIN, INPUT_PULLDOWN);     // pulldown: a FLOATING/unconnected pin reads a steady 0
#endif                                   // (random 0/1 in the monitor = SIG not actually reaching this pin)
}
void checkTap() {
#if USE_TAP
  static uint32_t tlast = 0;
 #if TAP_SRC == 1                              // external capacitive pad (TTP223)
  static bool last = false;
  bool now = digitalRead(TAP_PIN);
  if (now && !last && millis() - tlast > 250) { Serial.println("IN BODYTAP"); tlast = millis(); }
  last = now;
 #else                                         // onboard IMU: a sudden accel jump = a knock/tap
  static float px = 0, py = 0, pz = 0;
  float ax, ay, az;
  if (M5.Imu.getAccel(&ax, &ay, &az)) {
    float d = fabsf(ax - px) + fabsf(ay - py) + fabsf(az - pz);
    px = ax; py = ay; pz = az;
    if (d > TAP_G && millis() - tlast > 400) { Serial.println("IN BODYTAP"); tlast = millis(); }
  }
 #endif
#endif
}

// ---------------- SOUND: chirps via the built-in speaker ----------------
void chirp(const String& kind) {
  if (kind == "RISING")      { M5.Speaker.tone(700, 90); delay(90); M5.Speaker.tone(1200, 110); }
  else if (kind == "SINGLE") { M5.Speaker.tone(1500, 120); }                 // FOUND: one calm note
  else if (kind == "CALL") {                                                  // LOOK!: insistent "come-here" call (~2x longer)
    for (int i = 0; i < 4; i++) { M5.Speaker.tone(950, 90); delay(95); M5.Speaker.tone(1500, 90); delay(150); }
  }
  else if (kind == "DESCENDING") { M5.Speaker.tone(1200, 90); delay(90); M5.Speaker.tone(700, 110); }
  else if (kind == "CONFUSED") {                                              // head-tap: a puzzled "huh?" wobble
    M5.Speaker.tone(1100, 90); delay(95); M5.Speaker.tone(700, 90); delay(95); M5.Speaker.tone(950, 120);
  }
  else if (kind == "TICK")   { M5.Speaker.tone(2200, 25); }                   // mic-open tick
}

// ---------------- SCREEN ----------------
void drawButton(const Btn& b, const char* label, bool down) {
  uint16_t fill = down ? TFT_WHITE : b.col;
  uint16_t txt  = down ? TFT_BLACK : TFT_WHITE;
  M5.Display.fillRoundRect(b.x, BTN_Y, b.w, BTN_H, 8, fill);
  M5.Display.drawRoundRect(b.x, BTN_Y, b.w, BTN_H, 8, TFT_DARKGREY);
  M5.Display.setTextColor(txt);
  M5.Display.setTextDatum(middle_center);
  M5.Display.setTextSize(2);
  M5.Display.drawString(label, b.x + b.w / 2, BTN_Y + BTN_H / 2);
}

// word-wrap the status text into multiple centered lines (smaller font)
void drawStatusWrapped(const String& s) {
  M5.Display.setTextSize(2);                 // smaller so long lines fit
  M5.Display.setTextColor(TFT_WHITE);
  M5.Display.setTextDatum(middle_center);
  const int maxW  = SCREEN_W - 16;
  const int lineH = 24;
  String lines[8]; int n = 0; String cur = "";
  int i = 0;
  while (i <= (int)s.length() && n < 8) {
    int sp = s.indexOf(' ', i);
    String word = (sp < 0) ? s.substring(i) : s.substring(i, sp);
    String trial = (cur.length() == 0) ? word : cur + " " + word;
    if (M5.Display.textWidth(trial) <= maxW) cur = trial;
    else { lines[n++] = cur; cur = word; }
    if (sp < 0) break; else i = sp + 1;
  }
  if (n < 8 && cur.length()) lines[n++] = cur;
  int totalH = n * lineH;
  int y0 = (BTN_Y - totalH) / 2 + lineH / 2;
  for (int k = 0; k < n; k++) M5.Display.drawString(lines[k], SCREEN_W / 2, y0 + k * lineH);
}

void drawUI() {
  M5.Display.fillScreen(TFT_BLACK);
  drawStatusWrapped(statusLine);
  // buttons
  drawButton(BTN_PTT,  "PTT",  pressedBtn == 0);
  drawButton(BTN_MODE, eager ? "EAGER" : "CALM", pressedBtn == 1);
  drawButton(BTN_STOP, "STOP", pressedBtn == 2);
}

void setStatus(const String& s) { statusLine = s; drawUI(); }

// ---------------- protocol: handle one line from the laptop ----------------
void handleLine(String line) {
  line.trim();
  if (line.length() == 0) return;

  // split "EVT <CMD> <rest...>"
  if (!line.startsWith("EVT")) return;
  String rest = line.substring(3); rest.trim();
  int sp = rest.indexOf(' ');
  String cmd  = (sp < 0) ? rest : rest.substring(0, sp);
  String arg  = (sp < 0) ? ""   : rest.substring(sp + 1);
  cmd.toUpperCase();

  if      (cmd == "ARMED")      { setStatus("On duty"); chirp("RISING"); antDuty(); }
  else if (cmd == "WATCH")      { setStatus("Looking for: " + arg); antDuty(); }
  else if (cmd == "PROMPT")     { setStatus(arg); antDuty(); }   // the brief, shown as-is (stays during survey)
  else if (cmd == "TRANSCRIPT") { setStatus(arg); antDuty(); }   // live STT text
  else if (cmd == "SAY")        { setStatus(arg); }
  else if (cmd == "FOUND")      { setStatus("Found!"); chirp("SINGLE"); antFound(); }   // "I noticed it"
  else if (cmd == "LOOK")       { setStatus("Look!");  antLook(); }       // screen+antenna only; beats come via BEEP
  else if (cmd == "BEEP")       { M5.Speaker.tone(1400, 110); }           // eager rhythm beat (laptop drives the cadence)
  else if (cmd == "BEEPLO")     { M5.Speaker.tone(900, 130); }            // calm rhythm beat (lower, softer)
  else if (cmd == "VOL")        { M5.Speaker.setVolume(constrain(arg.toInt(), 0, 255)); }  // 0 = silent run
  else if (cmd == "KEEP")       { setStatus("kept"); antKeep(); }
  else if (cmd == "MODE") {                                                // CALM/EAGER tempo, shown during the dance
    String s = arg; s.toUpperCase();
    if (s == "EAGER") { eager = true;  setStatus("eager"); antLook(); }   // bright fast pulse
    else              { eager = false; setStatus("calm");  antDuty(); }   // cool gentle breathe
  }
  else if (cmd == "CLEAR")      { setStatus(""); }
  else if (cmd == "STEP") {
    String s = arg; s.toUpperCase();
    if      (s == "REST")     { setStatus("resting");  antRest(); }
    else if (s == "WATCH")    { setStatus("watching"); antDuty(); }
    else if (s == "CONFUSED") { setStatus("huh?"); chirp("CONFUSED"); antAmberBlink(); }
    else if (s == "STOP")     { setStatus("off duty"); chirp("DESCENDING"); antRest(); }
    else                        setStatus(s);
  }
}

// ---------------- touch → send IN events ----------------
bool inBtn(const Btn& b, int x, int y) {
  return x >= b.x && x <= b.x + b.w && y >= BTN_Y && y <= BTN_Y + BTN_H;
}

void checkTouch() {
  auto t = M5.Touch.getDetail();
  if (t.wasPressed()) {
    if      (inBtn(BTN_PTT,  t.x, t.y)) { pressedBtn = 0; chirp("TICK"); Serial.println("IN PTT_DOWN"); }
    else if (inBtn(BTN_MODE, t.x, t.y)) { pressedBtn = 1; eager = !eager; Serial.println(eager ? "IN TAP EAGER" : "IN TAP CALM"); }
    else if (inBtn(BTN_STOP, t.x, t.y)) { pressedBtn = 2; Serial.println("IN TAP STOP"); }
    if (pressedBtn >= 0) drawUI();
  }
  if (t.wasReleased()) {
    if (pressedBtn == 0) Serial.println("IN PTT_UP");   // PTT is hold-to-talk
    pressedBtn = -1;
    drawUI();
  }
}

// ---------------- setup / loop ----------------
void setup() {
  auto cfg = M5.config();
  M5.begin(cfg);
  Serial.begin(115200);
  M5.Speaker.setVolume(100);          // 0..255 — raise for louder chirps
  M5.Display.setBrightness(120);
  antennaInit();
  tapInit();
  antRest();
  setStatus("REST");
  Serial.println("IN HELLO cores3_sidekick v1");
}

void loop() {
  M5.update();          // refresh touch / buttons / imu
  antennaTick();        // animate the antenna (no-op until USE_ANTENNA 1)
  checkTouch();
  checkTap();           // body touch sensor → IN BODYTAP (no-op until USE_TAP 1)

  // read a line from the laptop (non-blocking accumulate)
  static String buf;
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n') { handleLine(buf); buf = ""; }
    else if (c != '\r') buf += c;
  }
  delay(5);
}
