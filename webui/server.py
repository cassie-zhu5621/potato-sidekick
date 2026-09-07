"""
attention_ui.py — web UI for the VLM-FIRST pipeline (attention_system.py).

The control surface follows the inverted architecture: the old web_demo's taste box
(tuning a back-end judge) is replaced by a CONTEXT box (driving the front-end planner):

  LEFT   : live annotated stream
  RIGHT  : THE PLAN — current context, the VLM's "why", and each watch entry with its
           LIVE state (satisfied / cooling / progress) — the legible, contestable part
  BELOW  : NOTICED feed (records), same as before
  BOTTOM : "Describe the scene" input -> POST /context -> the system re-plans next frame

Same STATE/LOCK/thumbs contract as web_demo, so attention_demo.publish() works unchanged.
"""

from __future__ import annotations
import json, os, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

# user_pan: where the participant is sitting, in Blender degrees. Locked by hand
# from the panel below, once, at the start of a session -- the seat is fixed and
# known, so asking a detector to rediscover it every frame would be inventing
# uncertainty. None = S7 plays at its authored template angles.
def _pan_reach():
    """(min, max) degrees the body can turn to, or None if calibration is absent.

    Wrapped in a try because the web UI is also opened against recorded sessions
    on machines with no robot config, and a seat row that fails to render is a
    worse outcome than one that falls back to its defaults.
    """
    try:
        from robot.pose import reach_deg
        lo, hi = reach_deg("pan")
        return [round(lo, 1), round(hi, 1)]
    except Exception:
        return None


# pending_user_pan: the loop drains this and hands it to the player.
STATE = {"jpg": None, "feed": [], "thumbs": {}, "frames": {},
         "user_pan": None, "pending_user_pan": "unset",
         "context": "", "why": "", "entries": [],      # [(expr, label)]
         "status": [],                                  # [dict per entry: see build_status]
         "judgments": {},                               # label -> candidate/judging/result
         "seen": [], "detect": [], "focus": [],         # relevance layer: enumerate -> tier
         # THE RESEARCHER'S TWO EMERGENCY CONTROLS, both one-shot flags the loop
         # drains and clears. They replaced a panel for hand-editing the watch
         # entries (removed 2026-08-12): correcting the SPEC mid-session asks the
         # researcher to think in relation ids while an actor is mid-scene and a
         # participant is watching. These ask instead for the two things that are
         # actually wanted at that moment -- notice this now, and go look again.
         "pending_finding": False, "pending_resweep": False,
         # What the participant called it. Typed here, sent to the board as
         # EVT NAME, and drawn on `idle` for the rest of the session.
         "bot_name": "", "pending_name": None,
         # Additions for the robot, kept SEPARATE from the plan slots above. An
         # earlier version showed the state machine by writing state rows into
         # `entries`/`status`, which silently replaced THE PLAN -- the one panel
         # that shows what the system is actually for. State monitoring is a
         # debugging need; it gets its own page.
         "transcript": "", "states": [], "suppressed": [], "collecting": [],
         "pending_pan": None,                           # developer re-aim
         "pan_now": None, "pan_scores": [],             # where it looks / sweep scores
         "pending_context": None}
LOCK = threading.Lock()
ARGS = None


def feed_dir():
    """Where sweep images live, or "." if the caller never had a feed.

    `ARGS` used to be reached into directly as ARGS.feed_dir, guarded only by
    `if ARGS else`. That guard tests for None, not for a namespace that simply
    lacks the attribute -- and noticebot_loop passes Namespace(web_port=...)
    because it has no feed. The AttributeError then fired inside a request
    thread, where a traceback is printed and the request is dropped: the page
    half-loads and nothing says why.
    """
    return getattr(ARGS, "feed_dir", None) or "."

# id -> short human name for the 11-row relation vocabulary (config_gate/docs/relation_table.md).
# Shown in THE PLAN panel so an entry reads "3 eye-contact AND 5 proxemics", not "single:3".
REL_NAMES = {
    1: "gazing-at", 2: "joint-attn", 3: "eye-contact", 4: "pointing",
    5: "proxemics", 6: "F-formation", 7: "approach/depart", 8: "lean-in",
    9: "hands-on", 10: "gathering", 11: "turn-taking",
}


def build_status(statuses, entries, truth):
    """Pack per-entry state for the UI: operator id groups + which rows are T this frame.

    statuses : list[EntryStatus] from WatchExecutor.step
    entries  : WatchExecutor.entries (each has all/any/not/then id lists + label)
    truth    : {row_id: bool} for the current frame
    """
    out = []
    for s, e in zip(statuses, entries):
        ids = set(e["all"]) | set(e["any"]) | set(e["not"]) | set(e["then"])
        out.append({
            "label": s.label, "sat": bool(s.satisfied), "cool": bool(s.cooling),
            "cooldown_remaining_s": round(float(s.cooldown_remaining_s), 1),
            "all": list(e["all"]), "any": list(e["any"]),
            "not": list(e["not"]), "then": list(e["then"]),
            "on": {str(r): bool(truth.get(r, False)) for r in ids},
            # WHICH OBJECT THE ENTRY IS ABOUT. Note the key is NOT `on` -- that
            # name was already taken, by the per-relation truth map above, and
            # the collision is why the binding never reached the page at all:
            # the spec had it, `_focus_ok` enforced it, and the panel showed a
            # bare "hands-on" with nothing attached. Reported 2026-08-11 as
            # "hands_on isn't bound to the whiteboard" about a plan that was
            # bound to it correctly.
            "onobj": e.get("on") or "",
        })
    return out

PAGE = """<!doctype html><html><head><meta charset=utf-8><title>attention system</title>
<style>
.namerow{display:flex;gap:8px;margin-top:8px}
#nm{flex:1;font:13px ui-monospace,monospace;background:#1c1c19;color:#e8e6e0;
  border:1px solid #3a3a35;border-radius:8px;padding:7px 10px}

.op.onobj{background:#1c1c19;border:1px solid #3a3a35;color:#cfe33a;
  font:11px ui-monospace,monospace;padding:3px 8px;border-radius:9px;margin-left:6px}
.op.onobj.none{color:#8a867d}

.devonly{font:11px ui-monospace,monospace;color:#8a867d;letter-spacing:.08em;
  text-transform:uppercase;margin-left:8px}
.erow{display:flex;flex-wrap:wrap;align-items:center;gap:6px;padding:9px 0;
  border-bottom:1px solid #2a2a27}
.chip{font:11px ui-monospace,monospace;padding:4px 8px;border-radius:11px;
  border:1px solid #3a3a35;color:#8a867d;cursor:pointer;user-select:none}
.chip.on{background:#cfe33a;border-color:#cfe33a;color:#141414;font-weight:700}
.eon{font:12px ui-monospace,monospace;background:#1c1c19;border:1px solid #3a3a35;
  color:#e8e6e0;border-radius:7px;padding:5px 8px;width:130px}
.ebtn{font:12px ui-sans-serif;background:transparent;border:1px solid #3a3a35;
  color:#e8e6e0;border-radius:8px;padding:5px 11px;cursor:pointer}
.ebtn:hover{border-color:#cfe33a}
.ebtn.go{background:#cfe33a;border-color:#cfe33a;color:#141414;font-weight:700}
.ebtn.force{background:#e0a052;border-color:#e0a052;color:#141414;font-weight:700}

 /* palette: #262626 black · #A1CC48 light green (main) · #D9E157 yellow-green
    #334020 dark olive · #D95B5B red (satisfied) · #E89D9D light red (cooling)
    · #88E4EA blue (lit trigger operators) */
 body{margin:0;background:#262626;color:#e8e8e4;font-family:ui-sans-serif,system-ui,sans-serif}
 .tabs{display:flex;gap:6px;padding:10px 14px 0}
 .tab{background:#1d1d1d;border:1px solid #3a3a36;color:#9a9a90;border-radius:8px 8px 0 0;
   padding:9px 18px;font-size:14px;font-weight:700;letter-spacing:.04em;cursor:pointer}
 .tab.active{background:#262626;color:#A1CC48;border-color:#3a3a36;border-bottom-color:#262626}
 .tab .badge{display:inline-block;margin-left:7px;min-width:16px;padding:0 6px;font-size:12px;
   border-radius:9px;background:#334020;color:#D9E157;text-align:center}
 .page{display:none}
 .page.show{display:block}
 .wrap{display:flex;gap:14px;padding:14px;box-sizing:border-box;height:calc(100vh - 64px)}
 .left{flex:2;min-width:0;display:flex;flex-direction:column;gap:10px}
 .right{flex:1.2;min-width:400px;display:flex;flex-direction:column;gap:10px}
 img#v{width:100%;border:1px solid #3a3a36;border-radius:10px;background:#000}
 h3{margin:4px 2px;font-size:13px;color:#A1CC48;font-weight:700;letter-spacing:.05em}
 .plan{flex:1;overflow:auto;background:#1d1d1d;border:1px solid #3a3a36;border-radius:10px;padding:14px}
 .ctx{font-size:28px;line-height:1.25;font-weight:600;color:#e8e8e4}
 .why{font-size:16px;color:#A1CC48;margin:8px 0 4px;font-style:italic}
 .entry{margin-top:16px;padding-top:14px;border-top:1px solid #3a3a36}
 .entry:first-of-type{border-top:none}
 /* the state header — bigger now */
 .ehead{display:flex;gap:10px;align-items:center;margin-bottom:10px}
 .dot{width:13px;height:13px;border-radius:50%;flex:none;background:#5a5a52}
 .dot.sat{background:#D95B5B;box-shadow:0 0 10px #D95B5B}.dot.cool{background:#E89D9D;box-shadow:0 0 9px #E89D9D}
 .estate{font-size:21px;font-weight:800;letter-spacing:.05em;text-transform:uppercase;color:#9a9a90}
 .estate.sat{color:#D95B5B}.estate.cool{color:#E89D9D}
 .estate.judging{color:#88E4EA}.estate.confirmed{color:#A1CC48}
 .estate.rejected{color:#E89D9D}
 .estate.grouped{color:#D9E157}.estate.suppressed{color:#7a7a70}
 .ecap{color:#7a7a70;font-size:17px}
 /* the lit-up logic line — operators are the stars, bigger than relations */
 .logic{display:flex;flex-wrap:wrap;gap:9px;align-items:center}
 .rel{display:inline-flex;align-items:center;gap:6px;padding:7px 13px;border-radius:7px;
   border:1px solid #334020;background:#2b2b2b;color:#9a9a90;font-size:17px;transition:all .12s}
 .rel .rid{font-size:13px;color:#6a6a60;font-family:ui-monospace,monospace}
 .rel.on{border-color:#D9E157;color:#262626;background:#D9E157;font-weight:600;
   box-shadow:0 0 11px rgba(217,225,87,.5)}
 .rel.on .rid{color:#334020}
 /* operators = hollow rounded outline rings (connectors), never filled —
    deliberately a different shape from the filled relation chips.
    lit trigger = palette blue #88E4EA */
 .op{font-family:ui-monospace,monospace;font-size:22px;font-weight:800;letter-spacing:.09em;
   min-width:22px;text-align:center;padding:11px 18px;border-radius:999px;color:#8a8a80;
   background:transparent;border:2px solid #5a5a52;line-height:1;text-transform:uppercase}
 .op.lit{color:#88E4EA;border-color:#88E4EA;background:transparent;
   box-shadow:0 0 13px rgba(136,228,234,.5);text-shadow:0 0 7px rgba(136,228,234,.5)}
 .op.paren{border:none;color:#7a7a70;padding:7px 2px;font-size:28px;background:transparent}
 .detail{color:#7a7a70;font-size:11px;margin-top:6px;font-family:ui-monospace,monospace}
 /* feed gallery */
 .feedwrap{padding:14px;box-sizing:border-box;height:calc(100vh - 64px);overflow:auto}
 .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px}
 .card{display:flex;gap:10px;background:#1d1d1d;border:1px solid #3a3a36;border-radius:10px;padding:10px}
 .card img{width:120px;height:90px;object-fit:cover;border-radius:6px;flex:none;background:#000}
 .note{font-size:13.5px;line-height:1.4}
 .meta{font-size:11px;color:#9a9a90;margin-top:3px}
 .empty{color:#7a7a70;font-size:13px;padding:24px}
 input{width:100%;box-sizing:border-box;background:#1d1d1d;border:1px solid #3a3a36;color:#e8e8e4;
   border-radius:8px;padding:10px 12px;font-size:14px}
 input:focus{outline:none;border-color:#A1CC48}
 /* SCENE -> PLAN relevance panel: what the VLM saw, tiered */
 .rel{flex:none;max-height:30%}
 .tierrow{display:flex;flex-wrap:wrap;gap:7px}
 .tier{padding:5px 11px;border-radius:7px;font-size:15px;border:1px solid #3a3a36}
 .tier.focus{background:#D9E157;color:#262626;font-weight:700;border-color:#D9E157;
   box-shadow:0 0 8px rgba(217,225,87,.4)}
 .tier.context{background:#334020;color:#D9E157;border-color:#334020}
 .tier.ignore{background:#2b2b2b;color:#6a6a60;text-decoration:line-through}
 .tier.nofind{outline:2px dashed #D95B5B;outline-offset:1px;opacity:.65}
 .tierkey{margin-top:11px;display:flex;gap:8px;flex-wrap:wrap;font-size:11px;color:#7a7a70}
 .tierkey .tier{font-size:11px;padding:2px 7px}
 /* SWEEPS gallery */
 .swcard{background:#1d1d1d;border:1px solid #3a3a36;border-radius:10px;padding:12px;margin-bottom:14px}
 .swhead{font-size:14px;color:#e8e8e4;margin-bottom:8px}
 .swhead b{color:#A1CC48}
 .pano{width:100%;border-radius:8px;border:1px solid #3a3a36;margin:8px 0;background:#000}
 .swrow{display:flex;gap:8px;overflow-x:auto;padding-bottom:6px}
 .swframe{flex:none;width:210px}
 .swframe img{width:210px;border-radius:6px;border:1px solid #3a3a36;background:#000;display:block}
 .aim{display:flex;gap:6px;flex-wrap:wrap;align-items:center}
 .panbtn{background:#1d1d1d;border:1px solid #3a3a36;border-radius:8px;color:#c8c8c0;
         padding:9px 13px;font-size:14px;cursor:pointer;font-variant-numeric:tabular-nums}
 .panbtn:hover{border-color:#A1CC48;color:#e8e8e4}
 .panbtn.on{background:#20261a;border-color:#A1CC48;color:#A1CC48;font-weight:700}
 .panbtn .sc{font-size:11px;color:#7a7a72;display:block;text-align:center}
 .panbtn.on .sc{color:#A1CC48}
 .panbtn.next{border-style:dashed}
 .collect{font-size:13px;color:#d8b24a;border:1px dashed #6a5a2a;border-radius:8px;
          padding:8px 12px;margin-bottom:10px}
 .collect b{color:#e8e8e4}
 .spin{display:inline-block;width:8px;height:8px;border-radius:50%;background:#d8b24a;
       margin-right:8px;animation:pulse 1.1s infinite}
 @keyframes pulse{0%,100%{opacity:.25}50%{opacity:1}}
 .gate{font-size:12px;color:#d8b24a;margin-top:6px}
 .heard{font-size:13px;color:#9a9a90;margin-top:8px;min-height:18px}
 .heard b{color:#A1CC48;font-weight:600}
 .heard .none{color:#6a6a62}
 .botnow{font-size:26px;color:#A1CC48;font-weight:700;margin-bottom:4px}
 .botsub{font-size:13px;color:#9a9a90;margin-bottom:14px}
 .brow{display:flex;align-items:center;gap:10px;padding:7px 10px;border-radius:8px;
       border:1px solid #2c2c28;margin-bottom:5px;font-size:14px;color:#9a9a90}
 .brow.on{border-color:#A1CC48;color:#e8e8e4;background:#20261a}
 .brow .bname{width:120px;font-weight:600}
 .brow .bnote{font-size:12px;color:#7a7a72}
 .swcap{font-size:11px;color:#9a9a90;margin-top:3px;text-align:center;font-variant-numeric:tabular-nums}
</style></head><body>
<div class=tabs>
  <div class="tab active" id=tabLive onclick="showTab('live')">LIVE</div>
  <div class="tab" id=tabFeed onclick="showTab('feed')">NOTICED <span class=badge id=fcount>0</span></div>
  <div class="tab" id=tabSweeps onclick="showTab('sweeps')">SWEEPS <span class=badge id=scount>0</span></div>
  <div class="tab" id=tabBot onclick="showTab('bot')">ROBOT <span class=badge id=bstate>—</span></div>
</div>

<div class="page show" id=pageLive>
 <div class=wrap>
  <div class=left>
    <h3>LIVE</h3>
    <img id=v src="/stream.mjpg">
    <h3>AIM — where it watches (developer)</h3>
    <div class=aim id=aim></div>
    <h3>USER — where the person is sitting</h3>
    <div class=aim id=userpan></div>
    <h3>DESCRIBE THE SCENE — it will re-plan</h3>
    <input id=c placeholder='e.g. "two of us are assembling a robot arm this afternoon"'>
    <div class=heard id=heard></div>
    <div class=namerow>
      <input id=nm placeholder="the name they gave it (ASCII, <=16)">
      <button class="ebtn go" onclick="setName()">name it &amp; say hello</button>
    </div>
    <h3>IF IT MISSES THE SCENE <span class=devonly>researcher only</span></h3>
    <div class=namerow>
      <button class="ebtn force" onclick="force()">notice this NOW</button>
      <button class=ebtn onclick="resweep()">look around again</button>
    </div>
    <div class=heard id=emerg></div>
  </div>
  <div class=right>
    <h3>SCENE → PLAN — what the VLM saw &amp; how it tiered it</h3>
    <div class="plan rel" id=rel></div>
    <h3>THE PLAN — what it watches for, lit as it happens</h3>
    <div class=plan id=plan></div>
  </div>
 </div>
</div>

<div class=page id=pageFeed>
 <div class=feedwrap>
   <h3>NOTICED — the feed</h3>
   <div id=collecting></div>
   <div class=grid id=feed></div>
 </div>
</div>

<div class=page id=pageBot>
 <div class=feedwrap>
   <h3>ROBOT — the eight-state machine (monitoring only)</h3>
   <div class=botnow id=botnow></div>
   <div class=plan id=bot></div>
 </div>
</div>

<div class=page id=pageSweeps>
 <div class=feedwrap>
   <h3>SWEEPS — full-sweep datasets (panorama + tier-boxed frames)</h3>
   <div id=sweeps></div>
 </div>
</div>

<script>
const REL = __REL_NAMES__;            // {id: "name"}
function relChip(id, on){
  const name = REL[id] || ('rel'+id);
  return `<span class="rel ${on?'on':''}"><span class=rid>${id}</span>${name}</span>`;
}
function op(word, lit){ return `<span class="op ${lit?'lit':''}">${word}</span>`; }
// Build the lit-up logic line for one watch entry from its operator id-groups + on-map.
function compose(s){
  const on = id => !!s.on[String(id)];
  let parts = [];
  if(s.then && s.then.length){                       // ordered sequence: a THEN b THEN c
    const allOn = s.then.every(on);
    s.then.forEach((id,i)=>{ if(i) parts.push(op('THEN', allOn)); parts.push(relChip(id, on(id))); });
  } else {
    if(s.all && s.all.length){
      const allOn = s.all.every(on);
      s.all.forEach((id,i)=>{ if(i) parts.push(op('AND', allOn)); parts.push(relChip(id, on(id))); });
    }
    if(s.any && s.any.length){
      const anyOn = s.any.some(on);
      if(parts.length) parts.push(op('AND', allLit(s)));
      parts.push(op('ANY', anyOn)); parts.push(`<span class="op paren">(</span>`);
      s.any.forEach((id,i)=>{ if(i) parts.push(op('OR', anyOn)); parts.push(relChip(id, on(id))); });
      parts.push(`<span class="op paren">)</span>`);
    }
    if(s.not && s.not.length){
      s.not.forEach(id=>{ parts.push(op('NOT', !on(id))); parts.push(relChip(id, on(id))); });
    }
  }
  if(!parts.length) parts.push('<span class=detail>(no relations)</span>');
  // WHAT IT IS BOUND TO. Without this the panel reads "hands-on" and says nothing
  // about which object has to be touched -- which is the whole of the entry's
  // meaning, and the difference between a card that can fire and one the focus
  // gate will hold back on every frame.
  if(s.onobj) parts.push(`<span class="op onobj">on ${s.onobj}</span>`);
  else if((s.all||[]).some(i=>[1,4,7,8,9,11].includes(i)))
    parts.push(`<span class="op onobj none">any focus object</span>`);
  return `<div class=logic>${parts.join('')}</div>`;
}
function allLit(s){ return (s.all||[]).every(id=>!!s.on[String(id)]); }
function caption(lbl){                                // turn "single:3" into a readable name
  const m = /^single:(\\d+)$/.exec(lbl||'');
  return m ? (REL[m[1]]||('rel'+m[1]))+' alone is worth recording' : (lbl||'');
}
function renderRel(p){
  const focus=new Set(p.focus||[]), detect=new Set(p.detect||[]);
  const all=[...new Set([...(p.seen||[]),...(p.detect||[]),...(p.focus||[])])];
  if(!all.length) return '<div class=detail>no scene analysis yet — describe the scene to plan</div>';
  const chips=all.map(o=>{
    const cls = focus.has(o)?'focus':(detect.has(o)?'context':'ignore');
    return `<span class="tier ${cls}">${o}</span>`;
  }).join('');
  return `<div class=tierrow>${chips}</div>
    <div class=tierkey>
      <span class="tier focus">focus · can trigger</span>
      <span class="tier context">context · enriches</span>
      <span class="tier ignore">seen · ignored</span></div>`+
    ((p.suppressed||[]).length
      ? `<div class=gate>focus gate held back: ${p.suppressed.join(', ')}</div>` : '');
}

// ---- THE TWO EMERGENCY CONTROLS --------------------------------------------
// A session is one shot. When the actor plays the scene and the CV does not
// fire, the choice is between a void session and a researcher taking over, and
// the second is worth having. `force` skips the trigger AND the confirmation
// judge and goes straight to the performance and the story; `resweep` is the
// nine-minute self-directed sweep, on demand.
async function say(m){const el=document.getElementById('emerg');
  el.textContent=m; setTimeout(()=>{el.textContent='';},4000);}
async function force(){
  await fetch('/finding',{method:'POST'});
  say('forced -- it will notice, then collect the story');
}
async function resweep(){
  await fetch('/resweep',{method:'POST'});
  say('re-sweeping on the request already on record');
}

async function setName(){
  const v=document.getElementById('nm').value.trim();
  if(!v) return;
  await fetch('/name',{method:'POST',body:v});
}
async function poll(){
  try{
    let p=await (await fetch('/plan.json')).json();
    document.getElementById('rel').innerHTML = renderRel(p);
    document.getElementById('plan').innerHTML =
      `<div class=ctx>${p.context||'(no context)'}</div>`+
      (p.why?`<div class=why>why: ${p.why}</div>`:'')+
      (p.status||[]).map(s=>{
        const j=(p.judgments||{})[s.label];
        const left=Math.max(0,Math.ceil(s.cooldown_remaining_s||0));
        const state = j&&j.status==='judging'?'<span class="estate judging">VLM judging</span>'
                     :j&&j.status==='candidate'?'<span class="estate sat">CV candidate</span>'
                     :s.cool&&j&&j.status==='grouped'?`<span class="estate grouped">confirmed · grouped · cooldown ${left}s</span>`
                     :s.cool&&j&&j.status==='suppressed'?`<span class="estate suppressed">suppressed · cooldown ${left}s</span>`
                     :s.cool&&j&&j.status==='confirmed'?`<span class="estate confirmed">confirmed · cooldown ${left}s</span>`
                     :s.cool&&j&&j.status==='rejected'?`<span class="estate rejected">rejected · cooldown ${left}s</span>`
                     :s.cool?`<span class="estate cool">cooldown ${left}s</span>`
                     :s.sat?'<span class="estate sat">held</span>'
                     :'<span class=estate>watching</span>';
        return `<div class=entry>
          <div class=ehead><span class="dot ${s.sat?'sat':(s.cool?'cool':'')}"></span>
            ${state}<span class=ecap>${caption(s.label)}</span></div>
          ${compose(s)}${j&&j.note?`<div class=detail>${j.note}</div>`:''}</div>`;
      }).join('');
    const nm=document.getElementById('nm');
    if(document.activeElement!==nm && p.bot_name!==undefined && !nm.value) nm.value=p.bot_name;
    document.getElementById('aim').innerHTML = renderAim(p);
    document.getElementById('userpan').innerHTML = renderUserPan(p);
    const h=document.getElementById('heard');
    h.innerHTML = p.transcript ? `heard: <b>${p.transcript}</b>`
                              : '<span class=none>nothing heard yet</span>';
    const st=p.states||[];
    const cur=st.find(x=>x.on);
    document.getElementById('bstate').textContent = cur?cur.name.split('_')[0]:'—';
    if(document.getElementById('pageBot').classList.contains('show')){
      document.getElementById('botnow').textContent = cur?cur.name:'—';
      document.getElementById('bot').innerHTML = st.map(x=>`<div class="brow ${x.on?'on':''}">
        <span class=bname>${x.name}</span><span class=bscreen>${x.screen||''}</span>
        <span class=bnote>${x.note||''}</span></div>`).join('');
    }
    let f=await (await fetch('/feed.json')).json();
    const col=p.collecting||[];
    document.getElementById('fcount').textContent = f.length + (col.length?'+'+col.length:'');
    document.getElementById('collecting').innerHTML = col.length
      ? col.map(c=>`<div class=collect><span class=spin></span>collecting a story:
          <b>${c.label}</b> — ${c.panels}/${c.max} panels</div>`).join('')
      : '';
    document.getElementById('feed').innerHTML = f.length ? f.map(m=>`<div class=card>
      <a href="/frame/${m.frame||''}" target="_blank" title="open the full story strip">
        <img src="/thumb/${m.thumb}"></a>
      <div><div class=note>${m.note||m.label||''}</div>
      <div class=meta>${m.label||''} · ${m.time} · <a href="/frame/${m.frame||''}"
        target="_blank" style="color:#00d0d0">full strip ↗</a></div>
      ${m.request?`<div class=meta style="opacity:.55">for: ${m.request}</div>`:''}</div>
    </div>`).join('') : '<div class=empty>nothing noticed yet</div>';
    let sw=await (await fetch('/sweeps.json')).json();
    document.getElementById('scount').textContent = sw.length;
    if(document.getElementById('pageSweeps').classList.contains('show')) renderSweeps(sw);
  }catch(e){}
}
function aimSet(deg){
  fetch('/pan',{method:'POST',body:''+deg});
}
function renderAim(p){
  // The stations come from the sweep, so the buttons are the angles the robot
  // actually looked at -- not a fixed row that might not correspond to anything.
  const sc=p.pan_scores||[], now=p.pan_now;
  const stations = sc.length ? sc : [{pan:60,score:null},{pan:30,score:null},
                                     {pan:0,score:null},{pan:-30,score:null},
                                     {pan:-60,score:null}];
  const btns = stations.map(st=>{
    const on = (now!=null && Math.abs(st.pan-now)<8);
    return `<button class="panbtn ${on?'on':''}" onclick="aimSet(${st.pan})">
      ${st.pan>0?'+':''}${st.pan}°<span class=sc>${st.score==null?'—':st.score}</span></button>`;
  }).join('');
  return btns + `<button class="panbtn next" onclick="aimSet('next')"
      title="the next-best angle the sweep scored">next best ▸</button>`;
}
function userPanSet(deg){
  fetch('/user_pan',{method:'POST',body:''+deg});
}
function renderUserPan(p){
  // Lock this once, after the participant has chosen where to put the robot.
  // Only S7 uses it: it is the only gesture that names two places -- "you" and
  // "that" -- so it is the only one that has to be told where they are.
  const now = p.user_pan;
  // The row stops where the BODY stops. Pan reaches about -68..+70 on this
  // mount, so there are no seats at -90 or +120 to offer: both would clamp to
  // the same edge this row already ends on, and the button would be a label for
  // a bearing the robot cannot hold. Worse than useless -- S7 computes its
  // crossing FROM this number, so an unreachable seat rescales the whole
  // gesture and moves the object leg too.
  //
  // Reach is read from the live calibration, so re-jogging the pan horn moves
  // these buttons instead of quietly invalidating them.
  const lo = (p.pan_reach && p.pan_reach[0] != null) ? p.pan_reach[0] : -67;
  const hi = (p.pan_reach && p.pan_reach[1] != null) ? p.pan_reach[1] :  69;
  const inner = [-60,-45,-30,-15,0,15,30,45,60].filter(d => d > lo && d < hi);
  const seats = [Math.ceil(lo), ...inner, Math.floor(hi)];
  const btns = seats.map(d=>{
    const on = (now!=null && Math.abs(d-now)<1.5);
    const edge = (d<=Math.ceil(lo) || d>=Math.floor(hi));
    return `<button class="panbtn ${on?'on':''}" onclick="userPanSet(${d})"
      title="${edge?'as far as the body turns — there is no facing past this'
                  :'seat at '+d+'°'}">
      ${d>0?'+':''}${d}°${edge?'▐':''}</button>`;
  }).join('');
  const state = now==null
    ? `<span class=sc>not set — S7 uses the authored +60, S6 faces −30</span>`
    : `<span class=sc>locked at ${now>0?'+':''}${now}°</span>`;
  return btns + `<button class="panbtn next" onclick="userPanSet('clear')"
      title="back to the authored template">clear</button> ` + state;
}
function panTag(p){ return (p==null?'—':(p>0?('+'+p):(''+p))+'°'); }
function renderSweeps(list){
  const el=document.getElementById('sweeps');
  if(!list.length){ el.innerHTML='<div class=empty>no sweeps yet — delegate with --sweep-capture</div>'; return; }
  el.innerHTML = list.map(s=>{
    const foc=new Set(s.focus||[]);
    const cov=s.coverage||{};
    const chips=(s.detect||[]).map(o=>{
      const n=(o in cov)?cov[o]:'?';
      const cls=(foc.has(o)?'focus':'context')+((cov[o]===0)?' nofind':'');
      return `<span class="tier ${cls}">${o}·${n}</span>`;
    }).join('');
    const pano = s.panorama ? `<img class=pano src="/sweepimg/${s.ts}/${s.panorama}">` : '';
    const frames=(s.shots||[]).map(sh=>`<div class=swframe>
        <img loading=lazy src="/sweepimg/${s.ts}/${sh.file}">
        <div class=swcap>${panTag(sh.pan)} · ${sh.n} box</div></div>`).join('');
    return `<div class=swcard>
      <div class=swhead><b>${s.ts}</b> &nbsp; ${s.context||''} &nbsp; · richest ${panTag(s.richest_pan)}</div>
      <div class=tierrow>${chips}</div>
      ${pano}
      <div class=swrow>${frames}</div>
    </div>`;
  }).join('');
}
function showTab(which){
  document.getElementById('pageLive').classList.toggle('show', which==='live');
  document.getElementById('pageFeed').classList.toggle('show', which==='feed');
  document.getElementById('pageSweeps').classList.toggle('show', which==='sweeps');
  document.getElementById('pageBot').classList.toggle('show', which==='bot');
  document.getElementById('tabLive').classList.toggle('active', which==='live');
  document.getElementById('tabFeed').classList.toggle('active', which==='feed');
  document.getElementById('tabSweeps').classList.toggle('active', which==='sweeps');
  document.getElementById('tabBot').classList.toggle('active', which==='bot');
  if(which==='sweeps'||which==='bot') poll();
}
document.getElementById('c').addEventListener('keydown',async e=>{
  if(e.key==='Enter'&&e.target.value.trim()){
    await fetch('/context',{method:'POST',body:e.target.value});
    e.target.value=''; setTimeout(poll,600);
  }
});
setInterval(poll,1200); poll();
</script></body></html>""".replace("__REL_NAMES__", json.dumps(REL_NAMES))


class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_GET(self):
        p = self.path.split("?")[0]
        if p == "/":
            self._send(200, "text/html", PAGE.encode())
        elif p == "/stream.mjpg":
            self.send_response(200)
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=frame")
            self.end_headers()
            try:
                while True:
                    jpg = STATE["jpg"]
                    if jpg:
                        self.wfile.write(b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + jpg + b"\r\n")
                    time.sleep(0.05)
            except Exception:
                pass
        elif p == "/plan.json":
            with LOCK:
                data = {"context": STATE["context"], "why": STATE["why"],
                        "entries": STATE["entries"], "status": STATE["status"],
                        "judgments": STATE["judgments"],
                        "bot_name": STATE["bot_name"],
                        "seen": STATE["seen"], "detect": STATE["detect"],
                        "focus": STATE["focus"], "transcript": STATE["transcript"],
                        "states": STATE["states"],
                        "suppressed": STATE["suppressed"],
                        "collecting": STATE["collecting"],
                        "pan_now": STATE["pan_now"], "pan_scores": STATE["pan_scores"],
                        "user_pan": STATE["user_pan"],
                        # Sent, not hard-coded in the page, so re-jogging the pan
                        # horn moves the seat buttons with it.
                        "pan_reach": _pan_reach()}
            self._send(200, "application/json", json.dumps(data).encode())
        elif p == "/feed.json":
            # READ THE LOG, NOT THE MEMORY. attention_log.jsonl is the durable
            # record of this run: append-only, written by the single writer in
            # session/feed.py at the same moment the jpgs land. Serving it
            # directly means the page shows what actually happened rather than
            # what this process still happens to be holding -- so the record
            # survives a reload, a browser opened late, and a laptop-side
            # restart mid-session, none of which the participant caused and all
            # of which used to present as "nothing noticed yet".
            #
            # The in-memory list stays as the fallback for a run with --save
            # off, which writes no log at all.
            #
            # Images already resolve from disk when they are not in memory (see
            # /thumb/ and /frame/ below), so a card served from the log is never
            # a broken one.
            data, fn = [], os.path.join(feed_dir(), "attention_log.jsonl")
            try:
                with open(fn) as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            data.append(json.loads(line))
                        except ValueError:
                            # A torn final line -- the writer was mid-append.
                            # Dropping it costs one card for one poll; refusing
                            # the whole response would blank the feed instead.
                            continue
            except OSError:
                data = []
            if not data:
                with LOCK:
                    data = list(STATE["feed"])
            self._send(200, "application/json",
                       json.dumps(list(reversed(data))[:200]).encode())
        elif p == "/sweeps.json":
            base = os.path.join(feed_dir(), "sweeps")
            out = []
            if os.path.isdir(base):
                for ts in sorted(os.listdir(base), reverse=True):
                    d = os.path.join(base, ts)
                    pj = os.path.join(d, "plan.json")
                    if not os.path.isfile(pj):
                        continue
                    try:
                        meta = json.load(open(pj))
                    except Exception:
                        continue
                    pano = meta.get("panorama") or "panorama.jpg"
                    out.append({"ts": ts, "context": meta.get("context", ""),
                                "detect": meta.get("detect") or [], "focus": meta.get("focus") or [],
                                "coverage": meta.get("coverage") or {},
                                "richest_pan": meta.get("richest_pan"),
                                "panorama": pano if os.path.isfile(os.path.join(d, pano)) else None,
                                "shots": [{"pan": s.get("pan"), "file": s.get("file"),
                                           "n": len(s.get("dets") or [])} for s in (meta.get("shots") or [])]})
                    if len(out) >= 50:
                        break
            self._send(200, "application/json", json.dumps(out).encode())
        elif p.startswith("/sweepimg/"):
            parts = p[len("/sweepimg/"):].split("/")
            base = os.path.join(feed_dir(), "sweeps")
            if len(parts) == 2:
                full = os.path.join(base, os.path.basename(parts[0]), os.path.basename(parts[1]))
                if os.path.isfile(full):
                    self._send(200, "image/jpeg", open(full, "rb").read())
                else:
                    self._send(404, "text/plain", b"")
            else:
                self._send(404, "text/plain", b"")
        elif p.startswith("/frame/"):
            name = os.path.basename(p[7:])
            mem = STATE.get("frames", {}).get(name)
            if mem is not None:
                self._send(200, "image/jpeg", mem)
            else:
                fn = os.path.join(feed_dir(), name)
                if os.path.exists(fn):
                    self._send(200, "image/jpeg", open(fn, "rb").read())
                else:
                    self._send(404, "text/plain", b"")
        elif p.startswith("/thumb/"):
            name = os.path.basename(p[7:])
            mem = STATE["thumbs"].get(name)
            if mem is not None:
                self._send(200, "image/jpeg", mem)
            else:
                fn = os.path.join(feed_dir(), name)
                if os.path.exists(fn):
                    self._send(200, "image/jpeg", open(fn, "rb").read())
                else:
                    self._send(404, "text/plain", b"")
        else:
            self._send(404, "text/plain", b"")

    def do_POST(self):
        if self.path == "/pan":
            n = int(self.headers.get("Content-Length", 0))
            val = self.rfile.read(n).decode("utf-8", "ignore").strip()
            with LOCK:
                STATE["pending_pan"] = val      # "next" or a number, in degrees
            self._send(200, "application/json", b'{"ok": true}')
        elif self.path == "/user_pan":
            raw = self.rfile.read(int(self.headers.get("Content-Length", 0))
                                  ).decode("utf-8", "ignore").strip()
            with LOCK:
                val = None if raw in ("", "none", "clear") else float(raw)
                STATE["user_pan"] = STATE["pending_user_pan"] = val
            self._send(200, "application/json", b'{"ok": true}')
        elif self.path == "/name":
            n = int(self.headers.get("Content-Length", 0))
            val = self.rfile.read(n).decode("utf-8", "ignore").strip()[:16]
            with LOCK:
                STATE["bot_name"] = val
                STATE["pending_name"] = val      # the loop sends it and greets
            self._send(200, "application/json", b'{"ok": true}')
        elif self.path in ("/finding", "/resweep"):
            # ONE-SHOT FLAGS, not values: the loop reads and clears them on its
            # next pass. A second click before the drain is the same click, which
            # is the right behaviour for a button somebody presses twice because
            # the room did not visibly react within a frame.
            with LOCK:
                STATE["pending_finding" if self.path == "/finding"
                      else "pending_resweep"] = True
            self._send(200, "application/json", b'{"ok": true}')
        elif self.path == "/context":
            n = int(self.headers.get("Content-Length", 0))
            sentence = self.rfile.read(n).decode("utf-8", "ignore").strip()
            with LOCK:
                STATE["pending_context"] = sentence
            self._send(200, "application/json", b'{"ok": true}')
        else:
            self._send(404, "text/plain", b"")

    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass


def serve(args):
    global ARGS
    ARGS = args
    srv = ThreadingHTTPServer(("", args.web_port), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    print(f"open  http://localhost:{args.web_port}   (live + plan + feed + context box)")
    import sys
    return sys.modules[__name__]
