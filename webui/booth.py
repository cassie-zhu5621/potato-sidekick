"""booth.py — the iPad the visitor touches, at an exhibition.

WHAT THIS IS FOR. The study runs in a quiet room with a moderator, a briefing
and seven minutes. A trade-show stand has none of those: strangers arrive, stay
ninety seconds and leave, the hall is at 75 dB, and the visitor reads Japanese
while the system speaks English. This page is the whole visitor-facing interface
under those conditions.

THE DIVISION OF LABOUR, and it is the reason this file is small:

    the robot   motion, light, sound, a face          -- NO language at all
    this page   Japanese, and every touch             -- ALL the language

Nothing about the robot changes for the exhibition. Its screen keeps the English
words and the faces it had in the study, and the faces are what carry it: a
sleeping face and a woken one need no translation. Everything a visitor has to
READ is here, so translating the stand means translating one file.

THE SCREENS. Fewer than there are states, on purpose -- the strip along the top
carries the state, and a page swap is reserved for a change in what the visitor
can DO:

    sleep    a sleeping face and one line asking to be touched. A stand with a
             list of options on it is a kiosk; a stand with something asleep on
             it is a thing you want to wake -- and waking it is how everything
             else here starts, so the tap has a consequence on both screens at
             once, which is what teaches the gesture.
    choose   three Japanese sentences. Tapping one posts the ENGLISH sentence
             through the same door a spoken request uses (stt.manual), so the
             planner really compiles it -- the tap replaces the speaking, not
             the pipeline. Waking the robot does NOT leave this screen: the tap
             on its head is the move that brings a visitor here, and taking the
             buttons away at that moment is the one thing the page must not do.
    room     scanning AND watching, which used to be two -- and the nod between
             them, which is 1.7 s and does not deserve a page of its own. Five
             stations fill in as the head captures them, the planner's boxes and
             a red frame arrive with the plan, and the red frame MOVES when the
             head is corrected. The watched direction is the LIVE camera, not
             the photograph taken during the sweep; tapping it fills the screen
             with the rule beside it, tapping again puts it back. The sixth cell
             of the 3x2 grid holds the rule itself. The 5-20 s VLM wait becomes
             the part people point at.
    notice   the same prompt as the robot's own screen, either of which takes
             the OK.
    report   AFTER OK THE TABLET STOPS FOLLOWING THE ROBOT. The robot goes
             straight back to watching -- right, it has a job -- but the visitor
             is owed the report they just asked for, and the storyboard takes
             another 6-45 s to narrate it. Following the robot back to the room
             grid threw that away and made OK look like it had cancelled
             something. The page waits here, on its own, until the story count
             rises, then shows that one story.
    wall     every story of the day, reachable from the choose screen -- which
             is where a visitor stands with nothing to do, and where the next
             one arrives.

Polling, not sockets: 200 ms on a LAN is imperceptible and the codebase already
polls. The study page's 1200 ms was too slow here -- the prompt has to land with
the chirp, and 1.2 s of lag reads as two separate machines.
"""
from __future__ import annotations

# id -> (Japanese for the visitor, English for the planner)
#
# BOTH are about an object the visitor puts down themselves, because "that is
# mine" is what makes delegating it mean anything. They differ in the relation,
# not the object: hands-on is the one tuned hardest and needs someone to act,
# gathering is ambient and a crowded hall supplies it for free -- a slow deliberate
# demo and a fast one, and the visitor picks without being told that is the choice.
CHOICES = [
    {"id": "touch",
     "ja": "荷物に触ったら教えて",
     "sub": "だれかが手を伸ばしたら",
     "en": "tell me if someone touches my bag"},
    {"id": "gather",
     "ja": "人が集まったら教えて",
     "sub": "まわりに人が集まってきたら",
     "en": "tell me if people gather around"},
    # THE ROOM THE STAND IS ACTUALLY IN. Presentations start at the posters all
    # around, all day: someone stands up in front of theirs and two or three
    # people collect to listen. It costs the visitor nothing to arrange, it will
    # happen whether or not anyone is waiting for it, and it makes the point
    # better than a staged event could -- the robot is watching the room the
    # visitor is standing in, not a scene set up for it.
    #
    # Bound to the POSTER, which is what separates it from `gather`: that one is
    # about people collecting near the visitor, this one about people collecting
    # somewhere specific. Without the object the two would compile to the same
    # watch entry and the choice would be a choice of wording only.
    {"id": "poster",
     "ja": "ポスターの前で発表がはじまったら教えて",
     "sub": "だれかが人を集めて話しはじめたら",
     "en": "tell me if people gather in front of a poster to listen to someone"},
]

_EN = {c["id"]: c["en"] for c in CHOICES}

# The relation vocabulary, short. Same ids and same words the developer page
# uses (webui.server.REL_NAMES); English on purpose -- these are the system's
# own terms and an onlooker reading over a visitor's shoulder is usually the
# person who wants to see them.
REL_NAMES = {
    1: "gaze", 2: "joint", 3: "eye", 4: "point", 5: "prox", 6: "F-form",
    7: "appr", 8: "lean", 9: "hands-on", 10: "gather", 11: "turn",
}

# THE ROBOT'S OWN FACES, copied from the firmware's uiFace(). Deliberately the
# same characters: the strip on the tablet and the face on the robot are then
# one vocabulary rather than two, and a visitor who looks from one to the other
# sees the same thing twice instead of having to learn a second code.
#
# S4 has no face on the robot -- planning is the one screen whose job is to send
# the eye to the room instead of the screen -- so the strip shows a looking mark
# rather than inventing an expression the robot does not wear.
FACES = [
    ("S1_IDLE",     "-_-",    "ねてる"),
    ("S2_LISTEN",   "._.",    "きづいた"),
    ("S3_ACK",      "^o^",    "わかった"),
    ("S4_PLAN",     "\u30fb\u30fb\u30fb",    "みてる"),
    ("S5B_TRACK",   "o_o",    "みはり"),
    ("S6_FINETUNE", ">_<",    "ちがう"),
    ("S7b",         "\\^o^/", "よんでる"),
]

# states that light the same lamp
_FACE_OF = {"S5A_SETTLE": "S5B_TRACK", "S7a": "S7b", "S8_ERROR": "S6_FINETUNE"}


def face_key(state):
    """Which lamp in the strip is lit for this state."""
    state = _FACE_OF.get(state, state)
    return state if any(k == state for k, _f, _l in FACES) else "S1_IDLE"


def english_for(choice_id):
    """The sentence the planner is given. None for an id we did not write."""
    return _EN.get(str(choice_id or "").strip())


# --------------------------------------------------------------------------- #
def booth_state(STATE, feed_records, sweep_meta):
    """Everything the tablet needs, in one poll.

    Assembled here rather than in the page so the page has no logic to get
    wrong, and so `phase` is decided ONCE -- the tablet and the robot must never
    disagree about which moment this is.
    """
    states = STATE.get("states") or []
    now = ""
    for s in states:
        if s.get("now"):
            now = s.get("name") or ""
            break
    now = now or STATE.get("flow_state") or ""

    if now in ("S7a", "S7b"):
        phase = "notice"
    elif (now in ("S4_PLAN", "S5A_SETTLE", "S5B_TRACK", "S6_FINETUNE")
            or STATE.get("plan_pending")):
        # SCANNING AND WATCHING ARE ONE SCREEN. They were two, and the watching
        # one said "tracking..." over an empty page, which tells a visitor
        # nothing they cannot already see -- the robot is right there, holding
        # still. The five frames answer the only question worth asking, WHICH WAY
        # IS IT LOOKING, and they answer it continuously: the grid fills in as
        # the head captures it, the red frame lands when the plan does, and it
        # MOVES when the head is corrected. Which state it is in is a glance at
        # the strip.
        phase = "room"
    elif now == "S3_ACK":
        # THE NOD DOES NOT GET A SCREEN. It is 1.7 s, and a page that appears and
        # vanishes inside two seconds is a flash, not information. It also
        # happens in two different places -- after a choice, and after OK -- so
        # any one screen would be wrong in one of them. The strip shows ^o^ and
        # the page stays where the visitor's attention already is: about to
        # sweep, or reading the report they just acknowledged.
        phase = "room"
    elif now == "S1_IDLE" or not now:
        # ASLEEP IS A FACE, NOT A MENU. A stand with a list of options on it is
        # a kiosk; a stand with something sleeping on it is a thing you want to
        # wake. It also gives the head tap a consequence on BOTH screens at
        # once, which is what teaches the gesture -- and the gesture is how
        # everything else here starts.
        phase = "sleep"
    else:
        # S2_LISTEN LANDS HERE. The robot has lifted its head because the
        # visitor touched it, and the choice is what they need next -- so the
        # tap is exactly what brings the two sentences up. Sending them to a
        # "woken" screen instead would take the page one step further from the
        # thing they came to do.
        phase = "choose"

    # WHICH FRAME IS RED: the station nearest where it is ACTUALLY AIMED, not
    # the one the sweep scored highest. They agree until the visitor taps the
    # head -- and that tap is the whole point of the Correct beat, so the red
    # frame has to move with it or the gesture has no answer on the tablet.
    # Before an aim exists (mid-sweep), the sweep's own pick stands in.
    shots, chosen = [], None
    if sweep_meta:
        aim = STATE.get("aimed_pan")
        pans = [sh.get("pan") for sh in (sweep_meta.get("shots") or [])
                if sh.get("pan") is not None]
        if aim is not None and pans:
            chosen = min(pans, key=lambda p: abs(p - float(aim)))
        else:
            chosen = sweep_meta.get("richest_pan")
        for sh in sweep_meta.get("shots") or []:
            shots.append({
                "pan": sh.get("pan"),
                "file": sh.get("file"),
                "dir": sweep_meta.get("dir") or "",
                "n": len(sh.get("dets") or []),
                "focus": sum(1 for d in (sh.get("dets") or [])
                             if d.get("tier") == "focus"),
            })

    # The judge's sentence is written BEFORE S7 plays, so it is already here at
    # the moment the visitor is asked to press OK. The strip takes another
    # 6-45 s; showing this first means OK is never answered by a blank screen.
    return {
        "phase": phase,
        "state": now,
        "faces": FACES,
        "face": face_key(now),
        "choices": CHOICES,
        "request_ja": STATE.get("booth_choice_ja") or "",
        "shots": shots,
        "chosen_pan": chosen,
        # THE WATCH-SPEC, in the developer page's own vocabulary. The grid has
        # five stations in six cells, and the empty one is where "what would
        # make it call you" belongs: a visitor who can read hands-on + bag knows
        # what to DO, and an onlooker can see the system is running a rule
        # rather than a guess. Same fields the LIVE panel renders from -- see
        # webui.server.build_status -- so the two cannot drift.
        "watch": [{"label": r.get("label") or "",
                   "on": r.get("onobj") or "",
                   "all": r.get("all") or [], "any": r.get("any") or [],
                   "not": r.get("not") or [], "then": r.get("then") or [],
                   "sat": bool(r.get("sat")), "cool": bool(r.get("cool")),
                   "truth": r.get("on") or {}}
                  for r in (STATE.get("status") or [])],
        "rel_names": REL_NAMES,
        "describe": STATE.get("describe") or "",
        "noticed": STATE.get("noticed_n") or 0,
        # THE WHOLE WALL, newest first, and a COUNT the page can compare
        # against. After OK the tablet stops following the robot and waits for a
        # story to appear -- it cannot know one has landed without a number that
        # changes. The robot, meanwhile, has already gone back to watching; the
        # two are doing different things on purpose from that moment.
        "stories": feed_records[-40:][::-1],
        "n_stories": len(feed_records),
        "seen": (STATE.get("seen") or [])[:8],
    }


PAGE = """<!doctype html><html lang=ja><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1,user-scalable=no,viewport-fit=cover">
<meta name=apple-mobile-web-app-capable content=yes>
<title>ポテト</title><style>
*{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
body{margin:0;background:#141414;color:#f2f0ea;display:flex;flex-direction:column;
  font-family:-apple-system,"Hiragino Sans","Noto Sans JP",sans-serif;
  height:100vh;overflow:hidden;user-select:none}

/* THE STATE STRIP. Small, at the top, always there. A state change is worth a
   glance, not a screen -- swapping the whole page when the robot lifts its head
   took the two task buttons away at the moment the visitor needed them. */
/* Faces centred, the way into the day's stories on the right. Same height as
   a face chip: it is a way out, not an offer, and it must not compete with the
   two things a visitor is actually being asked to choose between. */
#top{display:flex;align-items:center;flex:none;padding:14px 20px 0;gap:8px}
#strip{display:flex;gap:8px;justify-content:center;flex:1}
/* ONE rule for #app, not two. It carried height:100% AND flex:1 at the same
   time, so with the strip above it the page ran past the viewport and the
   bottom -- the grid, the buttons -- was clipped away under overflow:hidden. */
#app{flex:1;min-height:0;display:flex;flex-direction:column;
  padding:16px 28px 28px;gap:14px}
.f{font:14px ui-monospace,monospace;color:#3f3f3a;background:#1a1a17;
  border-radius:9px;padding:5px 10px;letter-spacing:.06em;
  transition:color .18s,background .18s}
.f.on{color:#141414;background:#cfe33a;font-weight:700}
.f b{display:block;font-weight:400;font-size:9px;letter-spacing:0;opacity:.55;
  margin-top:1px}
h1{font-weight:600;font-size:clamp(26px,3.4vh,40px);line-height:1.3;margin:0;letter-spacing:.02em}
.sub{font-size:clamp(15px,1.9vh,21px);color:#8a867d;margin-top:8px}
.grow{flex:1;min-height:0;display:flex;flex-direction:column;gap:18px}

/* NO `font:` SHORTHAND ON THIS PAGE, and it is not a style preference.
   `font: 700 40px/1.3 inherit` is INVALID -- `inherit` is a CSS-wide keyword,
   legal only as an entire value, never as the family slot -- so the browser
   drops the whole declaration and the element renders at the inherited default.
   Every size on this page was being thrown away that way, which is why the
   choice buttons stayed at 16px however large the number was set. Longhands
   only; the family comes down from body by inheritance anyway.

   THE CHOICE IS THE WHOLE SCREEN. A visitor decides from a metre away,
   standing, in a hall, in about two seconds -- so the sentence is set at a
   size that is readable at that distance and centred, and the card is the
   touch target rather than the text inside it. clamp() keeps it right on an
   iPad mini and a 12.9 alike without a media query. */
.card{background:#1d1d1a;border:3px solid #33332e;border-radius:28px;
  padding:20px;color:#f2f0ea;text-align:center;
  font-weight:800;font-size:clamp(44px,min(8.5vw,10vh),120px);line-height:1.22;
  letter-spacing:.01em;
  flex:1;display:flex;flex-direction:column;align-items:center;
  justify-content:center;gap:14px}
.card:active{background:#cfe33a;color:#141414;border-color:#cfe33a;
  transform:scale(.985)}
.card small{font-weight:400;font-size:clamp(17px,2.4vw,32px);color:#8a867d}
.card:active small{color:#3a3a20}

.grid{flex:1;display:grid;grid-template-columns:repeat(3,1fr);gap:12px;min-height:0}
.cell{position:relative;background:#1d1d1a;border:3px solid #2a2a27;
  border-radius:14px;overflow:hidden;display:flex;align-items:center;
  justify-content:center}
.cell img{width:100%;height:100%;object-fit:cover;display:block}
.cell.on{border-color:#e0554a;box-shadow:0 0 0 3px rgba(224,85,74,.35)}
.cell .tag{position:absolute;left:8px;bottom:6px;font:12px ui-monospace,monospace;
  color:#cfe33a;background:rgba(20,20,20,.72);padding:2px 7px;border-radius:7px}
.cell.wait{border-style:dashed;color:#54544c;font:22px ui-monospace,monospace}
.cell.live{border-color:#e0554a}          /* the frame; the picture is #live */

#live{position:fixed;display:none;object-fit:cover;z-index:5;
  border:3px solid #e0554a;border-radius:14px;
  box-shadow:0 0 0 3px rgba(224,85,74,.35);
  transition:left .22s,top .22s,width .22s,height .22s}
#live.big{border-radius:18px;box-shadow:0 0 0 4px rgba(224,85,74,.3)}
/* EXPANDED: everything except the picture and the rule goes quiet. The rule is
   NOT redrawn beside the live view -- it is already in the sixth cell, and the
   expanded picture is sized to stop short of it, so the one copy stays where
   the visitor last saw it. */
#app.dim > *{opacity:.14;transition:opacity .22s}
#app.dim .grid{opacity:1}
#app.dim .cell{opacity:.10;transition:opacity .22s}
#app.dim .cell.spec{opacity:1}
#app.dim .bar{display:none}
.hint{position:fixed;z-index:6;font-size:clamp(13px,1.5vw,19px);color:#6f6c65;
  font-family:ui-monospace,monospace;display:none}
.hint.on{display:block}

/* THE WAIT AFTER OK. 6-45 s with one small line of text on it reads as a
   machine that has stopped. The bar is deliberately NOT a real measure of
   anything -- nothing here knows how long the narration will take, and a bar
   that claimed to would be lying -- it is a sign of life, which is the only
   honest thing to show. The face is doing the same job the robot's own face
   does: it is what makes waiting feel like being waited WITH. */
/* ASLEEP. A stand with a list of options on it is a kiosk; a stand with
   something sleeping on it is a thing you want to wake -- and waking it is how
   everything else here starts, so the page has to ask for that and nothing
   else. Same face the robot's own screen wears in idle. */
.sleep{flex:1;display:flex;flex-direction:column;align-items:center;
  justify-content:center;gap:30px}
.sface{font-size:clamp(64px,12vw,180px);font-family:ui-monospace,monospace;
  color:#4a4a44;position:relative;animation:breathe 4.4s ease-in-out infinite}
.sface i{position:absolute;left:104%;top:-.15em;font-size:.34em;
  font-style:normal;color:#3a3a35;letter-spacing:.24em;
  animation:zzz 4.4s ease-in-out infinite}
@keyframes breathe{0%,100%{opacity:.55}50%{opacity:1}}
@keyframes zzz{0%,100%{opacity:.15;transform:translateY(4px)}
               50%{opacity:.9;transform:translateY(-6px)}}
.stap{font-size:clamp(20px,2.6vw,34px);color:#8a867d}

.waitbox{flex:1;display:flex;flex-direction:column;align-items:center;
  justify-content:center;gap:22px}
.wface{font-size:clamp(28px,4.5vw,60px);font-family:ui-monospace,monospace;
  color:#cfe33a;animation:bob 2.2s ease-in-out infinite}
@keyframes bob{0%,100%{transform:translateY(0)}50%{transform:translateY(-9px)}}
.wtxt{font-size:clamp(19px,2.4vw,30px);color:#8a867d}
.wbar{width:min(62%,520px);height:12px;border-radius:99px;background:#22221e;
  overflow:hidden}
.wbar i{display:block;height:100%;width:38%;border-radius:99px;
  background:linear-gradient(90deg,#cfe33a,#88e4ea);
  animation:slide 1.7s cubic-bezier(.6,0,.4,1) infinite}
@keyframes slide{0%{transform:translateX(-110%)}100%{transform:translateX(280%)}}

/* THE SIXTH CELL. Five stations in a 3x2 grid leave one empty, and what belongs
   there is the rule -- a visitor who can read hands-on + bag knows what to DO,
   and an onlooker can see the system is running a condition rather than
   guessing. Same fields and same words as the developer page's LIVE panel. */
.spec{border-style:solid;border-color:#2a2a27;background:#161614;
  flex-direction:column;justify-content:center;gap:9px;padding:12px;
  font-family:ui-monospace,monospace}
.spec .lb{font-size:clamp(12px,1.25vw,17px);color:#6f6c65;letter-spacing:.08em}
.spec .row{display:flex;flex-wrap:wrap;gap:5px;align-items:center;
  justify-content:center}
.rel{font-size:clamp(12px,1.3vw,18px);padding:4px 9px;border-radius:10px;
  border:1px solid #3a3a35;color:#7c796f}
.rel.t{background:#88e4ea;border-color:#88e4ea;color:#101010;font-weight:700}
.op{font-size:clamp(11px,1.1vw,15px);color:#55524b}
.obj{font-size:clamp(12px,1.3vw,18px);padding:4px 9px;border-radius:10px;
  background:#1c1c19;border:1px solid #3a3a35;color:#cfe33a}
.spec.sat{border-color:#d95b5b;background:#201616}

.bar{font-size:15px;color:#8a867d;display:flex;gap:14px;align-items:center}
.dot{width:11px;height:11px;border-radius:50%;background:#cfe33a;
  animation:p 1.4s ease-in-out infinite}
@keyframes p{0%,100%{opacity:.25}50%{opacity:1}}

/* ABOVE EVERYTHING, ALWAYS. The live view is z-index 5 and can be expanded to
   most of the screen; the one thing that must never be behind it is the robot
   asking to be answered. */
#veil{position:fixed;inset:0;background:rgba(10,10,10,.9);display:none;
  z-index:20;align-items:center;justify-content:center;padding:34px}
#veil.on{display:flex}
.pop{background:#faf9f6;color:#141414;border-radius:26px;padding:36px;
  max-width:640px;width:100%;text-align:center}
.pop h2{font-weight:700;font-size:30px;line-height:1.3;margin:0 0 14px}
.pop p{font-size:19px;line-height:1.6;color:#3a3a35;margin:0 0 26px}
.ok{background:#141414;color:#faf9f6;border:0;border-radius:16px;
  padding:20px 0;width:100%;font-weight:700;font-size:23px}
.ok:active{background:#cfe33a;color:#141414}

/* A STORY IS THE STRIP ITSELF. The storyboard composites its panels into one
   wide jpg -- that IS the shape of the finding, several moments in a row -- so
   it is shown at full height and scrolled sideways rather than squeezed into a
   thumbnail. The sentence sits under the picture, where a caption goes. */
.story{background:#1d1d1a;border-radius:18px;padding:14px;margin-bottom:14px}
.pan{overflow-x:auto;overflow-y:hidden;-webkit-overflow-scrolling:touch;
  border-radius:12px;background:#101010;scrollbar-width:none}
.pan::-webkit-scrollbar{display:none}
.pan img{height:clamp(150px,26vh,340px);width:auto;max-width:none;display:block}
.story p{margin:14px 4px 2px;font-size:clamp(19px,2.4vw,32px);line-height:1.4;
  color:#e6e3da}
.story span{display:block;margin:6px 4px 0;color:#7a776f;
  font:13px ui-monospace,monospace}
.swipe{font-size:12px;color:#54544c;margin:6px 4px 0;
  font-family:ui-monospace,monospace}

.wallbtn{flex:none;cursor:pointer}
.wallbtn:active{color:#141414;background:#cfe33a}
#stories{overflow-y:auto;flex:1;-webkit-overflow-scrolling:touch}
</style></head><body>
<!-- THE LIVE VIEW LIVES OUTSIDE #app AND IS NEVER RE-CREATED.
     An <img> on an MJPEG stream holds an open connection; putting it inside the
     innerHTML that render() rewrites would tear that connection down and open a
     new one every poll -- 5 times a second -- which is a black flicker and a new
     TCP connection each time. It is positioned OVER the cell instead, by
     rectangle, so the grid can be rewritten as often as it likes. -->
<img id=live onpointerdown="zoom()">
<div id=top><div id=strip></div>
  <div class="f wallbtn" id=wb onpointerdown="wall()"></div></div>
<div id=app></div>
<div class=hint id=hint>もう一度タップでもどる</div>
<div id=veil><div class=pop>
  <h2 id=pt>気づきました</h2><p id=pd></p>
  <button class=ok onpointerdown="ok()">OK</button>
</div></div>
<script>
let S={phase:'choose'},sent=0;

// THE TABLET STOPS FOLLOWING THE ROBOT AFTER OK. The robot goes straight back
// to watching -- that is right, it has a job -- but the visitor is owed the
// report they just asked for, and it takes another 6-45 s to write. Sending the
// tablet back to the room grid with it threw that away and looked like the OK
// had cancelled something. MODE overrides `phase` while it is set; null means
// follow the robot again.
let MODE=null;        // null | 'report' | 'wall'
let WAIT_FROM=0;      // how many stories existed when OK was pressed
let BIG=false;        // the live view, expanded

function zoom(){BIG=!BIG;place();}

// PUT #live WHERE IT BELONGS THIS FRAME. Over the red cell normally; filling
// the left of the screen when expanded, with the rule beside it. Measuring the
// cell rather than styling the image into the grid is what lets the grid be
// rewritten five times a second without ever touching the stream.
function place(){
  const el=document.getElementById('live'), hint=document.getElementById('hint');
  const cell=document.getElementById('livecell'), app=document.getElementById('app');
  const spec=document.querySelector('.cell.spec');
  if(!cell || MODE!==null){ el.style.display='none'; hint.classList.remove('on');
                            app.classList.remove('dim'); BIG=false; return; }
  if(!el.src) el.src='/stream.mjpg';      // opened once, on first need
  el.style.display='block';
  el.classList.toggle('big',BIG);
  app.classList.toggle('dim',BIG);
  hint.classList.toggle('on',BIG);
  if(BIG && spec){
    // STOP SHORT OF THE RULE. The sixth cell is the only other thing left lit,
    // so the picture takes the grid's area minus that column -- one copy of the
    // rule, exactly where it already was, no second rendering to keep in step.
    const g=document.querySelector('.grid').getBoundingClientRect();
    const r=spec.getBoundingClientRect();
    Object.assign(el.style,{left:g.left+'px',top:g.top+'px',
                            width:(r.left-g.left-14)+'px',height:g.height+'px'});
    Object.assign(hint.style,{left:r.left+'px',
                              top:(r.bottom+10)+'px',width:r.width+'px'});
  }else{
    const r=cell.getBoundingClientRect();
    Object.assign(el.style,{left:r.left+'px',top:r.top+'px',
                            width:r.width+'px',height:r.height+'px'});
  }
}
addEventListener('resize',place);

async function pick(id){
  if(Date.now()-sent<1500) return; sent=Date.now();
  await fetch('/booth/choose',{method:'POST',body:id});
}
async function ok(){
  if(Date.now()-sent<800) return; sent=Date.now();
  document.getElementById('veil').classList.remove('on');
  WAIT_FROM=S.n_stories||0; MODE='report';        // wait for the NEXT one
  render();
  await fetch('/booth/ok',{method:'POST'});
}
function wall(){MODE='wall';render();}
function back(){MODE=null;render();}
const esc=s=>String(s==null?'':s).replace(/[<>&]/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;'}[c]));

function specInner(){
  const w=(S.watch||[])[0];
  if(!w) return '<span class=lb>NO RULE YET</span>';
  const ids=[...w.all,...w.any,...w.then];
  const op = w.then.length ? 'THEN' : (w.any.length && !w.all.length ? 'OR' : 'AND');
  const chips=ids.map((id,k)=>
    (k?`<span class=op>${op}</span>`:'')+
    `<span class="rel${w.truth[String(id)]?' t':''}">${esc(S.rel_names[id]||id)}</span>`
  ).join('');
  return `<span class=lb>${w.sat?'いま成立':'これを待っています'}</span>
    <div class=row>${chips}${w.on?`<span class=op>on</span>
      <span class=obj>${esc(w.on)}</span>`:''}</div>`;
}

function specCell(){
  const w=(S.watch||[])[0];
  return `<div class="cell spec${w&&w.sat?' sat':''}">${specInner()}</div>`;
}

function cells(){
  const out=[];
  for(let i=0;i<5;i++){
    const sh=S.shots[i];
    const pan=[-60,-30,0,30,60][i];
    if(!sh){out.push(`<div class="cell wait">${pan>0?'+':''}${pan}\u00b0</div>`);continue;}
    const on = S.chosen_pan!=null && sh.pan===S.chosen_pan;
    // THE CHOSEN CELL CARRIES NO PICTURE OF ITS OWN. It is the frame; #live is
    // laid over it, so the one direction the robot is actually watching shows
    // the live camera rather than a photograph taken during the sweep.
    out.push(on
      ? `<div class="cell live" id=livecell>
           <span class=tag>${sh.pan>0?'+':''}${sh.pan}° LIVE</span></div>`
      : `<div class=cell>
           <img src="/sweepimg/${esc(sh.dir)}/${esc(sh.file)}">
           <span class=tag>${sh.pan>0?'+':''}${sh.pan}°</span></div>`);
  }
  out.push(specCell());       // the sixth cell of the 3x2 grid
  return out.join('');
}

function strip(){
  document.getElementById('strip').innerHTML=(S.faces||[]).map(
    ([k,f,l])=>`<div class="f${k===S.face?' on':''}">${esc(f)}<b>${esc(l)}</b></div>`
  ).join('');
  const w=document.getElementById('wb');
  w.innerHTML=`\u2630 ${S.n_stories||0}<b>きろく</b>`;
  w.style.visibility = MODE==='wall' ? 'hidden' : 'visible';
}

function story1(s){
  // /frame/ not /thumb/: the frame IS the strip, several panels wide. The
  // thumbnail is one squashed copy of it and loses the thing that makes a
  // story a story -- that it went on.
  const wide=(s.shots||1)>1;
  return `<div class=story>
    <div class=pan><img src="/frame/${esc(s.frame||s.thumb)}"></div>
    ${wide?'<div class=swipe>\u2190 よこにスワイプ</div>':''}
    <p>${esc(s.note)}</p><span>${esc(s.time)}</span></div>`;
}

function render(){
  strip();
  const a=document.getElementById('app');
  if(MODE==='wall'){
    a.innerHTML=`<div><h1>これまでに気づいたこと</h1>
      <div class=sub>${(S.stories||[]).length} 件</div></div>
      <div id=stories>`+(S.stories||[]).map(story1).join('')
      +`</div><button class=ok onpointerdown="back()">もどる</button>`;
    return;
  }
  if(MODE==='report'){
    const fresh=(S.n_stories||0)>WAIT_FROM ? S.stories[0] : null;
    a.innerHTML=`<div><h1>${fresh?'これを見つけました':'まとめています'}</h1>
      <div class=sub>${esc(S.request_ja)}</div></div>
      <div class=grow>`+(fresh
        ? story1(fresh)+`<button class=ok onpointerdown="back()">とじる</button>`
        : `<div class=waitbox>
             <div class=wface>\uff08\u30fb\u03c9\u30fb\uff09</div>
             <div class=wtxt>まとめています…</div>
             <div class=wbar><i></i></div>
           </div>`)+`</div>`;
    return;
  }
  if(S.phase==='sleep'){
    a.innerHTML=`<div class=sleep>
        <div class=sface>(-_-)<i>z z z</i></div>
        <div class=stap>あたまに そっとさわってください</div>
      </div>`;
    return;
  }
  if(S.phase==='choose'){
    a.innerHTML=`<div><h1>なにを見ていてほしい？</h1>
      <div class=sub>ポテトの頭にさわると、起きます</div></div>
      <div class=grow style="gap:18px">`+S.choices.map(c=>
        `<div class=card onpointerdown="pick('${c.id}')">${esc(c.ja)}
           <small>${esc(c.sub)}</small></div>`).join('')+`</div>`;
  } else {
    // ONE SCREEN FOR SCANNING AND FOR WATCHING. The only question a visitor has
    // in either is which way it is looking, and the grid answers it the whole
    // time: cells fill as the head captures them, the red frame lands with the
    // plan, and it moves when the head is corrected. "tracking..." over an empty
    // page told them nothing the robot in front of them was not already saying.
    const done = S.chosen_pan!=null;
    a.innerHTML=`<div><h1>${done?'ここを見張っています':'部屋を見ています'}</h1>
      <div class=sub>${esc(S.request_ja)}</div></div>
      <div class=grid>${cells()}</div>
      <div class=bar>${done
        ? (S.noticed? S.noticed+' 件 見つけました ・ ' : '')
          + 'ちがう方を見てほしいときは、頭をさわってください'
        : '<span class=dot></span>5方向を撮って、いま考えています'}</div>`;
  }
}

async function poll(){
  try{
    const r=await fetch('/booth.json',{cache:'no-store'});
    const n=await r.json();
    const changed = JSON.stringify(n)!==JSON.stringify(S);
    S=n;
    if(changed) render();
    place();
    const veil=document.getElementById('veil');
    // NOT while the tablet is on a report or the wall. The robot re-enters S7
    // on the NEXT finding, and a prompt reappearing over the report the visitor
    // is still reading is the same interruption OK was meant to end.
    if(S.phase==='notice' && MODE===null){
      document.getElementById('pd').textContent=S.describe||'';
      veil.classList.add('on');
    } else veil.classList.remove('on');
  }catch(e){}
}
setInterval(poll,200); poll();
</script></body></html>"""
