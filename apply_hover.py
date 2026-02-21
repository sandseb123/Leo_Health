#!/usr/bin/env python3
"""Apply hover animation updates to leo_health/dashboard.py in Leo-Health-Pro."""
import sys, os, re

TARGET = os.path.join(os.path.dirname(__file__), '..', 'Leo-Health-Pro', 'leo_health', 'dashboard.py')
# Allow override via argument
if len(sys.argv) > 1:
    TARGET = sys.argv[1]

if not os.path.exists(TARGET):
    # Try relative path common locations
    for candidate in [
        'leo_health/dashboard.py',
        '../Leo-Health-Pro/leo_health/dashboard.py',
        os.path.expanduser('~/Leo-Health-Pro/leo_health/dashboard.py'),
    ]:
        if os.path.exists(candidate):
            TARGET = candidate
            break
    else:
        print(f"ERROR: could not find dashboard.py. Pass path as argument:\n  python3 apply_hover.py /path/to/leo_health/dashboard.py")
        sys.exit(1)

print(f"Patching: {os.path.abspath(TARGET)}")
src = open(TARGET).read()

def extract_fn(src, signature):
    """Return (start_pos, end_pos) of the JS function starting with `signature`."""
    start = src.find(signature)
    if start == -1:
        return None, None
    # Fast-forward to opening brace
    i = start
    while i < len(src) and src[i] != '{':
        i += 1
    depth = 0
    while i < len(src):
        if src[i] == '{':
            depth += 1
        elif src[i] == '}':
            depth -= 1
            if depth == 0:
                return start, i + 1
        i += 1
    return start, len(src)

# ── New drawWoHR ──────────────────────────────────────────────────────────────
NEW_DRAWWOHR = r"""function drawWoHR(canvasId, data) {
  const c = $(canvasId);
  if (!c) return;
  const dpr = window.devicePixelRatio || 1;
  const w = c.offsetWidth || c.parentElement.offsetWidth || 280;
  const h = 120;
  c.width = w * dpr; c.height = h * dpr;
  const cx = c.getContext('2d');
  cx.scale(dpr, dpr);

  const vals = data.map(d => +d.value).filter(v => !isNaN(v));
  if (vals.length < 2) return;
  const rawMax = Math.max(...vals);
  const maxRef = Math.round(Math.max(rawMax * 1.12, rawMax + 15));
  const mn = Math.min(Math.min(...vals) * 0.97, maxRef * 0.45);
  const mx = maxRef * 1.03;
  const rng = mx - mn || 1;
  const pad = {t:8, r:8, b:18, l:36};
  const cw = w - pad.l - pad.r, ch = h - pad.t - pad.b;
  const avgV = Math.round(vals.reduce((s,v)=>s+v,0)/vals.length);
  const maxV = Math.round(rawMax);

  const ZONES = [
    { lo:0,    hi:0.50, color:'#5ac8fa', name:'Easy' },
    { lo:0.50, hi:0.60, color:'#30d158', name:'Fat Burn' },
    { lo:0.60, hi:0.70, color:'#ffd60a', name:'Aerobic' },
    { lo:0.70, hi:0.85, color:'#ff9f0a', name:'Tempo' },
    { lo:0.85, hi:1.01, color:'#ff375f', name:'Peak' },
  ];
  const getZone = bpm => ZONES.find(z => (bpm/maxRef) >= z.lo && (bpm/maxRef) < z.hi) || ZONES[4];

  const pts = data.map((d, i) => ({
    x: pad.l + (i / Math.max(data.length-1, 1)) * cw,
    y: pad.t + ch - ((+d.value - mn) / rng) * ch,
    v: +d.value,
    t: d.time,
  }));

  function rrect(x, y, rw, rh, r) {
    cx.beginPath();
    cx.moveTo(x+r, y);
    cx.lineTo(x+rw-r, y); cx.quadraticCurveTo(x+rw, y, x+rw, y+r);
    cx.lineTo(x+rw, y+rh-r); cx.quadraticCurveTo(x+rw, y+rh, x+rw-r, y+rh);
    cx.lineTo(x+r, y+rh); cx.quadraticCurveTo(x, y+rh, x, y+rh-r);
    cx.lineTo(x, y+r); cx.quadraticCurveTo(x, y, x+r, y);
    cx.closePath();
  }

  function drawBase() {
    cx.clearRect(0, 0, w, h);
    ZONES.forEach(z => {
      const yTop = pad.t + ch - Math.min(1, Math.max(0, (z.hi * maxRef - mn) / rng)) * ch;
      const yBot = pad.t + ch - Math.min(1, Math.max(0, (z.lo * maxRef - mn) / rng)) * ch;
      if (yBot > yTop) { cx.fillStyle = z.color+'1a'; cx.fillRect(pad.l, yTop, cw, yBot-yTop); }
    });
    cx.lineWidth = 1;
    [0, 0.33, 0.67, 1].forEach(f => {
      const y = pad.t + ch * f;
      cx.beginPath(); cx.moveTo(pad.l, y); cx.lineTo(w-pad.r, y);
      cx.strokeStyle = 'rgba(255,255,255,0.05)'; cx.stroke();
      cx.fillStyle = 'rgba(255,255,255,0.28)'; cx.font = '9px -apple-system,sans-serif';
      cx.textAlign = 'right'; cx.textBaseline = 'middle';
      cx.fillText(Math.round(mx - rng*f), pad.l-4, y);
    });
    const grad = cx.createLinearGradient(0, pad.t, 0, pad.t+ch);
    grad.addColorStop(0, C.hr+'40'); grad.addColorStop(1, C.hr+'04');
    cx.beginPath(); cx.moveTo(pts[0].x, pts[0].y);
    for (let i=1; i<pts.length; i++) {
      const cpx = (pts[i-1].x+pts[i].x)/2;
      cx.bezierCurveTo(cpx, pts[i-1].y, cpx, pts[i].y, pts[i].x, pts[i].y);
    }
    cx.lineTo(pts[pts.length-1].x, pad.t+ch); cx.lineTo(pts[0].x, pad.t+ch);
    cx.closePath(); cx.fillStyle = grad; cx.fill();
    cx.beginPath(); cx.moveTo(pts[0].x, pts[0].y);
    for (let i=1; i<pts.length; i++) {
      const cpx = (pts[i-1].x+pts[i].x)/2;
      cx.bezierCurveTo(cpx, pts[i-1].y, cpx, pts[i].y, pts[i].x, pts[i].y);
    }
    cx.strokeStyle = C.hr; cx.lineWidth = 1.5; cx.lineJoin = 'round'; cx.stroke();
    const avgY = pad.t + ch - ((avgV - mn) / rng) * ch;
    cx.beginPath(); cx.moveTo(pad.l, avgY); cx.lineTo(w-pad.r, avgY);
    cx.strokeStyle = 'rgba(255,255,255,0.18)'; cx.lineWidth = 1;
    cx.setLineDash([3,4]); cx.stroke(); cx.setLineDash([]);
    const maxY = pad.t + ch - ((maxV - mn) / rng) * ch;
    cx.fillStyle = C.hr; cx.font = 'bold 9px -apple-system,sans-serif';
    cx.textAlign = 'left'; cx.textBaseline = 'bottom';
    cx.fillText(`\u25b2 ${maxV}`, pad.l+2, maxY-1);
    cx.fillStyle = 'rgba(255,255,255,0.35)'; cx.font = '9px -apple-system,sans-serif';
    cx.textAlign = 'right'; cx.textBaseline = 'alphabetic';
    cx.fillText(`avg ${avgV} bpm`, w-pad.r, h-2);
  }

  function drawHover(mouseX) {
    const idx = pts.reduce((b, p, i) =>
      Math.abs(p.x - mouseX) < Math.abs(pts[b].x - mouseX) ? i : b, 0);
    const pt = pts[idx];
    const zone = getZone(pt.v);
    cx.beginPath(); cx.moveTo(pt.x, pad.t); cx.lineTo(pt.x, pad.t+ch);
    cx.strokeStyle = 'rgba(255,255,255,0.22)'; cx.lineWidth = 1;
    cx.setLineDash([2,3]); cx.stroke(); cx.setLineDash([]);
    cx.beginPath(); cx.moveTo(pad.l, pt.y); cx.lineTo(w-pad.r, pt.y);
    cx.strokeStyle = 'rgba(255,255,255,0.1)'; cx.lineWidth = 1;
    cx.setLineDash([2,3]); cx.stroke(); cx.setLineDash([]);
    cx.beginPath(); cx.arc(pt.x, pt.y, 6, 0, Math.PI*2);
    cx.fillStyle = zone.color+'44'; cx.fill();
    cx.beginPath(); cx.arc(pt.x, pt.y, 4, 0, Math.PI*2);
    cx.fillStyle = zone.color; cx.fill();
    cx.beginPath(); cx.arc(pt.x, pt.y, 2, 0, Math.PI*2);
    cx.fillStyle = '#fff'; cx.fill();
    let elapsed = '';
    if (pt.t && pts[0].t) {
      const ms = new Date(pt.t) - new Date(pts[0].t);
      const m = Math.floor(ms/60000), s = Math.floor((ms%60000)/1000);
      elapsed = `+${m}:${String(s).padStart(2,'0')}`;
    }
    const bpmStr = `${Math.round(pt.v)} bpm`;
    cx.font = 'bold 13px -apple-system,sans-serif';
    const bpmW = cx.measureText(bpmStr).width;
    cx.font = '10px -apple-system,sans-serif';
    const subW = cx.measureText(zone.name).width + (elapsed ? cx.measureText(elapsed).width + 14 : 0);
    const tipW = Math.max(bpmW, subW) + 20;
    const tipH = 40;
    let tx = pt.x - tipW/2;
    if (tx < pad.l) tx = pad.l;
    if (tx + tipW > w - 4) tx = w - 4 - tipW;
    const ty = pt.y - tipH - 12 < pad.t ? pt.y + 10 : pt.y - tipH - 12;
    cx.fillStyle = 'rgba(12,12,22,0.93)';
    rrect(tx, ty, tipW, tipH, 7); cx.fill();
    cx.strokeStyle = zone.color+'55'; cx.lineWidth = 1;
    rrect(tx, ty, tipW, tipH, 7); cx.stroke();
    cx.fillStyle = zone.color;
    cx.font = 'bold 13px -apple-system,sans-serif';
    cx.textAlign = 'left'; cx.textBaseline = 'top';
    cx.fillText(bpmStr, tx+10, ty+7);
    cx.fillStyle = 'rgba(240,240,248,0.45)';
    cx.font = '10px -apple-system,sans-serif';
    cx.textAlign = 'left';
    cx.fillText(zone.name, tx+10, ty+23);
    if (elapsed) { cx.textAlign = 'right'; cx.fillText(elapsed, tx+tipW-10, ty+23); }
  }

  drawBase();
  if (c._hrMove) c.removeEventListener('mousemove', c._hrMove);
  if (c._hrLeave) c.removeEventListener('mouseleave', c._hrLeave);
  c._hrMove = e => {
    const rect = c.getBoundingClientRect();
    const mouseX = (e.clientX - rect.left) * (w / rect.width);
    if (mouseX < pad.l || mouseX > w-pad.r) { drawBase(); return; }
    drawBase(); drawHover(mouseX);
  };
  c._hrLeave = () => drawBase();
  c.addEventListener('mousemove', c._hrMove);
  c.addEventListener('mouseleave', c._hrLeave);
  c.style.cursor = 'crosshair';
}"""

# ── New attachSleepHover ──────────────────────────────────────────────────────
NEW_SLEEP = r"""function attachSleepHover(data) {
  const canvas = $('slC');
  const overlay = $('slO');
  if (!canvas || !overlay) return;
  const nights = data.slice(-Math.min(30, data.length));
  const wrap = canvas.parentElement;
  let lastIdx = -1;

  const STAGES = [
    { key:'deep',  color:'#5e5ce6',              label:'Deep'  },
    { key:'rem',   color:'#bf5af2',              label:'REM'   },
    { key:'light', color:'#32ade6',              label:'Light' },
    { key:'awake', color:'rgba(255,149,0,0.85)', label:'Awake' },
  ];

  function getIdx(e) {
    const rect = canvas.getBoundingClientRect();
    const mx = (e.clientX - rect.left) / rect.width * (canvas.width / (window.devicePixelRatio||1));
    const cw = (canvas.width / (window.devicePixelRatio||1)) - 36 - 10;
    const barW = (cw - (nights.length-1)*3) / nights.length;
    return Math.floor((mx - 36) / (barW + 3));
  }

  function rrect(cx, x, y, rw, rh, r) {
    cx.beginPath();
    cx.moveTo(x+r, y);
    cx.lineTo(x+rw-r, y); cx.quadraticCurveTo(x+rw, y, x+rw, y+r);
    cx.lineTo(x+rw, y+rh-r); cx.quadraticCurveTo(x+rw, y+rh, x+rw-r, y+rh);
    cx.lineTo(x+r, y+rh); cx.quadraticCurveTo(x, y+rh, x, y+rh-r);
    cx.lineTo(x, y+r); cx.quadraticCurveTo(x, y, x+r, y);
    cx.closePath();
  }

  function hm(v) {
    const hrs = Math.floor(v), min = Math.round((v - hrs) * 60);
    return min > 0 ? `${hrs}h ${min}m` : `${hrs}h`;
  }

  function draw(idx) {
    const dpr = window.devicePixelRatio || 1;
    const W = overlay.offsetWidth || canvas.offsetWidth || 600;
    const H = overlay.offsetHeight || canvas.offsetHeight || 150;
    overlay.width = W * dpr; overlay.height = H * dpr;
    overlay.style.width = W + 'px'; overlay.style.height = H + 'px';
    const cx = overlay.getContext('2d'); cx.scale(dpr, dpr);
    cx.clearRect(0, 0, W, H);
    if (idx < 0 || idx >= nights.length) return;

    const PAD = {l:36, r:10, t:10, b:26};
    const cw = W - PAD.l - PAD.r, ch = H - PAD.t - PAD.b;
    const barW = (cw - (nights.length-1)*3) / nights.length;
    const barX = PAD.l + idx*(barW+3);

    cx.fillStyle = 'rgba(255,255,255,0.08)';
    rrect(cx, barX-1, PAD.t, barW+2, ch, 3); cx.fill();

    const n = nights[idx];
    const sleepH = (n.deep||0) + (n.rem||0) + (n.light||0);
    const totalH  = sleepH + (n.awake||0);

    const CW = 200, CH = 90;
    const barCx = barX + barW / 2;
    let cx0 = barCx > W / 2 ? barX - CW - 6 : barX + barW + 6;
    cx0 = Math.max(4, Math.min(cx0, W - CW - 4));
    const cy0 = PAD.t + Math.max(0, (ch - CH) / 2);

    cx.fillStyle = 'rgba(10,10,22,0.95)';
    rrect(cx, cx0, cy0, CW, CH, 9); cx.fill();
    cx.strokeStyle = 'rgba(255,255,255,0.08)'; cx.lineWidth = 1;
    rrect(cx, cx0, cy0, CW, CH, 9); cx.stroke();

    cx.fillStyle = 'rgba(255,255,255,0.4)';
    cx.font = '10px -apple-system,sans-serif';
    cx.textAlign = 'left'; cx.textBaseline = 'top';
    cx.fillText(fmtDateLong(n.date), cx0+11, cy0+9);

    cx.fillStyle = '#fff';
    cx.font = 'bold 16px -apple-system,sans-serif';
    cx.fillText(hm(sleepH), cx0+11, cy0+22);

    const SBX = cx0+11, SBY = cy0+46, SBW = CW-22, SBH = 8;
    cx.save();
    rrect(cx, SBX, SBY, SBW, SBH, 4); cx.clip();
    let sx = SBX;
    STAGES.forEach(s => {
      const val = n[s.key] || 0;
      if (!val || !totalH) return;
      const sw = (val / totalH) * SBW;
      if (sw < 0.5) return;
      cx.fillStyle = s.color;
      cx.fillRect(sx, SBY, sw, SBH);
      sx += sw;
    });
    cx.restore();

    const COL2 = cx0 + 11 + (CW - 22) / 2 + 4;
    STAGES.forEach((s, i) => {
      const col = i % 2, row = Math.floor(i / 2);
      const gx = col === 0 ? cx0+11 : COL2;
      const gy = cy0+60 + row*16;
      const val = n[s.key] || 0;
      cx.fillStyle = s.color;
      cx.beginPath(); cx.arc(gx+4, gy+5, 3, 0, Math.PI*2); cx.fill();
      cx.fillStyle = 'rgba(255,255,255,0.38)';
      cx.font = '10px -apple-system,sans-serif';
      cx.textAlign = 'left'; cx.textBaseline = 'top';
      cx.fillText(s.label, gx+12, gy);
      cx.fillStyle = '#fff';
      cx.font = 'bold 10px -apple-system,sans-serif';
      cx.textAlign = 'right';
      const rightEdge = col === 0 ? COL2 - 6 : cx0 + CW - 11;
      cx.fillText(val > 0 ? hm(val) : '\u2014', rightEdge, gy);
    });
  }

  const domTT = $('tt');
  wrap.addEventListener('mousemove', e => {
    if (domTT) domTT.style.display = 'none';
    const idx = getIdx(e);
    if (idx < 0 || idx >= nights.length) { draw(-1); lastIdx = -1; return; }
    if (idx !== lastIdx) { draw(idx); lastIdx = idx; }
  });
  wrap.addEventListener('mouseleave', () => { draw(-1); lastIdx = -1; });
}"""

# ── Apply replacements ────────────────────────────────────────────────────────
for sig, new_code in [
    ('function drawWoHR(canvasId, data)', NEW_DRAWWOHR),
    ('function attachSleepHover(data)', NEW_SLEEP),
]:
    sig_full = sig + ' {'
    start, end = extract_fn(src, sig_full)
    if start is None:
        print(f"WARNING: could not find '{sig}' — skipping")
        continue
    old_fn = src[start:end]
    src = src[:start] + new_code + src[end:]
    print(f"Replaced {sig} ({len(old_fn)} -> {len(new_code)} chars)")

open(TARGET, 'w').write(src)
print(f"Done. Now run:\n  git add {TARGET}\n  git commit -m 'feat: hover animations for workout HR and sleep charts'\n  git push origin main")
