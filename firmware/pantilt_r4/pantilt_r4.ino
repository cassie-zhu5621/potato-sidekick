/*
 * pantilt_r4.ino — Arduino Uno R4 + 2x MG90S  (Second Attention pan-tilt)
 *
 * PURE MUSCLE: the laptop brain (live_loop.py) decides everything; this board
 * just eases the servos to a target sent over USB serial.
 *
 * Serial (115200), one line:
 *   "pan,tilt"   -> ease to that target (degrees relative to center), e.g. "30,-10"
 *   "off"        -> relax (detach) the servos now
 *   "beep"       -> 'noticed' chirp on the passive buzzer (two rising notes)
 *
 * AUTO-RELAX: if no command arrives for IDLE_MS, the servos detach so they stop
 * buzzing / heating up when idle. Any new "pan,tilt" re-attaches and moves them.
 *
 * Wiring: PAN -> D9, TILT -> D10, servo V+ -> external 5V, GND shared, R4 on USB.
 *         PASSIVE BUZZER: + -> D8, - -> GND.  (passive = needs tone(); a chirp is
 *         the audible half of the 'noticed' cue, synced with the nod by the laptop.)
 *         RED LED: long leg (anode +) -> 220-330 ohm resistor -> D6 (PWM);
 *                  short leg (cathode -) -> GND.
 *
 * LED behaviour (no laptop change needed): SEARCHING -> slow calm breathing; on "beep"
 * (the laptop's 'found/nod' moment) -> a sudden fast flutter, then back to breathing.
 */

#include <Servo.h>

const int PAN_PIN  = 9;
const int TILT_PIN = 10;
const int BUZZ_PIN = 8;
const int LED_PIN  = 6;                  // red LED (PWM) — anode->220R->D6, cathode->GND
const unsigned long BREATH_MS = 3000;    // breathing period while searching

const int PAN_CENTER  = 90;
const int TILT_CENTER = 90;
const int PAN_MIN = 30,  PAN_MAX = 150;
const int TILT_MIN = 55, TILT_MAX = 135;   // raised from 125: camera mounted tilted up, need more DOWN travel

const int STEP_MS = 12;                 // glide pace
const unsigned long IDLE_MS = 4000;     // relax servos after this much idle

Servo pan, tilt;
int curPan = PAN_CENTER, curTilt = TILT_CENTER;
bool live = false;                      // are the servos currently driven (attached)?
unsigned long lastCmd = 0;

int clampi(int v, int lo, int hi) { return v < lo ? lo : (v > hi ? hi : v); }

void attachServos() {
  if (!live) {
    pan.attach(PAN_PIN, 500, 2400);
    tilt.attach(TILT_PIN, 500, 2400);
    pan.write(curPan);
    tilt.write(curTilt);
    live = true;
  }
}

void relax() {
  if (live) {
    pan.detach();
    tilt.detach();
    live = false;
    Serial.println("relaxed (idle)");
  }
}

void chirp() {                      // 'noticed': two short rising notes (friendly, not alarm-like)
  tone(BUZZ_PIN, 880);  delay(80);
  tone(BUZZ_PIN, 1320); delay(120);
  noTone(BUZZ_PIN);
}

void breathe() {                    // SEARCHING: slow calm pulse (smooth sine fade, PWM)
  unsigned long phase = millis() % BREATH_MS;
  int b = (int)(127.5 * (1.0 - cos(TWO_PI * phase / (float)BREATH_MS)));
  analogWrite(LED_PIN, b);
}

void ledBurst() {                   // FOUND!: a sudden excited fast flutter, then resume breathing
  for (int i = 0; i < 8; i++) {
    analogWrite(LED_PIN, 255); delay(45);
    analogWrite(LED_PIN, 0);   delay(45);
  }
}

void easeTo(int tp, int tt) {
  tp = clampi(tp, PAN_MIN, PAN_MAX);
  tt = clampi(tt, TILT_MIN, TILT_MAX);
  attachServos();
  while (curPan != tp || curTilt != tt) {
    if (curPan  < tp) curPan++;  else if (curPan  > tp) curPan--;
    if (curTilt < tt) curTilt++; else if (curTilt > tt) curTilt--;
    pan.write(curPan);
    tilt.write(curTilt);
    breathe();                   // keep the LED breathing during the glide
    delay(STEP_MS);
  }
  lastCmd = millis();
}

void setup() {
  Serial.begin(115200);
  pinMode(LED_PIN, OUTPUT);
  attachServos();              // center on boot
  lastCmd = millis();
  Serial.println("pantilt ready — 'pan,tilt' to move, 'off' to relax; auto-relax when idle");
}

void loop() {
  if (Serial.available()) {
    String line = Serial.readStringUntil('\n');
    line.trim();
    if (line == "off" || line == "relax") {
      relax();
    } else if (line == "beep") {
      ledBurst();                // FOUND: sudden fast flutter (synced with the nod)
      chirp();
      lastCmd = millis();
      Serial.println("beep");
    } else {
      int comma = line.indexOf(',');
      if (comma > 0) {
        int dPan  = line.substring(0, comma).toInt();
        int dTilt = line.substring(comma + 1).toInt();
        easeTo(PAN_CENTER + dPan, TILT_CENTER + dTilt);
        Serial.print("-> pan="); Serial.print(curPan);
        Serial.print(" tilt="); Serial.println(curTilt);
      }
    }
  }

  if (live && millis() - lastCmd > IDLE_MS) {
    relax();                   // no commands for a while -> stop buzzing/heating
  }

  breathe();                   // SEARCHING/idle: the red LED breathes (calm 'I'm watching')
}
