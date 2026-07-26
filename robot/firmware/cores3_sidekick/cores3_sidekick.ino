/*
 * cores3_sidekick.ino — NoticeBot participant I/O board  ·  M5Stack CoreS3
 * -----------------------------------------------------------------------
 * v2 (2026-07-26). The board owns three participant-facing channels and nothing
 * else: SCREEN, SOUND, and the LED antenna. Servos are on the FE-URT2, driven
 * from the laptop.
 *
 * WHAT WAS REMOVED, and why it mattered. v1 carried a second, older UI: a
 * three-button layout with an EAGER/CALM personality toggle, plus a dozen legacy
 * EVTs (ARMED / WATCH / PROMPT / TRANSCRIPT / SAY / FOUND / LOOK / KEEP / MODE /
 * STEP / BEEP / CLEAR) that each set the status text or the antenna colour AS A
 * SIDE EFFECT. Once the state machine owned the screen and the colour, those side
 * effects were not dead code -- they were actively fighting the new channels:
 *
 *   - `EVT STEP WATCH` called antDuty(), which painted S6's RED negation blue.
 *   - `EVT MODE` called setStatus("eager"), which repainted the old three-button
 *     screen -- EAGER button and all -- over the participant UI.
 *
 * Both were patched with guards (`hueOwned`, `if (!uiMode)`) before being deleted.
 * The guards are gone too: there is now exactly one owner per channel, so there
 * is nothing to arbitrate.
 *
 * LINK: USB serial @ 115200, line-based, '\n'-terminated.
 *
 *   Laptop -> CoreS3
 *     EVT UI <screen>      one of: idle recording waiting heard planning
 *                          tracking notthat noticed error   (states.py SCREENS)
 *     EVT NOTICED <n>      session cumulative count, shown on `noticed`.
 *                          The LAPTOP owns this number: it owns the feed, so one
 *                          source of truth, and it survives a reboot here.
 *     EVT LED <0-255>      antenna level, streamed from the clip's `led` column
 *                          for accents synced to the motion. Falls back to the
 *                          local breath after 500 ms of silence, so a stalled
 *                          link is not a frozen LED.
 *     EVT HUE <WARM|COOL|RED|GREEN|ALARM>   colour = which KIND of state
 *     EVT SFX <name>       curious ack shutter puzzled excited lost | NONE
 *     EVT LEVEL <0-100>    mic level for the recording bar
 *     EVT VOL <0-255>      speaker volume; 0 for a silent run
 *     EVT REST             end of a run: go quiet, back to idle + warm breath
 *     EVT PING             -> `IN PONG cores3_sidekick v2`
 *
 *   CoreS3 -> Laptop
 *     IN PTT_DOWN / IN PTT_UP     the green button, held
 *     IN OK                       the green button on `noticed`
 *     IN STOP                     the red button, on every screen
 *     IN BODYTAP                  touch sensor = "not that one"
 *     IN PONG ...                 identity, on request
 *
 * Board: M5CoreS3  ·  Lib: M5Unified  ·  "USB CDC On Boot: Enabled"
 */

#include <M5Unified.h>

// EVERY struct used in a function signature belongs up here, above the LAST
// #include in the file (which is ChainableLED.h, a few lines down -- not this
// one). The .ino preprocessor generates a prototype for every function in the
// sketch and injects them all immediately after that last #include; a type
// defined further down does not exist yet at that point, so the prototype is
// what fails, not the definition. Hence "error: 'X' does not name a type"
// pointing at a line that looks perfectly fine.
//
// Note hit this first; UiBtn hit it next, for the same reason. If you add a
// struct and pass it to anything, put it here.
struct Note  { uint16_t hz; uint16_t ms; };
struct UiBtn { const char* id; int x, y, w, h; uint16_t col; const char* label; };

void sfxStart(const Note* seq, int n);
void sfxTick();
void sfxByName(const String& n);
void uiSet(const String& name);
void uiDraw();
void uiTick();
void uiTouch();
bool uiHit(const UiBtn& b, int x, int y);

static const int SCREEN_W = 320, SCREEN_H = 240;

// ---------------- ANTENNA: Grove Chainable RGB LED v2.0 (P9813), Port B -------
#define USE_ANTENNA 1
#if USE_ANTENNA
  #include <ChainableLED.h>
  static const int LED_CLK = 8, LED_DATA = 9;
  ChainableLED leds(LED_CLK, LED_DATA, 1);
  int aR = 255, aG = 150, aB = 60;              // warm at boot

  // The clip streams the LEVEL; the colour comes from the state. Local breathing
  // is only a FALLBACK -- if no level has arrived for A_EXT_TIMEOUT the board
  // breathes on its own, because a stalled link should leave the robot looking
  // alive rather than frozen mid-flash. A frozen LED reads to a participant as
  // "it broke"; a breath reads as "still here".
  int aExt = -1;
  uint32_t aExtAt = 0;
  const uint32_t A_EXT_TIMEOUT = 500;

  void antennaInit() {}
  void setAntennaHue(int r, int g, int b) { aR = r; aG = g; aB = b; }
  void setAntennaLevel(int v) { aExt = constrain(v, 0, 255); aExtAt = millis(); }
  void antennaTick() {
    uint32_t m = millis();
    float s = (aExt >= 0 && (m - aExtAt) < A_EXT_TIMEOUT)
              ? aExt / 255.0f                                    // clip-driven
              : 0.25f + 0.55f * (0.5f + 0.5f * sinf(m / 950.0f)); // fallback breath
    leds.setColorRGB(0, (uint8_t)(aR * s), (uint8_t)(aG * s), (uint8_t)(aB * s));
  }
#else
  void antennaInit() {}
  void setAntennaHue(int, int, int) {}
  void setAntennaLevel(int) {}
  void antennaTick() {}
#endif

// ---------------- BODY TAP (TTP223 capacitive: SIG/VCC/GND) -------------------
// SIG->TAP_PIN, VCC->3V3 (NOT 5V, ESP32 GPIO is not 5V-tolerant), GND->GND.
#define USE_TAP   1
#define TAP_SRC   1         // 1 = external TTP223 ; 2 = onboard IMU knock
static const int TAP_PIN = 17;     // Grove Port C; avoid 8/9 (antenna)
static const float TAP_G = 1.4f;   // (TAP_SRC 2) accel jump for a tap, g

void tapInit() {
#if USE_TAP && TAP_SRC == 1
  // pulldown: an unconnected pin then reads a steady 0. Random 0/1 in the
  // monitor means SIG is not actually reaching this pin.
  pinMode(TAP_PIN, INPUT_PULLDOWN);
#endif
}

void checkTap() {
#if USE_TAP
  static uint32_t tlast = 0;
 #if TAP_SRC == 1
  static bool last = false;
  bool now = digitalRead(TAP_PIN);
  if (now && !last && millis() - tlast > 250) {
    Serial.println("IN BODYTAP"); tlast = millis();
  }
  last = now;
 #else
  static float px = 0, py = 0, pz = 0;
  float ax, ay, az;
  if (M5.Imu.getAccel(&ax, &ay, &az)) {
    float d = fabsf(ax - px) + fabsf(ay - py) + fabsf(az - pz);
    px = ax; py = ay; pz = az;
    if (d > TAP_G && millis() - tlast > 400) {
      Serial.println("IN BODYTAP"); tlast = millis();
    }
  }
 #endif
#endif
}

// ---------------- SFX: per-state sounds, non-blocking ------------------------
// Scheduled rather than played with delay(), because loop() also drives the LED:
// a blocking chirp at a state change freezes the light at exactly the moment both
// channels are meant to be saying the same thing.
//
// The vocabulary mirrors the motion rather than decorating it:
//   curious  rises   a question, matching the turn toward you (S2, unused now)
//   ack      falls   affirmation, same downward accent as the nod (S3)
//   shutter  click   one capture, fired off the LED flash so they cannot drift (S4)
//   puzzled  ends up an unresolved question (S6)
//   excited  rises   calling you over from across the desk (S7a)
//   lost     wanders odd intervals, drifting down, unresolved (S8)
const int SFX_MAX = 10;
Note sfxSeq[SFX_MAX];
int sfxLen = 0, sfxIdx = 0;
uint32_t sfxNextAt = 0;

void sfxStart(const Note* seq, int n) {
  sfxLen = (n < SFX_MAX) ? n : SFX_MAX;
  for (int i = 0; i < sfxLen; i++) sfxSeq[i] = seq[i];
  sfxIdx = 0;
  sfxNextAt = millis();
}

void sfxTick() {
  if (sfxIdx >= sfxLen) return;
  uint32_t m = millis();
  if (m < sfxNextAt) return;
  Note n = sfxSeq[sfxIdx++];
  if (n.hz) M5.Speaker.tone(n.hz, n.ms);
  sfxNextAt = m + n.ms;
}

// two notes close together read as ONE blip that rises -- tone() cannot glide
const Note SFX_CURIOUS[] = {{1000, 35}, {1400, 55}};
const Note SFX_ACK[]     = {{1400, 70}, {1000, 110}};
const Note SFX_SHUTTER[] = {{2600, 22}};
const Note SFX_PUZZLED[] = {{1000, 70}, {760, 70}, {1180, 130}};
const Note SFX_EXCITED[] = {{1000, 55}, {1350, 55}, {1800, 85}, {0, 55}, {1800, 120}};
// deliberately non-diatonic and irregular: "weird" comes from intervals that do
// not resolve, not from being loud
const Note SFX_LOST[]    = {{820, 150}, {605, 110}, {700, 170}, {515, 210},
                            {0, 110}, {560, 95}};

void sfxByName(const String& n) {
  String k = n; k.toUpperCase();
  if      (k == "CURIOUS") sfxStart(SFX_CURIOUS, 2);
  else if (k == "ACK")     sfxStart(SFX_ACK, 2);
  else if (k == "SHUTTER") sfxStart(SFX_SHUTTER, 1);
  else if (k == "PUZZLED") sfxStart(SFX_PUZZLED, 3);
  else if (k == "EXCITED") sfxStart(SFX_EXCITED, 5);
  else if (k == "LOST")    sfxStart(SFX_LOST, 6);
  else if (k == "NONE")    { sfxLen = sfxIdx = 0; }
}

// ---------------- PARTICIPANT UI: nine screens, at most two buttons ----------
// Two rules the layout ENFORCES rather than documents:
//   * GREEN IS ALWAYS LEFT AND AFFIRMATIVE, RED ALWAYS RIGHT AND STOP. A
//     participant should never have to read a button to know what it does, and
//     moving them between screens would destroy that.
//   * TOUCH ONLY HITS BUTTONS THAT ARE ACTUALLY DRAWN. A STOP-only screen has no
//     phantom PTT, so a stray thumb cannot start a recording nobody sees.
//
// Nine screens for eight states: `waiting` belongs to S2 after the button is
// released, when the robot's behaviour has not changed but what it is telling you
// has.
String uiScreen = "idle";
int    noticedN = 0;
int    recLevel = 0;
uint32_t recDrawnAt = 0;

// UiBtn itself is defined at the top of the file -- see the note there.
const int UI_MAX_BTN = 2;
UiBtn uiBtns[UI_MAX_BTN];
int uiNBtn = 0;
int uiDown = -1;

static const uint16_t COL_GREEN = 0x2604;
static const uint16_t COL_RED   = 0xB000;

void uiLayout() {
  uiNBtn = 0;
  if (uiScreen == "idle") {
    // the one screen a participant reads while deciding to act, so both are big
    uiBtns[uiNBtn++] = {"PTT",  8, 132, 150, 100, COL_GREEN, "PTT"};
    uiBtns[uiNBtn++] = {"STOP", 166, 132, 146, 100, COL_RED, "STOP"};
  } else if (uiScreen == "recording") {
    // nothing: the finger is already on the button that matters
  } else if (uiScreen == "noticed") {
    uiBtns[uiNBtn++] = {"OK",   8, 168, 150, 64, COL_GREEN, "OK"};
    uiBtns[uiNBtn++] = {"STOP", 166, 168, 146, 64, COL_RED, "STOP"};
  } else {
    uiBtns[uiNBtn++] = {"STOP", 88, 168, 144, 64, COL_RED, "STOP"};
  }
}

void uiDrawBtn(int i) {
  const UiBtn& b = uiBtns[i];
  bool down = (uiDown == i);
  M5.Display.fillRoundRect(b.x, b.y, b.w, b.h, 10, down ? TFT_WHITE : b.col);
  M5.Display.drawRoundRect(b.x, b.y, b.w, b.h, 10, TFT_DARKGREY);
  M5.Display.setTextColor(down ? TFT_BLACK : TFT_WHITE);
  M5.Display.setTextDatum(middle_center);
  M5.Display.setTextSize(3);
  M5.Display.drawString(b.label, b.x + b.w / 2, b.y + b.h / 2);
}

void uiDrawBar() {
  const int x = 30, y = 100, w = SCREEN_W - 60, h = 34;
  M5.Display.drawRoundRect(x, y, w, h, 6, TFT_DARKGREY);
  int fill = (w - 6) * constrain(recLevel, 0, 100) / 100;
  M5.Display.fillRect(x + 3, y + 3, fill, h - 6, COL_GREEN);
  M5.Display.fillRect(x + 3 + fill, y + 3, (w - 6) - fill, h - 6, TFT_BLACK);
}

const char* uiText() {
  if (uiScreen == "idle")      return "idle";
  if (uiScreen == "waiting")   return "waiting...";
  if (uiScreen == "heard")     return "I heard you.";
  if (uiScreen == "planning")  return "planning...";
  if (uiScreen == "tracking")  return "tracking...";
  if (uiScreen == "notthat")   return "not that!?";
  if (uiScreen == "error")     return "error";
  return "";
}

void uiDraw() {
  M5.Display.fillScreen(TFT_BLACK);
  uiLayout();
  if (uiScreen == "recording") {
    uiDrawBar();
  } else if (uiScreen == "noticed") {
    M5.Display.setTextColor(TFT_WHITE);
    M5.Display.setTextDatum(middle_center);
    M5.Display.setTextSize(6);
    M5.Display.drawString(String(noticedN), SCREEN_W / 2, 70);
    M5.Display.setTextSize(3);
    M5.Display.drawString("noticed", SCREEN_W / 2, 122);
  } else {
    M5.Display.setTextColor(TFT_WHITE);
    M5.Display.setTextDatum(middle_center);
    M5.Display.setTextSize(3);
    M5.Display.drawString(uiText(), SCREEN_W / 2, 80);
  }
  for (int i = 0; i < uiNBtn; i++) uiDrawBtn(i);
}

void uiSet(const String& name) {
  if (name != uiScreen) { uiScreen = name; uiDown = -1; uiDraw(); }
}

void uiTick() {
  // only the bar repaints between events, and only its own rectangle: a full
  // redraw at bar rate would stall loop(), which also drives the LED
  if (uiScreen == "recording" && millis() - recDrawnAt > 80) {
    recDrawnAt = millis();
    uiDrawBar();
  }
}

bool uiHit(const UiBtn& b, int x, int y) {
  return x >= b.x && x <= b.x + b.w && y >= b.y && y <= b.y + b.h;
}

// PTT is the one control that is HELD, so its release cannot be owned by the
// screen. It used to be: press PTT on `idle` -> the laptop answers with
// `EVT UI recording` -> uiSet() clears uiDown and uiLayout() leaves the recording
// screen with ZERO buttons -> the finger lifts, and `wasReleased() && uiDown >= 0`
// is false, so IN PTT_UP was never sent. The laptop then sat in S2_LISTEN with
// nothing to time out (the STT deadline is armed BY ptt_up), the recorder was
// never stopped, and the session hung on the bar forever.
//
// pttHeld is a latch that outlives the redraw: whoever sent PTT_DOWN owes a
// PTT_UP, no matter what is on screen when the finger lifts.
bool pttHeld = false;

void uiTouch() {
  auto t = M5.Touch.getDetail();
  if (t.wasPressed()) {
    for (int i = 0; i < uiNBtn; i++) {
      if (uiHit(uiBtns[i], t.x, t.y)) {
        uiDown = i; uiDrawBtn(i);
        String id = uiBtns[i].id;
        if      (id == "PTT")  { pttHeld = true; Serial.println("IN PTT_DOWN"); }
        else if (id == "OK")   { Serial.println("IN OK");   M5.Speaker.tone(1600, 60); }
        else if (id == "STOP") { pttHeld = false; Serial.println("IN STOP"); M5.Speaker.tone(700, 90); }
        break;
      }
    }
    return;
  }

  // Release. Two independent triggers, because one hang is one too many:
  // the release EVENT, and -- if that is missed while the screen is being
  // repainted -- simply no longer having a finger down.
  bool up = t.wasReleased() || (pttHeld && M5.Touch.getCount() == 0);
  if (!up) return;

  if (uiDown >= 0) {
    String id = uiBtns[uiDown].id;
    int was = uiDown; uiDown = -1;
    if (was < uiNBtn) uiDrawBtn(was);
  }
  if (pttHeld) { pttHeld = false; Serial.println("IN PTT_UP"); }
}

// ---------------- protocol -----------------------------------------------------
void handleLine(String line) {
  line.trim();
  if (!line.startsWith("EVT")) return;
  String rest = line.substring(3); rest.trim();
  int sp = rest.indexOf(' ');
  String cmd = (sp < 0) ? rest : rest.substring(0, sp);
  String arg = (sp < 0) ? ""   : rest.substring(sp + 1);
  cmd.toUpperCase();

  if      (cmd == "UI")      uiSet(arg);
  else if (cmd == "NOTICED") { noticedN = arg.toInt();
                               if (uiScreen == "noticed") uiDraw(); }
  else if (cmd == "LEVEL")   recLevel = arg.toInt();
  else if (cmd == "LED")     setAntennaLevel(arg.toInt());
  else if (cmd == "SFX")     sfxByName(arg);
  else if (cmd == "VOL")     M5.Speaker.setVolume(constrain(arg.toInt(), 0, 255));
  else if (cmd == "HUE") {
    String s = arg; s.toUpperCase();
    if      (s == "WARM")  setAntennaHue(255, 150,  60);   // present, idle
    else if (s == "COOL")  setAntennaHue( 60, 150, 230);   // attending
    else if (s == "RED")   setAntennaHue(255,  40,  30);   // negation (S6)
    // Blue near zero on purpose: at 90 it read as teal beside COOL, because a
    // diffused P9813's blue die is strong and any blue left in "green" pulls it
    // toward the colour it has to contrast with.
    else if (s == "GREEN") setAntennaHue(  0, 255,  40);   // a result (S7)
    else if (s == "ALARM") setAntennaHue(240, 140,  20);   // stuck (S8)
  }
  else if (cmd == "REST") {
    // End of a run. Without this, a test that finishes on S8 leaves the amber
    // alarm flashing indefinitely: the streamed level outlives the laptop going
    // quiet.
    sfxLen = sfxIdx = 0;
    aExt = -1;
    setAntennaHue(255, 150, 60);
    uiSet("idle");
  }
  else if (cmd == "PING") Serial.println("IN PONG cores3_sidekick v2");
}

// ---------------- setup / loop -------------------------------------------------
void setup() {
  auto cfg = M5.config();
  M5.begin(cfg);
  Serial.begin(115200);
  M5.Speaker.setVolume(100);
  M5.Display.setBrightness(120);
  antennaInit();
  tapInit();
  uiDraw();                 // the idle screen, immediately -- no legacy layout
  Serial.println("IN HELLO cores3_sidekick v2");
}

void loop() {
  M5.update();
  antennaTick();
  uiTouch();
  uiTick();
  sfxTick();
  checkTap();

  static String buf;
  while (Serial.available()) {
    char c = (char)Serial.read();
    if (c == '\n') { handleLine(buf); buf = ""; }
    else if (c != '\r') buf += c;
  }
  delay(5);
}
