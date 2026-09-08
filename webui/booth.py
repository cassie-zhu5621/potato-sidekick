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

    choose   two Japanese sentences. Tapping one posts the ENGLISH sentence
             through the same door a spoken request uses (stt.manual), so the
             planner really compiles it -- the tap replaces the speaking, not
             the pipeline. Waking the robot does NOT leave this screen: the tap
             on its head is the move that brings a visitor here, and taking the
             buttons away at that moment is the one thing the page must not do.
    room     scanning AND watching, which used to be two. The five stations fill
             in as the head captures them, the planner's boxes and a red frame
             arrive with the plan, and the red frame MOVES when the head is
             corrected. It replaced a screen that said "tracking..." over
             nothing, which told a visitor less than the robot in front of them
             already was. The 5-20 s VLM wait becomes the part people point at.
    notice   the same prompt as the robot's own screen, either of which takes
             the OK -- then the story.

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
]

_EN = {c["id"]: c["en"] for c in CHOICES}

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
        phase = "ack"
    else:
        # S2_LISTEN LANDS HERE ON PURPOSE. That is the robot lifting its head
        # because the visitor touched it, and the next thing they have to do is
        # pick a task -- so the choice must still be on the screen. Sending them
        # to a "woken" screen took the two buttons away at the exact moment they
        # were needed. The state is shown by the face strip instead, which is
        # what a state change is worth here: a glance, not a screen.
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
        "describe": STATE.get("describe") or "",
        "noticed": STATE.get("noticed_n") or 0,
        "stories": feed_records[-6:][::-1],
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
#strip{display:flex;gap:8px;justify-content:center;flex:none;padding:14px 0 0}
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
  font-weight:800;font-size:clamp(48px,9vw,132px);line-height:1.22;letter-spacing:.01em;
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

.bar{font-size:15px;color:#8a867d;display:flex;gap:14px;align-items:center}
.dot{width:11px;height:11px;border-radius:50%;background:#cfe33a;
  animation:p 1.4s ease-in-out infinite}
@keyframes p{0%,100%{opacity:.25}50%{opacity:1}}

#veil{position:fixed;inset:0;background:rgba(10,10,10,.86);display:none;
  align-items:center;justify-content:center;padding:34px}
#veil.on{display:flex}
.pop{background:#faf9f6;color:#141414;border-radius:26px;padding:36px;
  max-width:640px;width:100%;text-align:center}
.pop h2{font-weight:700;font-size:30px;line-height:1.3;margin:0 0 14px}
.pop p{font-size:19px;line-height:1.6;color:#3a3a35;margin:0 0 26px}
.ok{background:#141414;color:#faf9f6;border:0;border-radius:16px;
  padding:20px 0;width:100%;font-weight:700;font-size:23px}
.ok:active{background:#cfe33a;color:#141414}

.story{display:flex;gap:14px;align-items:center;background:#1d1d1a;
  border-radius:16px;padding:12px;margin-bottom:10px}
.story img{width:104px;height:66px;object-fit:cover;border-radius:9px;flex:none}
.story div{font-size:15px;line-height:1.45;color:#d8d5cc}
.story span{display:block;font:12px ui-monospace,monospace;color:#7a776f;margin-top:4px}
#stories{overflow-y:auto;flex:1;-webkit-overflow-scrolling:touch}
</style></head><body>
<div id=strip></div>
<div id=app></div>
<div id=veil><div class=pop>
  <h2 id=pt>気づきました</h2><p id=pd></p>
  <button class=ok onpointerdown="ok()">OK</button>
</div></div>
<script>
let S={phase:'choose'},sent=0;

async function pick(id){
  if(Date.now()-sent<1500) return; sent=Date.now();
  await fetch('/booth/choose',{method:'POST',body:id});
}
async function ok(){
  if(Date.now()-sent<800) return; sent=Date.now();
  document.getElementById('veil').classList.remove('on');
  await fetch('/booth/ok',{method:'POST'});
}
const esc=s=>String(s==null?'':s).replace(/[<>&]/g,c=>({'<':'&lt;','>':'&gt;','&':'&amp;'}[c]));

function cells(){
  const out=[];
  for(let i=0;i<5;i++){
    const sh=S.shots[i];
    const pan=[-60,-30,0,30,60][i];
    if(!sh){out.push(`<div class="cell wait">${pan>0?'+':''}${pan}\u00b0</div>`);continue;}
    const on = S.chosen_pan!=null && sh.pan===S.chosen_pan;
    out.push(`<div class="cell${on?' on':''}">
      <img src="/sweepimg/${esc(sh.dir)}/${esc(sh.file)}">
      <span class=tag>${sh.pan>0?'+':''}${sh.pan}°</span></div>`);
  }
  return out.join('');
}

function strip(){
  document.getElementById('strip').innerHTML=(S.faces||[]).map(
    ([k,f,l])=>`<div class="f${k===S.face?' on':''}">${esc(f)}<b>${esc(l)}</b></div>`
  ).join('');
}

function render(){
  strip();
  const a=document.getElementById('app');
  if(S.phase==='choose'){
    a.innerHTML=`<div><h1>なにを見ていてほしい？</h1>
      <div class=sub>ポテトの頭にさわると、起きます</div></div>
      <div class=grow style="gap:18px">`+S.choices.map(c=>
        `<div class=card onpointerdown="pick('${c.id}')">${esc(c.ja)}
           <small>${esc(c.sub)}</small></div>`).join('')+`</div>`;
  } else if(S.phase==='ack'){
    a.innerHTML=`<div><h1>わかりました</h1>
      <div class=sub>${esc(S.request_ja)}</div></div>
      <div class=grow style="align-items:center;justify-content:center">
      <div class=bar><span class=dot></span>うなずいています</div></div>`;
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
    const veil=document.getElementById('veil');
    if(S.phase==='notice'){
      document.getElementById('pd').textContent=S.describe||'';
      veil.classList.add('on');
    } else veil.classList.remove('on');
  }catch(e){}
}
setInterval(poll,200); poll();
</script></body></html>"""
