"""The review page a participant reads in protocol §6 -- one HTML file per session.

Called two ways, and the second is why this lives here rather than in tools/:

  * `tools/build_review.py` on the command line, for rebuilding after the fact;
  * `session.feed.publish`, on every report as it is written.

The second keeps the page current without anyone remembering to run anything.
§6 follows the work phase immediately, and a review page that is one report stale
is worse than none -- the participant is asked about a report that is not on the
screen. `feed.publish` is the single writer for the record, so it is the only
place that can promise the page and the log agree.

WHAT IS DELIBERATELY NOT ON THE PAGE. Every field the system used to decide:
`judge_agreed`, the truth vector, relation ids and names, `worth`,
`plan_generation`, the card label. The participant is being asked whether the
robot noticed the right thing; a page that also shows them the machinery invites
them to grade the machinery instead, and RQ4 is about reading the ROBOT, not
reading its log. What remains is what the robot actually told them: when, in what
words, and the pictures it kept.

The one exception is the request, shown large at the top of each group. It is
theirs -- they said it -- and "did this match what you asked for" cannot be put
without it in view.

NO LINKS BETWEEN SESSIONS. A review page is opened on a machine in front of a
participant, and one stray click on a navigation bar would put another
participant's session and spoken requests on the screen. The index is a separate
file that is never linked FROM a review page.
"""
from __future__ import annotations

import glob
import html
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FEED = os.path.join(ROOT, "session_feed")


def _records(session_dir):
    path = os.path.join(session_dir, "attention_log.jsonl")
    if not os.path.exists(path):
        return []
    out = []
    for line in open(path):
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue      # a run killed mid-write leaves half a line; skip it
    return out


def _panels(rec, session_dir):
    """-> (filename, panel count). `shots` is authoritative; the image is
    measured only for records written before that field existed."""
    frame = rec.get("frame") or ""
    n = int(rec.get("shots") or 0)
    if n <= 0:
        try:
            from PIL import Image
            with Image.open(os.path.join(session_dir, frame)) as im:
                w, h = im.size
            n = max(1, round(w / h / (16 / 9)))
        except Exception:
            n = 1
    return frame, max(1, n)


def _pretty(name):
    return f"{name[4:8]}-{name[8:10]}-{name[10:12]} {name[13:15]}:{name[15:17]}" \
        if len(name) > 16 else name


CSS = """
:root { color-scheme: light dark;
  --ink:#141414; --dim:#6a6a6a; --line:#e0ddd6; --bg:#faf9f6; --card:#fff;
  --pick:#b5561f; }
@media (prefers-color-scheme: dark) {
  :root { --ink:#eceae5; --dim:#9a968d; --line:#33322e; --bg:#161513; --card:#1e1d1a;
          --pick:#e08a52; } }
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--ink);
  font:16px/1.6 ui-sans-serif,-apple-system,"Hiragino Sans","Noto Sans JP",sans-serif; }
.wrap { max-width:1180px; margin:0 auto; padding:48px 28px 140px; }
h1 { font-size:26px; font-weight:650; margin:0 0 4px; letter-spacing:-.01em; }
.sub { color:var(--dim); font-size:15px; margin-bottom:44px; }
.ask { margin:52px 0 22px; padding:20px 24px; background:var(--card);
  border:1px solid var(--line); border-radius:14px; }
.ask .lab { font-size:12px; letter-spacing:.09em; text-transform:uppercase;
  color:var(--dim); margin-bottom:8px; }
.ask .txt { font-size:23px; font-weight:600; line-height:1.4; }
.card { background:var(--card); border:1px solid var(--line); border-radius:16px;
  padding:26px 28px 20px; margin-bottom:22px; }
.when { font-size:13px; letter-spacing:.09em; text-transform:uppercase;
  color:var(--dim); margin-bottom:12px; }
/* The report that arrived while they were walking over. Fades rather than stays:
   it answers "which one is new" and then stops being a label on the data. */
@keyframes settle { from { border-color:var(--pick); box-shadow:0 0 0 4px rgba(181,86,31,.14); }
                    to   { border-color:var(--line); box-shadow:none; } }
.card.fresh { animation:settle 3.2s ease-out forwards; }
.note { font-size:30px; line-height:1.38; font-weight:500; letter-spacing:-.012em;
  margin-bottom:22px; }

/* ONE CONTINUOUS STRIP. Laid flat to the page width a five-panel strip is 124px
   tall and unreadable, so it keeps a fixed height and scrolls sideways -- which
   is also how a comic strip is actually read. */
.reel { overflow-x:auto; overflow-y:hidden; border:1px solid var(--line);
  border-radius:10px; background:#0d0d0c; cursor:zoom-in; }
.reel img { display:block; height:300px; width:auto; max-width:none; }
.hint { font-size:12px; color:var(--dim); margin:7px 2px 0; }

.rate { display:flex; align-items:center; gap:18px; flex-wrap:wrap;
  margin-top:18px; padding-top:16px; border-top:1px solid var(--line); }
.rate .q { font-size:15px; color:var(--dim); }
.dots { display:flex; gap:7px; }
.dot { width:30px; height:30px; border-radius:50%; border:1.5px solid var(--line);
  background:transparent; cursor:pointer; font-size:13px; color:var(--dim);
  display:flex; align-items:center; justify-content:center; padding:0; }
.dot:hover { border-color:var(--ink); }
.dot.on { background:var(--pick); border-color:var(--pick); color:#fff; }
.keep { margin-left:auto; display:flex; align-items:center; gap:9px;
  font-size:15px; cursor:pointer; user-select:none; }
.keep input { width:19px; height:19px; accent-color:var(--pick); }

.bar { position:fixed; left:0; right:0; bottom:0; background:var(--card);
  border-top:1px solid var(--line); padding:12px 28px;
  display:flex; align-items:center; gap:16px; font-size:14px; z-index:20; }
.bar button { font:inherit; padding:8px 16px; border-radius:9px;
  border:1px solid var(--line); background:transparent; color:var(--ink);
  cursor:pointer; }
.bar button:hover { border-color:var(--ink); }
.bar .n { color:var(--dim); margin-left:auto; }

#lb { position:fixed; inset:0; background:rgba(10,10,10,.95); display:none;
  z-index:50; cursor:zoom-out; overflow:auto; }
#lb.on { display:flex; align-items:center; }
#lb img { height:86vh; width:auto; max-width:none; margin:auto; display:block; }

table { border-collapse:collapse; width:100%; font-size:15px; }
td,th { text-align:left; padding:11px 14px; border-bottom:1px solid var(--line); }
th { font-size:12px; letter-spacing:.08em; text-transform:uppercase; color:var(--dim); }
a { color:inherit; }
"""

JS = """
const KEY = 'review:' + SESSION + ':';
function load(i,k){ return localStorage.getItem(KEY+i+':'+k); }
function save(i,k,v){ localStorage.setItem(KEY+i+':'+k, v); count(); }

document.querySelectorAll('.dots').forEach(row=>{
  const i = row.dataset.i, cur = load(i,'rate');
  [...row.children].forEach(b=>{
    if (b.dataset.v === cur) b.classList.add('on');
    b.addEventListener('click', ()=>{
      const off = b.classList.contains('on');
      [...row.children].forEach(x=>x.classList.remove('on'));
      if (off) { localStorage.removeItem(KEY+i+':rate'); count(); }
      else { b.classList.add('on'); save(i,'rate',b.dataset.v); }
    });
  });
});
document.querySelectorAll('.keep input').forEach(cb=>{
  const i = cb.dataset.i;
  cb.checked = load(i,'keep') === '1';
  cb.addEventListener('change', ()=>save(i,'keep', cb.checked?'1':'0'));
});

function count(){
  let n=0; for (let i=0;i<ROWS.length;i++) if (load(i,'rate')||load(i,'keep')) n++;
  document.getElementById('n').textContent = n+' / '+ROWS.length+' answered';
}
count();

function csv(){
  const esc = s => '"'+String(s==null?'':s).replace(/"/g,'""')+'"';
  const out = [['session','time','request','report','rating','keep'].join(',')];
  ROWS.forEach((r,i)=> out.push([SESSION, r.t, r.q, r.n,
      load(i,'rate')||'', load(i,'keep')==='1'?'keep':(load(i,'keep')==='0'?'drop':'')
    ].map(esc).join(',')));
  return out.join('\\n');
}
document.getElementById('dl').addEventListener('click', ()=>{
  const b = new Blob([csv()], {type:'text/csv'});
  const a = document.createElement('a');
  a.href = URL.createObjectURL(b); a.download = SESSION+'_review.csv'; a.click();
});
document.getElementById('cp').addEventListener('click', async ()=>{
  try { await navigator.clipboard.writeText(csv());
        document.getElementById('cp').textContent='Copied';
        setTimeout(()=>document.getElementById('cp').textContent='Copy CSV',1400); }
  catch(e){ window.prompt('Copy:', csv()); }
});

// ---- THE PAGE FOLLOWS THE FEED --------------------------------------------
// `feed.publish` rewrites this file on every report, but a tablet propped beside
// the robot is showing the copy it loaded. The summons is the moment the person
// walks over to look; arriving at a page that does not yet contain the thing
// they were called for is worse than not being called.
//
// HEAD on the log rather than a timer on the page: a blind reload every few
// seconds would interrupt someone reading and rate-limit nothing. Content-Length
// changes on every appended line, so one cheap request answers "is there more".
//
// Only over http:// -- file:// forbids fetch, so a double-clicked page stays
// static and silently so. That is the researcher's own copy; the tablet is
// served, and that is the one this is for.
const SEEN='rv:'+SESSION+':n';
let sig=null;
async function poll(){
  try{
    const r=await fetch('attention_log.jsonl',{method:'HEAD',cache:'no-store'});
    const s=(r.headers.get('Last-Modified')||'')+'|'+(r.headers.get('Content-Length')||'');
    if(sig===null) sig=s;
    else if(s!==sig){ sessionStorage.setItem(SEEN, ROWS.length); location.reload(); }
  }catch(e){}
}
setInterval(poll,2000); poll();

// AND IT LANDS ON THE NEW ONE. Reloading to the top of a page of old reports
// makes the person hunt for what changed, which is the opposite of being shown
// something. Ratings survive: they are in localStorage, saved on click.
(function(){
  const seen=parseInt(sessionStorage.getItem(SEEN)||'0',10);
  if(seen>0 && ROWS.length>seen){
    const cards=document.querySelectorAll('.card');
    const last=cards[cards.length-1];
    if(last){ last.classList.add('fresh');
              last.scrollIntoView({behavior:'smooth',block:'center'}); }
  }
  sessionStorage.setItem(SEEN, ROWS.length);
})();

const lb=document.getElementById('lb'), lbi=lb.querySelector('img');
document.querySelectorAll('.reel').forEach(r=>r.addEventListener('click',()=>{
  lbi.src = r.querySelector('img').src; lb.classList.add('on'); lb.scrollLeft=0; }));
lb.addEventListener('click',()=>lb.classList.remove('on'));
addEventListener('keydown',e=>{ if(e.key==='Escape') lb.classList.remove('on'); });
"""


def build(session_dir, allow_empty=False) -> str | None:
    """-> the path written, or None when there is nothing to show.

    `allow_empty` writes the page anyway, saying so. That is for the START of a
    run: `write_latest` names the session the tablet should follow, and without a
    page to follow the tablet gets a 404 for the whole briefing phase -- which on
    2026-08-09 it then never recovered from. A session that has not noticed
    anything yet is a real state and deserves a page of its own.
    """
    recs = _records(session_dir)
    name = os.path.basename(session_dir)
    if not recs and not allow_empty:
        return None

    body = ['<h1>What the robot noticed</h1>',
            f'<div class="sub">{html.escape(_pretty(name))} &middot; '
            f'{len(recs)} report{"s" if len(recs) != 1 else ""}</div>']

    if not recs:
        body.append('<div class="none">It hasn\'t noticed anything yet.</div>')

    rows, last_req = [], object()
    for i, rec in enumerate(recs):
        req = rec.get("request") or ""
        note = str(rec.get("note") or "")
        when = str(rec.get("time") or "")
        rows.append({"t": when, "q": req, "n": note})

        if req != last_req:
            # A new brief starts a new group. Sessions carry more than one -- the
            # card brief, the participant's own, anything re-spoken after a
            # correction -- and a report only means something beside the words it
            # was answering.
            body.append('<div class="ask"><div class="lab">You asked</div>'
                        f'<div class="txt">{html.escape(req) or "&mdash;"}</div></div>')
            last_req = req

        frame, n = _panels(rec, session_dir)
        dots = "".join(
            f'<button class="dot" data-v="{v}">{v}</button>' for v in range(1, 6))
        body.append(
            '<div class="card">'
            f'<div class="when">{html.escape(when)}</div>'
            f'<div class="note">{html.escape(note)}</div>'
            f'<div class="reel"><img src="{html.escape(frame)}" alt=""></div>'
            f'<div class="hint">{n} frame{"s" if n != 1 else ""} '
            f'&middot; scroll sideways &middot; click to enlarge</div>'
            '<div class="rate">'
            '<span class="q">Worth telling you about?</span>'
            f'<div class="dots" data-i="{i}">{dots}</div>'
            f'<label class="keep"><input type="checkbox" data-i="{i}"> Keep</label>'
            '</div></div>')

    payload = json.dumps(rows, ensure_ascii=False)
    doc = (f'<!doctype html><meta charset="utf-8">'
           f'<meta name="viewport" content="width=device-width,initial-scale=1">'
           f'<title>What the robot noticed &middot; {html.escape(_pretty(name))}</title>'
           f'<style>{CSS}</style><div class="wrap">{"".join(body)}</div>'
           f'<div id="lb"><img alt=""></div>'
           f'<div class="bar"><button id="dl">Download CSV</button>'
           f'<button id="cp">Copy CSV</button><span class="n" id="n"></span></div>'
           f'<script>const SESSION={json.dumps(name)};const ROWS={payload};</script>'
           f'<script>{JS}</script>')

    out = os.path.join(session_dir, "review.html")
    with open(out, "w") as fh:
        fh.write(doc)
    return out


LATEST = """<!doctype html><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>What the robot noticed</title>
<style>html,body{margin:0;height:100%%;background:%s}
iframe{border:0;width:100%%;height:100%%;display:block}
#w{display:flex;height:100%%;align-items:center;justify-content:center;
   font:16px/1.6 ui-sans-serif,-apple-system,sans-serif;color:#8a867d}</style>
<div id="w">waiting for a session…</div>
<script>
// A FIXED ADDRESS FOR THE TABLET.
//
// The session folder is `e2e_<timestamp>`, so its URL does not exist until the
// run starts -- and it changes for every participant. Typing it into a tablet at
// the top of each session is one more thing to get wrong while somebody is
// waiting, and getting it wrong means the summons leads to the previous
// participant's reports.
//
// So the tablet is pointed here, once, and never touched again. `current.txt` is
// written by the loop at startup; this page follows it, including across a
// restart between participants.
// LATCH ONLY ON A PAGE THAT ACTUALLY LOADED. Observed 2026-08-09 20:32: the run
// started, current.txt changed, the iframe asked for a review.html that did not
// exist yet and got a 404 -- and because `now` had already been set to the new
// session, every later tick saw no change and did nothing. The page sat on the
// 404 for three minutes, through a report landing, until it was reloaded by
// hand. A poller that gives up after one try is a poller that only works when
// nothing goes wrong.
let now=null;
async function tick(){
  try{
    const r=await fetch('current.txt',{cache:'no-store'});
    if(!r.ok) return;
    const s=(await r.text()).trim();
    if(!s || s===now) return;
    const h=await fetch(s+'/review.html',{method:'HEAD',cache:'no-store'});
    if(!h.ok) return;                     // not written yet -- ask again next tick
    now=s;
    document.getElementById('w').style.display='none';
    let f=document.querySelector('iframe');
    if(!f){ f=document.createElement('iframe'); document.body.appendChild(f); }
    f.src=s+'/review.html?t='+Date.now();
  }catch(e){}
}
setInterval(tick,3000); tick();
</script>"""


def write_latest(session_dir) -> None:
    """Point the tablet at the run that is starting. Additive: writes two files
    beside the sessions and touches nothing inside them."""
    os.makedirs(FEED, exist_ok=True)
    # The page BEFORE the first report. `feed.publish` overwrites it on every
    # finding; this is only so the tablet has something to load from the moment
    # the run starts.
    try:
        build(session_dir, allow_empty=True)
    except Exception:
        pass
    with open(os.path.join(FEED, "current.txt"), "w") as fh:
        fh.write(os.path.basename(os.path.abspath(session_dir)))
    with open(os.path.join(FEED, "latest.html"), "w") as fh:
        fh.write(LATEST % "#faf9f6")


def build_index(dirs) -> str:
    """FOR THE RESEARCHER ONLY. Never linked from a review page -- see module
    docstring. Keep it off the machine the participant sits at."""
    rows = []
    for d in dirs:
        recs = _records(d)
        if not recs:
            continue
        name = os.path.basename(d)
        reqs = list(dict.fromkeys(r.get("request") or "" for r in recs))
        rows.append(
            f'<tr><td><a href="{html.escape(name)}/review.html">'
            f'{html.escape(_pretty(name))}</a></td>'
            f'<td>{len(recs)}</td>'
            f'<td>{html.escape("  ·  ".join(q for q in reqs if q)) or "&mdash;"}</td></tr>')

    doc = (f'<!doctype html><meta charset="utf-8">'
           f'<meta name="viewport" content="width=device-width,initial-scale=1">'
           f'<title>Sessions</title><style>{CSS}</style><div class="wrap">'
           f'<h1>Sessions</h1><div class="sub">{len(rows)} with reports &middot; '
           f'researcher view &mdash; do not leave this open in front of a participant</div>'
           f'<table><tr><th>Session</th><th>Reports</th><th>Briefs</th></tr>'
           f'{"".join(rows)}</table></div>')
    out = os.path.join(FEED, "index.html")
    with open(out, "w") as fh:
        fh.write(doc)
    return out
