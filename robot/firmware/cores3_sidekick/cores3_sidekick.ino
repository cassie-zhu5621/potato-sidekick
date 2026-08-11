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
 *     EVT HUE <WARM|COOL|RED|SUMMON|SPENT>  colour = which KIND of state
 *       (GREEN/ALARM still accepted -- old names, new colours)
 *     EVT SFX <name>       curious ack shutter puzzled excited lost | NONE
 *     EVT LEVEL <0-100>    mic level for the recording bar
 *     EVT VOL <0-255>      speaker volume; 0 for a silent run
 *     EVT REST             end of a run: go quiet, back to idle + warm breath
 *     EVT PING             -> `IN PONG cores3_sidekick v5`
 *
 *   CoreS3 -> Laptop
 *     IN PTT_DOWN / IN PTT_UP     the green button, held
 *     IN OK                       the green button on `noticed` -- OR a TAP on the
 *                                 red one, from any screen (see uiTouch)
 *     IN STOP                     the red button HELD for STOP_HOLD_MS. A tap on
 *                                 it is a mis-touch and sends IN OK instead, so
 *                                 that brushing it cannot discard the task.
 *     IN BODYTAP                  touch sensor = "not that one"
 *     IN PONG ...                 identity, on request
 *
 * Board: M5CoreS3  ·  Lib: M5Unified  ·  "USB CDC On Boot: Enabled"
 *
 * BUMP THE VERSION STRING FOR ANY CHANGE THE LAPTOP CANNOT OBSERVE -- a new
 * command, a changed colour, a moved button. Not only for the protocol.
 *
 * v3 was cut when SUMMON and SPENT were ADDED. SPENT's value then changed from
 * blue to amber inside v3, and because the string did not move, cores3_link's
 * version check reported a match against a board still carrying the blue. The
 * check was built to answer "did I reflash" and it answered "yes" wrongly,
 * which is worse than not having it. Keep it in step with cores3_link.py's
 * FIRMWARE_V.
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
  int aR = 255, aG = 242, aB = 224;             // warm at boot (= HUE WARM)

  // The clip streams the LEVEL; the colour comes from the state. Local breathing
  // is only a FALLBACK -- if no level has arrived for A_EXT_TIMEOUT the board
  // breathes on its own, because a stalled link should leave the robot looking
  // alive rather than frozen mid-flash. A frozen LED reads to a participant as
  // "it broke"; a breath reads as "still here".
  int aExt = -1;
  uint32_t aExtAt = 0;
  const uint32_t A_EXT_TIMEOUT = 500;

  // DO WHAT init() DOES, RATHER THAN CALLING IT.
  //
  // "ChainableLED has no member named 'init'" is a LIBRARY-VERSION error, not a
  // sketch error: it appears the first time this file is compiled on a machine
  // whose ChainableLED differs from the one that last flashed the board.
  // pjpmarques/ChainableLED exposes init(); several forks do the same work in
  // the constructor and never declare it. Both are correct libraries, and
  // hard-coding either choice makes the board flashable from exactly one desk.
  //
  // The obvious fix -- an overload pair on decltype(l.init()) -- CANNOT BE USED
  // HERE. See the note at the top of this file: the .ino preprocessor generates
  // a prototype for every function in the sketch, and it drops the
  // `template <typename T>` line when it does, so the injected prototype
  // references an undeclared T. That surfaces as "'l' was not declared in this
  // scope" pointing at a template that is perfectly valid C++. NO TEMPLATE
  // DEFINED IN A .ino SURVIVES THIS. Put one in a .h beside the sketch if it is
  // ever genuinely needed.
  //
  // So: no detection at all. init() only sets the two pins to OUTPUT and blanks
  // the strip, which is safe to do directly and harmless to repeat if the
  // constructor already did it. setColorRGB is the one call both versions
  // agree on, and antennaTick() below already depends on it.
  void antennaInit() {
    pinMode(LED_CLK, OUTPUT);
    pinMode(LED_DATA, OUTPUT);
    leds.setColorRGB(0, 0, 0, 0);
  }
  void setAntennaHue(int r, int g, int b) { aR = r; aG = g; aB = b; }
  void setAntennaLevel(int v) { aExt = constrain(v, 0, 255); aExtAt = millis(); }
  // The fallback breath must BE S1_IDLE's envelope, not a second, louder one.
  // It stands in for the idle state, so anything else makes the handover between
  // "laptop streaming" and "board on its own" visible -- and it was: the old
  // fallback ran 0.25..0.80 (64..204 of 255) while S1's authored envelope is
  // 0.8..2.5 on the generator's 0-8 Strength scale, i.e. 26..80. The board's
  // default was two and a half times brighter and wider than the design, so the
  // moment a clip took over the antenna got DIMMER. It read as the authored
  // envelope not working at all.
  //
  // These two numbers are tied to generate_s1_idle.py's LED_LO / LED_HI and
  // export_clip.py's LED_FULL = 8.0:  0.8/8 = 0.10,  2.5/8 = 0.31.
  // If S1's LED range changes, change these with it.
  static const float FB_LO = 0.0375f;         // = S1 LED_LO 0.30 / LED_FULL 8.0
  static const float FB_SPAN = 0.275f;        // up to S1 LED_HI 2.5 / 8.0 = 0.3125
  static const float FB_MS = 800.0f;          // 2*pi*800 = 5.0 s, S1's mean breath
  // The floor dropped from 0.10 to 0.0375 because the breath was not reading.
  // PWM is linear in luminance and perceived brightness goes roughly as L^0.43,
  // so 26..80 of 255 is only a 1.6x PERCEIVED swing. 10..80 makes it 2.5x for
  // the same peak. Lower than this starts to look like a fault rather than idle.

  void antennaTick() {
    uint32_t m = millis();
    float s = (aExt >= 0 && (m - aExtAt) < A_EXT_TIMEOUT)
              ? aExt / 255.0f                                     // clip-driven
              : FB_LO + FB_SPAN * (0.5f + 0.5f * sinf(m / FB_MS)); // = S1 idle
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
    // "PTT" is the id the loop speaks; the LABEL is what a participant reads,
    // and it is not the same word. Push-To-Talk is radio jargon -- nobody who
    // has not used a walkie-talkie decodes it, and the study is not the place
    // to find that out.
    //
    // The label says HOLD rather than TALK or RECORD because the failure it has
    // to prevent is specific: this button is a LATCH (PTT_DOWN / PTT_UP), and
    // every tap-to-record convention on earth says press once and let go. A
    // participant who taps produces an instant down-up pair, an empty
    // transcript, and S8 -- and reads that as the microphone being broken
    // rather than as having used the button wrongly. The record dot drawn above
    // it carries "this is the speaking one"; the word carries "and do not let
    // go", which the dot cannot say.
    uiBtns[uiNBtn++] = {"PTT",  8, 132, 150, 100, COL_GREEN, "HOLD"};
    uiBtns[uiNBtn++] = {"STOP", 166, 132, 146, 100, COL_RED, "STOP"};
  } else if (uiScreen == "recording") {
    // nothing: the finger is already on the button that matters
  } else if (uiScreen == "noticed") {
    // OK ALONE. This is the screen the participant is looking at when the robot
    // has just called them over, which makes it the one they are most likely to
    // touch and the worst place to keep a cancel: STOP discards the watch-spec,
    // and during the study's work phase that voids every scripted event still to
    // come. There is nothing to cancel here anyway -- the finding is already in
    // the feed, and OK is the whole of what this screen asks for.
    //
    // Losing STOP from this screen loses nothing: S7 returns to S5 on OK or on
    // its own timeout, and `tracking` still carries a hold-to-stop.
    uiBtns[uiNBtn++] = {"OK", 88, 168, 144, 64, COL_GREEN, "OK"};
  } else if (uiScreen == "error") {
    // GREEN, AND A TAP, because S8 is the one screen where getting out IS the
    // affirmative act. A hold would be the wrong gesture to demand of somebody
    // looking at a robot that has just failed, and there is no task left to
    // protect -- S8 is reached when a request was unusable or a plan never
    // arrived, so nothing is discarded by leaving.
    //
    // The laptop already gives up by itself after ST.S8_RECOVER_S (16 s). This
    // makes the same exit reachable immediately by whoever is watching.
    uiBtns[uiNBtn++] = {"OK", 88, 168, 144, 64, COL_GREEN, "OK"};
  } else if (uiScreen == "black") {
    // S1 FILMING ONLY (`EVT UI black`, sent by tools/film_s1.py): a screen
    // with a state word on it is a printed answer key -- the study's rule is
    // that the STATE is read from movement/light/sound, so the stimulus must
    // not caption itself. Pure black, ZERO buttons (the plain else below would
    // add a STOP, and a phantom touch target on an unlabeled screen is worse
    // than none). The antenna and speaker are separate channels and keep
    // running. Never used in a live session.
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
  int cx = b.x + b.w / 2;
  // The speaking button carries a record dot above its label. Icon AND word:
  // the dot says which button this is at a glance, the word says what to do
  // with it, and neither does the other's job. See the note in uiLayout().
  if (String(b.id) == "PTT") {
    M5.Display.fillCircle(cx, b.y + 34, 15, down ? TFT_BLACK : TFT_WHITE);
    M5.Display.setTextSize(3);
    M5.Display.drawString(b.label, cx, b.y + b.h - 28);
  } else {
    M5.Display.setTextSize(3);
    M5.Display.drawString(b.label, cx, b.y + b.h / 2);
  }
}

void uiDrawBar() {
  const int x = 30, y = 100, w = SCREEN_W - 60, h = 34;
  M5.Display.drawRoundRect(x, y, w, h, 6, TFT_DARKGREY);
  int fill = (w - 6) * constrain(recLevel, 0, 100) / 100;
  M5.Display.fillRect(x + 3, y + 3, fill, h - 6, COL_GREEN);
  M5.Display.fillRect(x + 3 + fill, y + 3, (w - 6) - fill, h - 6, TFT_BLACK);
}

const char* uiText() {
  // "idle" was the state's name leaking onto the participant's screen. It is
  // accurate and it is not for them: it describes what the machine is not
  // doing. "ready" describes what they can do, which is the only thing this
  // screen is for.
  if (uiScreen == "idle")      return "ready";
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
    // The bar alone showed LEVEL but never said RECORDING, so the one moment a
    // participant most needs confirming -- "is it getting this?" -- was carried
    // entirely by a bar that also moves when nobody speaks. Dot and word, the
    // same pairing as the button, in the same place they were promised.
    //
    // Drawn ONCE here rather than in uiTick: only the bar's own rectangle
    // repaints at 12 Hz, and a full redraw at that rate stalls loop(), which is
    // also driving the LED.
    M5.Display.fillCircle(64, 60, 11, COL_RED);
    M5.Display.setTextColor(TFT_WHITE);
    M5.Display.setTextDatum(middle_left);
    M5.Display.setTextSize(3);
    M5.Display.drawString("recording", 86, 60);
    uiDrawBar();
  } else if (uiScreen == "noticed") {
    M5.Display.setTextColor(TFT_WHITE);
    M5.Display.setTextDatum(middle_center);
    M5.Display.setTextSize(6);
    M5.Display.drawString(String(noticedN), SCREEN_W / 2, 70);
    M5.Display.setTextSize(3);
    M5.Display.drawString("noticed", SCREEN_W / 2, 122);
  } else if (uiScreen == "idle") {
    // Two lines, not "ready ^_^" on one. On one line the face trails the word
    // like punctuation; given its own line and a larger size it reads as a
    // face, which is the whole point of putting it there.
    //
    // PURE ASCII, deliberately. The default GFX font is 32..126 only -- a
    // Unicode kaomoji would come out as blanks or tofu, and it would do so
    // silently, on the one screen a participant looks at before deciding
    // whether this thing works.
    M5.Display.setTextColor(TFT_WHITE);
    M5.Display.setTextDatum(middle_center);
    M5.Display.setTextSize(3);
    M5.Display.drawString(uiText(), SCREEN_W / 2, 62);
    M5.Display.setTextSize(4);
    M5.Display.drawString("^_^", SCREEN_W / 2, 100);
  } else if (uiScreen == "black") {
    // nothing: the fillScreen at the top already painted it black, and the
    // final else would print uiText() -- exactly the caption this screen
    // exists to remove.
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

// ---- STOP IS HOLD-TO-STOP; A TAP MEANS OK ------------------------------------
//
// STOP is a CANCEL: the laptop discards the watch-spec and returns to S1_IDLE
// (states.py STOP_DISCARDS_TASK). During the study's fifteen-minute work phase
// the participant sits alone with this screen having been told to do whatever
// feels natural, and the red button is next to the green one. One brush of it
// used to end the task -- and it fired on touch-DOWN, so not even a deliberate
// press was required. Every scripted event after that point could then fire for
// nobody, and the session was not comparable with anyone else's.
//
// So the tap and the hold are separated HERE rather than on the laptop, because
// the laptop cannot tell them apart: `IN STOP` was the only line STOP ever sent,
// and no release event followed it. One line in, no duration.
//
//   tap   (< STOP_HOLD_MS)  -> IN OK    the same line the green button sends.
//                                       On `noticed` it returns S7 to S5 -- back
//                                       to the watch-spec, task intact -- and on
//                                       every other screen the flow ignores it.
//                                       So a mis-touch costs nothing anywhere.
//   hold  (>= STOP_HOLD_MS) -> IN STOP  unchanged, and still available from every
//                                       screen. §4 needs it: PTT is accepted only
//                                       from S1_IDLE and STOP is the only route
//                                       there, so taking a second brief depends
//                                       on it.
//
// The laptop is untouched by this. It still maps `IN OK` -> ok and `IN STOP` ->
// stop exactly as before; what changed is which finger gesture produces which.
//
// stopHeld is a latch for the same reason pttHeld is one: uiSet() clears uiDown
// on any screen change, so a press that outlives a repaint would otherwise lose
// its button identity and send neither line.
static const uint32_t STOP_HOLD_MS = 800;
bool     stopHeld  = false;
uint32_t stopSince = 0;

void uiTouch() {
  auto t = M5.Touch.getDetail();
  if (t.wasPressed()) {
    for (int i = 0; i < uiNBtn; i++) {
      if (uiHit(uiBtns[i], t.x, t.y)) {
        uiDown = i; uiDrawBtn(i);
        String id = uiBtns[i].id;
        if      (id == "PTT")  { pttHeld = true; Serial.println("IN PTT_DOWN"); }
        else if (id == "OK")   { Serial.println("IN OK");   M5.Speaker.tone(1600, 60); }
        else if (id == "STOP") { pttHeld = false;
                                 stopHeld = true; stopSince = millis();
                                 M5.Speaker.tone(1200, 25); }   // touched, not yet acted on
        break;
      }
    }
    return;
  }

  // THE HOLD COMMITS WHILE THE FINGER IS STILL DOWN, not on release. Two
  // reasons, and the second is the one that matters: the participant hears the
  // stop tone at the moment it becomes a stop, so the gesture teaches itself
  // rather than being explained; and a commit that waited for release could be
  // lost the same way IN PTT_UP once was, if the screen repaints in between.
  if (stopHeld && millis() - stopSince >= STOP_HOLD_MS) {
    stopHeld = false;
    Serial.println("IN STOP");
    M5.Speaker.tone(700, 90);
  }

  // Release. Two independent triggers, because one hang is one too many:
  // the release EVENT, and -- if that is missed while the screen is being
  // repainted -- simply no longer having a finger down.
  bool up = t.wasReleased() || ((pttHeld || stopHeld) && M5.Touch.getCount() == 0);
  if (!up) return;

  // Lifted before the hold matured: a tap, and a tap is OK. Sent here and not in
  // the press branch so that the two outcomes are mutually exclusive by
  // construction -- one touch can never produce both lines.
  if (stopHeld) {
    stopHeld = false;
    Serial.println("IN OK");
    M5.Speaker.tone(1600, 60);
  }

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
  // Derivation for every value here is in robot_motion/LED_COLOR_DESIGN.md.
  // Short version, because the reasoning is not guessable from the numbers:
  //
  //   Colour is the WEAKEST channel -- 69% classification alone against 92%
  //   for colour+motion (Loffler et al., HRI'18, n=33). So these are not
  //   chosen to be expressive on their own, which they cannot be. They are
  //   chosen to (a) be tellable apart and (b) not contradict the clip that is
  //   playing, because a colour fighting its motion is worse than no colour.
  //
  //   BRIGHTNESS CARRIES AROUSAL. Both Loffler and Song & Yamada put the
  //   passive state at reduced brightness and the active one at full. The old
  //   table ran everything at V ~1.0 and used hue alone, which is why idle and
  //   error ended up 5 degrees apart and indistinguishable.
  else if (cmd == "HUE") {
    String s = arg; s.toUpperCase();
    // EVERY VALUE HERE IS AT FULL BRIGHTNESS. These set the COLOUR only; how
    // bright it is at any instant is `s` in antennaTick(), streamed from the
    // clip's led column. Multiplying a dimmed hue by a dim envelope dims twice:
    // WARM was briefly set to V .38, and against S1's envelope (0.0375..0.3125)
    // that put the antenna at (3,2,0) -- a regime where 8-bit PWM no longer
    // controls colour, and this module's strong blue die wins. Idle came up
    // BLUE. Arousal belongs in the envelope, which is per-clip and exists.
    //
    // SATURATION DOES NOT TRANSFER FROM THE PAPERS. Loffler displayed their
    // colours on an ANDROID PHONE SCREEN -- a large flat field with surrounding
    // context. This is one diffused point source: small, self-luminous, with no
    // white anywhere near it for the eye to judge against. Below roughly S 0.5
    // a point LED collapses to WHITE. SPENT was set to the paper's 230/40 and
    // came up pure white on the bench, which is how this was found.
    //
    // So hues are taken from the literature and SATURATION IS RE-DERIVED FOR
    // THIS DISPLAY: anything that must read as a colour sits at S >= 0.6.
    // The one exception is deliberate and is also a citation -- Song & Yamada
    // map RELAXED to WHITE, so idle reading as a warm white is the intended
    // percept rather than a washed-out amber.
    if      (s == "WARM")  setAntennaHue(255, 242, 224);   // present, idle
    else if (s == "COOL")  setAntennaHue( 64, 255, 255);   // attending
    // negation. "Seeing red" is the one colour metaphor both papers agree on,
    // at full brightness for high arousal. Agrees with S6's horizontal shake.
    else if (s == "RED")   setAntennaHue(255,   0,   0);   // negation (S6)
    // S7, the summons. 45/100/100 -- and this is the ONLY colour in the table
    // taken from a RESULT rather than from a candidate list. Loffler's Table 1
    // ("final expression designs tested in the user evaluation") gives joy as
    // 45/100/100 after a 22-participant manipulation check narrowed 57 stimuli
    // to 12. Anger there is 0/100/100, which is exactly RED above.
    //
    // WAS GREEN, then briefly MAGENTA, and both were wrong for reasons worth
    // keeping. Green is the low-arousal positive corner (Song & Yamada map it
    // to *happy*, a calm positive) while S7 is the highest-arousal moment in
    // the library -- and COL_GREEN is the OK button eight centimetres away, so
    // "I found something" and "dismiss it" were one colour in one visual field.
    // Magenta 315/100/100 was then chosen off Loffler's JOY CANDIDATE list --
    // but 315 is one of the variants that LOST the manipulation check, and it
    // was picked as if it were a finding. It read as harsh and unpleasant on
    // the bench, which is presumably why 22 undergraduates dropped it.
    else if (s == "SUMMON") setAntennaHue(255, 191,   0);   // a finding (S7)
    // S8, and this one went out and came back. It was amber, was moved to blue
    // on Loffler's "sadness is blue", and is amber again -- dim.
    //
    // THE COLOUR WAS NEVER THE DEFECT. S8 was reported as an alarm because it
    // ran at 143 (brighter than S5B_TRACK's 96, i.e. being stuck outshone
    // working) and because its envelope re-inflated LO->HI every four seconds,
    // which is an alarm's rhythm. Both are fixed in the clip: 13..51, decaying.
    // Changing the hue as well was an over-correction of a brightness problem.
    //
    // AND FOR A LIGHT, AMBER IS THE BETTER READING. Kovecses' sadness metaphors
    // as Loffler lists them are darkness, "lacking brightness", passiveness, and
    // cold -- "losing his father put his fire out". Blue is one entry in that
    // set and it is the SYMBOLIC one, which is what suits a colour field on a
    // phone screen (their display). A failing LIGHT does not turn blue; embers,
    // a guttering candle and a browning-out bulb all shift warm as they die.
    // "The light going out of it" is literally a warm-shift.
    //
    // At S .95 and 13..51 this is a coal, not a warning lamp. It is separated
    // from SUMMON by 5x peak brightness and from RED by 3.7x -- brightness and
    // motion, which is where separation belongs (colour is the weak channel).
    else if (s == "SPENT")  setAntennaHue(255, 134,  13);   // stuck (S8)
    // Old names kept so a CoreS3 that has not been reflashed still lights up
    // rather than going dark mid-session. They map to the NEW colours: the
    // point is the colour, not the word.
    else if (s == "GREEN")  setAntennaHue(255, 191,   0);
    else if (s == "ALARM")  setAntennaHue(255, 134,  13);
    else {
      // AN UNKNOWN HUE USED TO DO NOTHING, WHICH IS THE WORST AVAILABLE
      // BEHAVIOUR. The chain simply fell through and the antenna kept whatever
      // colour was last set -- so a board flashed before SUMMON/SPENT existed
      // showed S8 in S1's colour, and the two states that most need telling
      // apart became one. Nothing reported a fault: every layer had done
      // exactly what it was written to do.
      //
      // The compatibility aliases above are the WRONG DIRECTION for this. They
      // protect an old laptop driving new firmware; the failure that actually
      // happens is a new laptop driving old firmware, and no amount of aliasing
      // here can reach a board that has not been flashed. Only saying so can.
      Serial.print("IN WARN unknown hue "); Serial.println(s);
    }
  }
  else if (cmd == "REST") {
    // End of a run. Without this, a test that finishes on S8 leaves the amber
    // alarm flashing indefinitely: the streamed level outlives the laptop going
    // quiet.
    sfxLen = sfxIdx = 0;
    aExt = -1;
    setAntennaHue(255, 242, 224);   // = WARM. Keep in sync with HUE above.
    uiSet("idle");
  }
  else if (cmd == "PING") Serial.println("IN PONG cores3_sidekick v5");
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
  Serial.println("IN HELLO cores3_sidekick v5");
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
