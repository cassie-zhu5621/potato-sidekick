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

THE FOUR SCREENS follow the loop the paper is about, one screen per phase:

    choose   two Japanese sentences. Tapping one posts the ENGLISH sentence
             through the same door a spoken request uses (stt.manual), so the
             planner really compiles it -- the tap replaces the speaking, not
             the pipeline.
    sweep    the five stations as the head captures them, then the planner's own
             boxes, and a red frame on the one it chose. This is the 5-20 s VLM
             wait, turned into the part of the demo people point at.
    watch    quiet. What it is watching for, and nothing else moving.
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
    elif now in ("S4_PLAN",) or STATE.get("plan_pending"):
        phase = "sweep"
    elif now in ("S5A_SETTLE", "S5B_TRACK", "S6_FINETUNE"):
        phase = "watch"
    elif now in ("S2_LISTEN", "S3_ACK"):
        phase = "ack"
    else:
        phase = "choose"

    shots, chosen = [], None
    if sweep_meta:
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
body{margin:0;background:#141414;color:#f2f0ea;
  font-family:-apple-system,"Hiragino Sans","Noto Sans JP",sans-serif;
  height:100vh;overflow:hidden;user-select:none}
#app{height:100%;display:flex;flex-direction:column;padding:28px;gap:20px}
h1{font:600 30px/1.3 inherit;margin:0;letter-spacing:.02em}
.sub{font:15px inherit;color:#8a867d;margin-top:6px}
.grow{flex:1;min-height:0;display:flex;flex-direction:column;gap:18px}

.card{background:#1d1d1a;border:2px solid #33332e;border-radius:22px;
  padding:30px 28px;font:600 27px/1.45 inherit;color:#f2f0ea;text-align:left;
  flex:1;display:flex;flex-direction:column;justify-content:center;gap:10px}
.card:active{background:#cfe33a;color:#141414;border-color:#cfe33a}
.card small{font:400 16px inherit;color:#8a867d}
.card:active small{color:#3a3a20}

.grid{flex:1;display:grid;grid-template-columns:repeat(3,1fr);gap:12px;min-height:0}
.cell{position:relative;background:#1d1d1a;border:3px solid #2a2a27;
  border-radius:14px;overflow:hidden;display:flex;align-items:center;
  justify-content:center}
.cell img{width:100%;height:100%;object-fit:cover;display:block}
.cell.on{border-color:#e0554a;box-shadow:0 0 0 3px rgba(224,85,74,.35)}
.cell .tag{position:absolute;left:8px;bottom:6px;font:12px ui-monospace,monospace;
  color:#cfe33a;background:rgba(20,20,20,.72);padding:2px 7px;border-radius:7px}
.cell.wait{border-style:dashed;color:#4a4a44;font:13px ui-monospace,monospace}

.bar{font:15px inherit;color:#8a867d;display:flex;gap:14px;align-items:center}
.dot{width:11px;height:11px;border-radius:50%;background:#cfe33a;
  animation:p 1.4s ease-in-out infinite}
@keyframes p{0%,100%{opacity:.25}50%{opacity:1}}

#veil{position:fixed;inset:0;background:rgba(10,10,10,.86);display:none;
  align-items:center;justify-content:center;padding:34px}
#veil.on{display:flex}
.pop{background:#faf9f6;color:#141414;border-radius:26px;padding:36px;
  max-width:640px;width:100%;text-align:center}
.pop h2{font:700 30px/1.3 inherit;margin:0 0 14px}
.pop p{font:19px/1.6 inherit;color:#3a3a35;margin:0 0 26px}
.ok{background:#141414;color:#faf9f6;border:0;border-radius:16px;
  padding:20px 0;width:100%;font:700 23px inherit}
.ok:active{background:#cfe33a;color:#141414}

.story{display:flex;gap:14px;align-items:center;background:#1d1d1a;
  border-radius:16px;padding:12px;margin-bottom:10px}
.story img{width:104px;height:66px;object-fit:cover;border-radius:9px;flex:none}
.story div{font:15px/1.45 inherit;color:#d8d5cc}
.story span{display:block;font:12px ui-monospace,monospace;color:#7a776f;margin-top:4px}
#stories{overflow-y:auto;flex:1;-webkit-overflow-scrolling:touch}
</style></head><body>
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
    if(!sh){out.push('<div class="cell wait">…</div>');continue;}
    const on = S.chosen_pan!=null && sh.pan===S.chosen_pan;
    out.push(`<div class="cell${on?' on':''}">
      <img src="/sweepimg/${esc(sh.dir)}/${esc(sh.file)}">
      <span class=tag>${sh.pan>0?'+':''}${sh.pan}°</span></div>`);
  }
  return out.join('');
}

function render(){
  const a=document.getElementById('app');
  if(S.phase==='choose'){
    a.innerHTML=`<div><h1>なにを見ていてほしい？</h1>
      <div class=sub>ポテトの頭にさわると、起きます</div></div>
      <div class=grow>`+S.choices.map(c=>
        `<div class=card onpointerdown="pick('${c.id}')">${esc(c.ja)}
           <small>${esc(c.sub)}</small></div>`).join('')+`</div>`;
  } else if(S.phase==='ack'){
    a.innerHTML=`<div><h1>わかりました</h1>
      <div class=sub>${esc(S.request_ja)}</div></div>
      <div class=grow style="align-items:center;justify-content:center">
      <div class=bar><span class=dot></span>うなずいています</div></div>`;
  } else if(S.phase==='sweep'){
    a.innerHTML=`<div><h1>部屋を見ています</h1>
      <div class=sub>${S.chosen_pan==null?'5方向を撮って、いま考えています'
        :'赤いところを見張ります'}</div></div>
      <div class=grid>${cells()}</div>
      <div class=bar>${S.seen.length?'見えたもの： '+S.seen.map(esc).join('・'):''}</div>`;
  } else {
    a.innerHTML=`<div><h1>見張っています</h1>
      <div class=sub>${esc(S.request_ja)}</div></div>
      <div class=grow><div class=bar><span class=dot></span>
        ${S.noticed?S.noticed+' 件 見つけました':'まだ何も起きていません'}
        ・ ちがう方を見てほしいときは頭をさわってください</div>
        <div id=stories>`+S.stories.map(s=>
          `<div class=story><img src="/thumb/${esc(s.thumb)}">
             <div>${esc(s.note)}<span>${esc(s.time)}</span></div></div>`).join('')
        +`</div></div>`;
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
