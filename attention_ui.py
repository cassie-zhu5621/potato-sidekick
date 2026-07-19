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

STATE = {"jpg": None, "feed": [], "thumbs": {}, "frames": {},
         "context": "", "why": "", "entries": [],      # [(expr, label)]
         "status": [],                                  # [dict per entry: see build_status]
         "seen": [], "detect": [], "focus": [],         # relevance layer: enumerate -> tier
         "pending_context": None}
LOCK = threading.Lock()
ARGS = None

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
            "all": list(e["all"]), "any": list(e["any"]),
            "not": list(e["not"]), "then": list(e["then"]),
            "on": {str(r): bool(truth.get(r, False)) for r in ids},
        })
    return out

PAGE = """<!doctype html><html><head><meta charset=utf-8><title>attention system</title>
<style>
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
 .swcap{font-size:11px;color:#9a9a90;margin-top:3px;text-align:center;font-variant-numeric:tabular-nums}
</style></head><body>
<div class=tabs>
  <div class="tab active" id=tabLive onclick="showTab('live')">LIVE</div>
  <div class="tab" id=tabFeed onclick="showTab('feed')">NOTICED <span class=badge id=fcount>0</span></div>
  <div class="tab" id=tabSweeps onclick="showTab('sweeps')">SWEEPS <span class=badge id=scount>0</span></div>
</div>

<div class="page show" id=pageLive>
 <div class=wrap>
  <div class=left>
    <h3>LIVE</h3>
    <img id=v src="/stream.mjpg">
    <h3>DESCRIBE THE SCENE — it will re-plan</h3>
    <input id=c placeholder='e.g. "two of us are assembling a robot arm this afternoon"'>
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
   <div class=grid id=feed></div>
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
      <span class="tier ignore">seen · ignored</span></div>`;
}
async function poll(){
  try{
    let p=await (await fetch('/plan.json')).json();
    document.getElementById('rel').innerHTML = renderRel(p);
    document.getElementById('plan').innerHTML =
      `<div class=ctx>${p.context||'(no context)'}</div>`+
      (p.why?`<div class=why>why: ${p.why}</div>`:'')+
      (p.status||[]).map(s=>{
        const state = s.sat?'<span class="estate sat">satisfied</span>'
                     :(s.cool?'<span class="estate cool">cooling</span>':'<span class=estate>watching</span>');
        return `<div class=entry>
          <div class=ehead><span class="dot ${s.sat?'sat':(s.cool?'cool':'')}"></span>
            ${state}<span class=ecap>${caption(s.label)}</span></div>
          ${compose(s)}</div>`;
      }).join('');
    let f=await (await fetch('/feed.json')).json();
    document.getElementById('fcount').textContent = f.length;
    document.getElementById('feed').innerHTML = f.length ? f.map(m=>`<div class=card>
      <a href="/frame/${m.frame||''}" target="_blank" title="open the full story strip">
        <img src="/thumb/${m.thumb}"></a>
      <div><div class=note>${m.note||m.label||''}</div>
      <div class=meta>${m.label||''} · ${m.time} · <a href="/frame/${m.frame||''}"
        target="_blank" style="color:#00d0d0">full strip ↗</a></div></div>
    </div>`).join('') : '<div class=empty>nothing noticed yet</div>';
    let sw=await (await fetch('/sweeps.json')).json();
    document.getElementById('scount').textContent = sw.length;
    if(document.getElementById('pageSweeps').classList.contains('show')) renderSweeps(sw);
  }catch(e){}
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
  document.getElementById('tabLive').classList.toggle('active', which==='live');
  document.getElementById('tabFeed').classList.toggle('active', which==='feed');
  document.getElementById('tabSweeps').classList.toggle('active', which==='sweeps');
  if(which==='sweeps') poll();
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
                        "seen": STATE["seen"], "detect": STATE["detect"], "focus": STATE["focus"]}
            self._send(200, "application/json", json.dumps(data).encode())
        elif p == "/feed.json":
            with LOCK:
                data = list(reversed(STATE["feed"]))
            self._send(200, "application/json", json.dumps(data).encode())
        elif p == "/sweeps.json":
            base = os.path.join(ARGS.feed_dir, "sweeps") if ARGS else "sweeps"
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
            base = os.path.join(ARGS.feed_dir, "sweeps") if ARGS else "sweeps"
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
                fn = os.path.join(ARGS.feed_dir, name) if ARGS else name
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
                fn = os.path.join(ARGS.feed_dir, name) if ARGS else name
                if os.path.exists(fn):
                    self._send(200, "image/jpeg", open(fn, "rb").read())
                else:
                    self._send(404, "text/plain", b"")
        else:
            self._send(404, "text/plain", b"")

    def do_POST(self):
        if self.path == "/context":
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
