"""HyperFrames HTML templates for this creator's inserts (2160x3840 coordinate space).

Rules baked in (see .claude/skills/reel-motion/SKILL.md):
- Never paint #root / html / body (alpha output keeps [data-composition-id] backgrounds).
- Every graphic moves: enter (slow scale-up + fade, no bounce), internal motion (rows
  reveal, highlight travels, numbers count, image pans), explicit exit at D-0.35.
- Only transform / opacity / filter are tweened; initial hidden states are CSS.
- The card is positioned inside a graphics band computed from his face box; its shadow
  budget is inside the band too. QA measures the real alpha afterwards.
"""
from __future__ import annotations

import html
import json

GSAP_URL = "https://cdn.jsdelivr.net/npm/gsap@3.15.0/dist/gsap.min.js"  # served from HyperFrames' bundle
ACCENT = "#FFD400"


def _e(text) -> str:
    return html.escape(str(text if text is not None else ""))


def _arabic(text: str) -> bool:
    return any("؀" <= c <= "ۿ" for c in str(text))


def _txt(text, cls: str = "", extra: str = "") -> str:
    attrs = ' dir="rtl" lang="ar"' if _arabic(text) else ""
    klass = f' class="{cls}{" ar" if attrs else ""}"' if cls or attrs else ""
    return f"<span{klass}{attrs}{extra}>{_e(text)}</span>"


def _page(comp_id: str, dur: float, box: dict, body: str, css: str, script: str) -> str:
    z = float(box.get("zoom", 1.0))  # narrated reels enlarge the whole card (no face to avoid)
    x, y, w, h = (int(box[k] / z) for k in ("x", "y", "w", "h"))
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="UTF-8" />
<meta name="viewport" content="width=2160, height=3840" />
<script src="{GSAP_URL}"></script>
<style>
@font-face {{ font-family: "NotoKufiArabic"; src: url("assets/NotoKufiArabic-Bold.ttf"); font-weight: 700; }}
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
html, body {{ width: 2160px; height: 3840px; overflow: hidden; background: transparent; }}
#root {{ position: relative; width: 2160px; height: 3840px; overflow: hidden; background: transparent;
        font-family: "Montserrat", sans-serif; color: #fff; }}
#clip {{ position: absolute; inset: 0; zoom: {z}; }}
#card {{ position: absolute; left: {x}px; top: {y}px; width: {w}px; max-height: {h}px; overflow: hidden;
        padding: 64px 80px 72px; border-radius: 48px; background: rgba(12, 14, 20, 0.86);
        border: 3px solid rgba(255, 255, 255, 0.12); box-shadow: 0 16px 40px rgba(0, 0, 0, 0.40);
        opacity: 0; }}
#accent {{ position: absolute; left: 0; top: 0; width: 100%; height: 12px; background: {ACCENT};
          transform-origin: left center; }}
.kicker {{ display: block; font-size: 52px; font-weight: 900; letter-spacing: 0.12em; color: {ACCENT}; opacity: 0; }}
.ar {{ font-family: "NotoKufiArabic", sans-serif; letter-spacing: 0; }}
{css}
</style>
</head>
<body>
<div id="root" data-composition-id="{comp_id}" data-start="0" data-duration="{dur:.3f}"
     data-width="2160" data-height="3840" data-fps="60">
  <section id="clip" class="clip" data-start="0" data-duration="{dur:.3f}" data-track-index="1">
    <div id="card"><div id="accent"></div>
{body}
    </div>
  </section>
</div>
<script>
window.__timelines = window.__timelines || {{}};
const D = {dur:.3f};
const tl = gsap.timeline({{ paused: true }});
tl.fromTo("#card", {{ opacity: 0, y: 80, scale: 0.94 }}, {{ opacity: 1, y: 0, scale: 1, duration: 0.7, ease: "power3.out" }}, 0.05);
tl.fromTo("#accent", {{ scaleX: 0 }}, {{ scaleX: 1, duration: 0.7, ease: "power2.out" }}, 0.2);
tl.fromTo(".kicker", {{ opacity: 0, y: 24 }}, {{ opacity: 1, y: 0, duration: 0.5, ease: "power3.out" }}, 0.3);
{script}
tl.fromTo("#card", {{ opacity: 1, y: 0, filter: "blur(0px)" }},
  {{ opacity: 0, y: -40, filter: "blur(6px)", duration: 0.34, ease: "power2.in", immediateRender: false }}, D - 0.35);
window.__timelines["{comp_id}"] = tl;
</script>
</body>
</html>
"""


def title(cid, dur, box, p) -> str:
    text = p.get("text") or p.get("title") or ""
    kicker = p.get("kicker") or p.get("subtitle") or ""
    css = (".line { display: block; font-size: 116px; font-weight: 900; line-height: 1.08; opacity: 0; }"
           "#hl { position: absolute; left: 80px; right: 80px; bottom: 60px; height: 10px; background: "
           f"{ACCENT}; opacity: 0.7; transform-origin: left center; }}")
    words = str(text).split()
    mid = (len(words) + 1) // 2
    lines = [" ".join(words[:mid]), " ".join(words[mid:])] if len(text) > 18 else [text]
    body = (f'      {_txt(kicker, "kicker")}\n' if kicker else "") + "\n".join(
        f"      {_txt(l, 'line')}" for l in lines if l) + '\n      <div id="hl"></div>'
    script = ('tl.fromTo(".line", { opacity: 0, y: 40 }, { opacity: 1, y: 0, duration: 0.6, ease: "power3.out", '
              'stagger: 0.12 }, 0.35);\n'
              'tl.fromTo("#hl", { scaleX: 0 }, { scaleX: 1, duration: Math.max(0.6, D - 1.8), ease: "power2.inOut" }, 1.0);')
    return _page(cid, dur, box, body, css, script)


def list_(cid, dur, box, p) -> str:
    items = [str(i) for i in (p.get("items") or [])][:5] or [str(p.get("text", ""))]
    head = p.get("title", "")
    css = (".row { display: flex; align-items: center; gap: 36px; margin-top: 28px; font-size: 76px; "
           "font-weight: 800; opacity: 0; }"
           f".dot {{ width: 28px; height: 28px; border-radius: 50%; background: {ACCENT}; flex: none; }}"
           ".head { display: block; font-size: 92px; font-weight: 900; margin-bottom: 12px; opacity: 0; }")
    body = (f'      {_txt(head, "head")}\n' if head else "") + "\n".join(
        f'      <div class="row"><span class="dot"></span>{_txt(it)}</div>' for it in items)
    step = max(0.25, min(0.6, (dur - 1.6) / max(1, len(items))))
    script = ('tl.fromTo(".head", { opacity: 0, y: 30 }, { opacity: 1, y: 0, duration: 0.5, ease: "power3.out" }, 0.3);\n'
              f'tl.fromTo(".row", {{ opacity: 0, x: -60 }}, {{ opacity: 1, x: 0, duration: 0.55, ease: "power3.out", '
              f'stagger: {step:.2f} }}, 0.6);')
    return _page(cid, dur, box, body, css, script)


def stat(cid, dur, box, p) -> str:
    try:
        value = float(str(p.get("value", 0)).replace("%", "").replace(",", ""))
    except ValueError:
        value = 0.0
    prefix, suffix = _e(p.get("prefix", "")), _e(p.get("suffix", "%" if "%" in str(p.get("value", "")) else ""))
    label = p.get("label", "")
    decimals = 0 if value.is_integer() else 1
    css = ("#num { display: block; font-size: 260px; font-weight: 900; line-height: 1; color: #fff; opacity: 0; }"
           ".label { display: block; margin-top: 24px; font-size: 70px; font-weight: 700; opacity: 0;"
           " color: rgba(255,255,255,0.82); }"
           "#bar { position: relative; margin-top: 40px; height: 24px; border-radius: 12px; overflow: hidden;"
           " background: rgba(255,255,255,0.14); }"
           f"#fill {{ position: absolute; inset: 0; background: {ACCENT}; transform-origin: left center; }}")
    body = (f'      <span id="num">{prefix}0{suffix}</span>\n      {_txt(label, "label")}\n'
            '      <div id="bar"><div id="fill"></div></div>')
    # per-frame tl.set count-up (no onUpdate: render seeks may suppress events)
    frames = max(2, int(min(1.6, dur - 1.4) * 60))
    sets = []
    for k in range(frames + 1):
        v = value * (1 - (1 - k / frames) ** 3)
        txt = f"{v:,.{decimals}f}"
        sets.append(f'tl.set("#num", {{ textContent: {json.dumps(prefix + txt + suffix)} }}, {0.5 + k / 60:.4f});')
    script = ('tl.fromTo("#num", { opacity: 0, scale: 0.92 }, { opacity: 1, scale: 1, duration: 0.5, ease: "power3.out" }, 0.25);\n'
              'tl.fromTo(".label", { opacity: 0, y: 30 }, { opacity: 1, y: 0, duration: 0.5, ease: "power3.out" }, 0.6);\n'
              f'tl.fromTo("#fill", {{ scaleX: 0 }}, {{ scaleX: {max(0.05, min(1.0, value / 100 if value <= 100 else 1)):.3f}, duration: 1.4, '
              'ease: "power2.inOut" }, 0.6);\n' + "\n".join(sets))
    return _page(cid, dur, box, body, css, script)


def screenshot(cid, dur, box, p) -> str:
    """The capture is BUILT on screen: masked wipe reveal, then it pans vertically inside
    its frame like the page scrolling (never a static still, never a static box over it)."""
    src = p.get("src", "")
    caption = p.get("caption", "")
    h = max(400, box["h"] - 260)
    css = (f"#frame {{ position: relative; height: {h}px; border-radius: 28px; overflow: hidden; margin-top: 8px;"
           " background: #111; }"
           "#shot { position: absolute; left: 0; top: 0; width: 100%; transform-origin: top center; }"
           "#mask { position: absolute; inset: 0; background: rgba(12,14,20,1); transform-origin: right center; }"
           ".cap { display: block; margin-top: 28px; font-size: 64px; font-weight: 800; opacity: 0; }")
    body = (f'      <div id="frame"><img id="shot" src="{_e(src)}" alt="" /><div id="mask"></div></div>\n'
            f'      {_txt(caption, "cap")}')
    script = ('tl.fromTo("#mask", { scaleX: 1 }, { scaleX: 0, duration: 0.8, ease: "power3.inOut" }, 0.35);\n'
              'tl.fromTo("#shot", { y: 0, scale: 1.04 }, { y: -' + str(int(h * 0.35)) +
              ', scale: 1, duration: Math.max(0.8, D - 1.6), ease: "power1.inOut" }, 0.9);\n'
              'tl.fromTo(".cap", { opacity: 0, y: 24 }, { opacity: 1, y: 0, duration: 0.5, ease: "power3.out" }, 0.9);')
    return _page(cid, dur, box, body, css, script)


def nametag(cid, dur, box, p) -> str:
    name = p.get("name", "Jweed")
    sub = p.get("subtitle") or p.get("role") or "AI news · Iraq"
    css = (".name { display: block; font-size: 120px; font-weight: 900; opacity: 0; }"
           ".sub { display: block; margin-top: 12px; font-size: 60px; font-weight: 700; color: rgba(255,255,255,0.8); opacity: 0; }")
    body = f'      {_txt(name, "name")}\n      {_txt(sub, "sub")}'
    script = ('tl.fromTo(".name", { opacity: 0, x: -50 }, { opacity: 1, x: 0, duration: 0.6, ease: "power3.out" }, 0.3);\n'
              'tl.fromTo(".sub", { opacity: 0, x: -50 }, { opacity: 1, x: 0, duration: 0.6, ease: "power3.out" }, 0.55);\n'
              'tl.to("#accent", { scaleX: 0.35, duration: Math.max(0.6, D - 1.8), ease: "power2.inOut" }, 1.1);')
    return _page(cid, dur, box, body, css, script)


def logo(cid, dur, box, p) -> str:
    """Real logo image when a local file is given; otherwise the brand name as a wordmark
    whose letters are revealed one by one (never an invented logo)."""
    src = p.get("src", "")
    label = p.get("label") or p.get("source") or p.get("text") or ""
    css = ("#logo { display: block; max-height: 220px; max-width: 70%; opacity: 0; }"
           ".label { display: block; margin-top: 24px; font-size: 64px; font-weight: 700; opacity: 0;"
           " color: rgba(255,255,255,0.8); }"
           ".mark { display: block; font-size: 150px; font-weight: 900; letter-spacing: -0.02em; line-height: 1; }"
           ".ch { display: inline-block; opacity: 0; }")
    if src:
        body = f'      <img id="logo" src="{_e(src)}" alt="" />\n      {_txt(label, "label")}'
        script = ('tl.fromTo("#logo", { opacity: 0, scale: 0.9 }, { opacity: 1, scale: 1, duration: 0.8, ease: "power3.out" }, 0.3);\n'
                  'tl.fromTo(".label", { opacity: 0, y: 30 }, { opacity: 1, y: 0, duration: 0.6, ease: "power3.out" }, 0.7);\n'
                  'tl.to("#logo", { scale: 1.05, duration: Math.max(0.6, D - 1.8), ease: "power1.inOut" }, 1.2);')
    else:
        chars = "".join(f'<span class="ch">{_e(c) if c != " " else "&nbsp;"}</span>' for c in str(label))
        body = f'      <span class="mark">{chars}</span>'
        step = max(0.03, min(0.08, 0.8 / max(1, len(str(label)))))
        script = (f'tl.fromTo(".ch", {{ opacity: 0, y: 40 }}, {{ opacity: 1, y: 0, duration: 0.45, ease: "power3.out", '
                  f'stagger: {step:.3f} }}, 0.35);\n'
                  'tl.to("#accent", { scaleX: 0.25, duration: Math.max(0.6, D - 1.8), ease: "power2.inOut" }, 1.2);')
    return _page(cid, dur, box, body, css, script)


def quote(cid, dur, box, p) -> str:
    text, who = p.get("text", ""), p.get("source") or p.get("name") or ""
    words = str(text).split()
    css = (".q { display: block; font-size: 84px; font-weight: 800; line-height: 1.18; }"
           ".w { display: inline-block; opacity: 0; margin-right: 0.25em; }"
           ".src { display: block; margin-top: 28px; font-size: 56px; font-weight: 700; color: " + ACCENT + "; opacity: 0; }")
    dir_attr = ' dir="rtl" lang="ar"' if _arabic(text) else ""
    body = (f'      <span class="q{" ar" if dir_attr else ""}"{dir_attr}>' +
            "".join(f'<span class="w">{_e(w)}</span>' for w in words) + "</span>\n" + f'      {_txt(who, "src")}')
    step = max(0.04, min(0.18, (dur - 2.0) / max(1, len(words))))
    script = (f'tl.fromTo(".w", {{ opacity: 0, y: 20 }}, {{ opacity: 1, y: 0, duration: 0.35, ease: "power2.out", '
              f'stagger: {step:.3f} }}, 0.4);\n'
              'tl.fromTo(".src", { opacity: 0 }, { opacity: 1, duration: 0.5, ease: "power2.out" }, D - 1.2);')
    return _page(cid, dur, box, body, css, script)


TEMPLATES = {"title": title, "list": list_, "stat": stat, "screenshot": screenshot, "nametag": nametag,
             "logo": logo, "quote": quote}


def render_template(template: str, comp_id: str, duration: float, box: dict, params: dict) -> str:
    return TEMPLATES[template](comp_id, duration, box, params or {})


# ── story scenes (narrated reels) ────────────────────────────────────────────
def photo(cid, dur, box, p) -> str:
    """Full-frame picture that never sits still: wipe-in reveal, slow push-in + drift,
    a label chip that slides in, darkened bottom so captions stay readable."""
    src, tag = p.get("src", ""), p.get("tag", "")
    kicker, headline, credit = p.get("kicker", ""), p.get("headline", ""), p.get("credit", "")
    css_tag = ("#tag { position: absolute; left: 140px; top: 520px; padding: 26px 46px; border-radius: 999px;"
               f" background: {ACCENT}; color: #111; font-size: 76px; font-weight: 900; opacity: 0; }}"
               "#news { position: absolute; left: 140px; right: 140px; bottom: " + str(int(p.get("news_bottom", 1190))) + "px; }"
               "#kick { display: inline-block; padding: 18px 40px; background: #e11d2e; color: #fff; font-size: 64px;"
               " font-weight: 900; letter-spacing: .08em; opacity: 0; }"
               "#head { display: block; margin-top: 26px; color: #fff; font-size: 112px; font-weight: 900; line-height: 1.06;"
               " opacity: 0; }"
               + ("#head { padding: 26px 40px; background: rgba(8,10,18,.78); border-left: 14px solid #e11d2e;"
                  " font-size: 100px; text-shadow: 0 4px 24px rgba(0,0,0,.6); }" if headline else "")
               + "#credit { position: absolute; right: 140px; top: 440px; max-width: 1000px; text-align: right;"
               " color: rgba(255,255,255,.72); font-size: 34px; font-weight: 600; opacity: 0; }")
    news = ""
    if kicker or headline:
        news = ('<div id="news">' + (f'<span id="kick">{_e(kicker)}</span>' if kicker else "")
                + (f'<span id="head">{_e(headline)}</span>' if headline else "") + "</div>")
    news_js = []
    if kicker:
        news_js.append('tl.fromTo("#kick", { opacity: 0, x: -80 }, { opacity: 1, x: 0, duration: 0.45, ease: "power3.out" }, 0.45);')
        news_js.append('tl.fromTo("#kick", { opacity: 1 }, { opacity: 0.55, duration: 0.4, yoyo: true, repeat: 3, ease: "sine.inOut" }, 1.0);')
    if headline:
        news_js.append('tl.fromTo("#head", { opacity: 0, y: 50 }, { opacity: 1, y: 0, duration: 0.6, ease: "power3.out" }, 0.7);')
    if credit:
        news_js.append('tl.fromTo("#credit", { opacity: 0 }, { opacity: 1, duration: 0.5 }, 0.6);')
    if news:
        news_js.append('tl.fromTo("#news", { opacity: 1 }, { opacity: 0, duration: 0.3, immediateRender: false }, D - 0.3);')
    return f"""<!doctype html>
<html lang="en"><head><meta charset="UTF-8" /><meta name="viewport" content="width=2160, height=3840" />
<script src="{GSAP_URL}"></script>
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; }}
html, body {{ width: 2160px; height: 3840px; overflow: hidden; background: transparent; }}
#root {{ position: relative; width: 2160px; height: 3840px; overflow: hidden; background: transparent;
        font-family: "Montserrat", sans-serif; }}
#clip {{ position: absolute; inset: 0; }}
#frame {{ position: absolute; inset: 0; overflow: hidden; opacity: 0; }}
#img {{ position: absolute; left: -6%; top: -6%; width: 112%; height: 112%; object-fit: cover; }}
#shade {{ position: absolute; left: 0; right: 0; bottom: 0; height: 1500px;
          background: linear-gradient(to bottom, rgba(8,10,18,0), rgba(8,10,18,0.85)); }}
{css_tag}
</style></head>
<body>
<div id="root" data-composition-id="{cid}" data-start="0" data-duration="{dur:.3f}" data-width="2160" data-height="3840" data-fps="60">
  <section id="clip" class="clip" data-start="0" data-duration="{dur:.3f}" data-track-index="1">
    <div id="frame"><img id="img" src="{_e(src)}" alt="" /><div id="shade"></div></div>
    {f'<div id="tag">{_e(tag)}</div>' if tag else ''}
    {news}
    {f'<div id="credit">{_e(credit)}</div>' if credit else ''}
  </section>
</div>
<script>
window.__timelines = window.__timelines || {{}};
const D = {dur:.3f};
const tl = gsap.timeline({{ paused: true }});
tl.fromTo("#frame", {{ opacity: {0 if not p.get("cut_in") else 1}, scale: 1.06 }}, {{ opacity: 1, scale: 1, duration: 0.7, ease: "power2.out" }}, 0);
tl.fromTo("#img", {{ scale: 1.0, x: -40, y: 20 }}, {{ scale: 1.12, x: 40, y: -30, duration: D, ease: "none" }}, 0);
{'tl.fromTo("#tag", { opacity: 0, x: -120 }, { opacity: 1, x: 0, duration: 0.6, ease: "power3.out" }, 0.5);' if tag else ''}
tl.fromTo("#frame", {{ opacity: 1 }}, {{ opacity: 0, duration: 0.3, ease: "power2.in", immediateRender: false }}, D - 0.3);
{'tl.fromTo("#tag", { opacity: 1 }, { opacity: 0, duration: 0.3, immediateRender: false }, D - 0.3);' if tag else ''}
{chr(10).join(news_js)}
window.__timelines["{cid}"] = tl;
</script>
</body></html>
"""


def chat(cid, dur, box, p) -> str:
    """AI chat: the prompt types itself out letter by letter, typing dots, then the reply appears."""
    prompt, reply = str(p.get("prompt", "")), str(p.get("reply", ""))
    css = (".hdr { display: flex; align-items: center; gap: 18px; font-size: 44px; font-weight: 800; color: rgba(255,255,255,.7);"
           " margin-bottom: 30px; }"
           f".dot0 {{ width: 22px; height: 22px; border-radius: 50%; background: {ACCENT}; }}"
           ".user { margin-left: auto; max-width: 88%; background: " + ACCENT + "; color: #111; border-radius: 40px 40px 8px 40px;"
           " padding: 30px 40px; font-size: 64px; font-weight: 800; line-height: 1.25; min-height: 140px; opacity: 0; }"
           ".typing { display: flex; gap: 14px; margin: 34px 0 0 10px; opacity: 0; }"
           ".typing span { width: 22px; height: 22px; border-radius: 50%; background: rgba(255,255,255,.7); }"
           ".ai { margin-top: 30px; max-width: 92%; background: #2a3042; color: #fff; border-radius: 40px 40px 40px 8px;"
           " padding: 30px 40px; font-size: 58px; font-weight: 700; line-height: 1.3; opacity: 0; }")
    body = (f'      <div class="hdr"><span class="dot0"></span>AI chat</div>\n'
            f'      <div class="user"><span id="typed"></span></div>\n'
            '      <div class="typing"><span></span><span></span><span></span></div>\n'
            + (f'      <div class="ai">{_e(reply)}</div>' if reply else ""))
    type_end = min(0.6 + len(prompt) * 0.035, max(1.2, dur * 0.55))
    step = (type_end - 0.6) / max(1, len(prompt))
    sets = [f'tl.set("#typed", {{ textContent: {json.dumps(prompt[:k])} }}, {0.6 + k * step:.4f});'
            for k in range(1, len(prompt) + 1)]
    script = ('tl.fromTo(".user", { opacity: 0, y: 30 }, { opacity: 1, y: 0, duration: 0.4, ease: "power3.out" }, 0.4);\n'
              + "\n".join(sets) + "\n" +
              (f'tl.fromTo(".typing", {{ opacity: 0 }}, {{ opacity: 1, duration: 0.2 }}, {type_end + 0.1:.3f});\n'
               f'tl.fromTo(".typing span", {{ y: 0 }}, {{ y: -16, duration: 0.25, stagger: 0.1, yoyo: true, repeat: 3, ease: "sine.inOut" }}, {type_end + 0.1:.3f});\n'
               f'tl.fromTo(".typing", {{ opacity: 1 }}, {{ opacity: 0, duration: 0.15, immediateRender: false }}, {type_end + 1.0:.3f});\n'
               f'tl.fromTo(".ai", {{ opacity: 0, y: 30 }}, {{ opacity: 1, y: 0, duration: 0.5, ease: "power3.out" }}, {type_end + 1.1:.3f});'
               if reply else ""))
    return _page(cid, dur, box, body, css, script)


def bars(cid, dur, box, p) -> str:
    """Bar chart that grows: bars rise one after another while their numbers count up."""
    items = p.get("bars") or []
    title = p.get("title", "")
    top = max([float(b.get("value", 0)) for b in items] + [1.0])
    css = (".ttl { display: block; font-size: 80px; font-weight: 900; margin-bottom: 40px; opacity: 0; }"
           ".chart { display: flex; align-items: flex-end; gap: 46px; height: 900px; }"
           ".col { flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: flex-end; height: 100%; }"
           ".num { font-size: 64px; font-weight: 900; margin-bottom: 16px; opacity: 0; }"
           f".bar {{ width: 100%; border-radius: 22px 22px 6px 6px; background: linear-gradient(to top, #e0a800, {ACCENT});"
           " transform-origin: bottom center; }"
           ".lbl { margin-top: 20px; font-size: 48px; font-weight: 700; color: rgba(255,255,255,.75); }")
    cols = []
    for i, b in enumerate(items):
        hpx = int(780 * float(b.get("value", 0)) / top)
        cols.append(f'<div class="col"><span class="num" id="n{i}">0</span><div class="bar" id="b{i}" style="height:{hpx}px"></div>'
                    f'<span class="lbl">{_e(b.get("label", ""))}</span></div>')
    body = f'      <span class="ttl">{_e(title)}</span>\n      <div class="chart">' + "".join(cols) + "</div>"
    lines = ['tl.fromTo(".ttl", { opacity: 0, y: 30 }, { opacity: 1, y: 0, duration: 0.5, ease: "power3.out" }, 0.3);']
    gap = max(0.25, min(0.6, (dur - 2.4) / max(1, len(items))))
    for i, b in enumerate(items):
        t0 = 0.6 + i * gap
        v = float(b.get("value", 0))
        lines.append(f'tl.fromTo("#b{i}", {{ scaleY: 0 }}, {{ scaleY: 1, duration: 0.8, ease: "power3.out" }}, {t0:.3f});')
        lines.append(f'tl.fromTo("#n{i}", {{ opacity: 0 }}, {{ opacity: 1, duration: 0.2 }}, {t0:.3f});')
        for k in range(1, 25):
            val = int(round(v * (1 - (1 - k / 24) ** 3)))
            lines.append(f'tl.set("#n{i}", {{ textContent: "{val:,}" }}, {t0 + k * 0.8 / 24:.4f});')
    return _page(cid, dur, box, body, css, "\n".join(lines))


def compare(cid, dur, box, p) -> str:
    """Before vs after: the weak version appears, gets struck out, then the strong one slides in."""
    before, after = p.get("before", ""), p.get("after", "")
    css = (".row { position: relative; border-radius: 30px; padding: 34px 40px; margin-top: 30px; opacity: 0; }"
           ".bad { background: #2a2f3d; color: rgba(255,255,255,.65); }"
           f".good {{ background: rgba(255,212,0,.14); border: 4px solid {ACCENT}; color: #fff; }}"
           ".k { display: block; font-size: 44px; font-weight: 900; letter-spacing: .1em; margin-bottom: 14px; }"
           ".t { display: block; font-size: 62px; font-weight: 800; line-height: 1.25; }"
           ".strike { position: absolute; left: 30px; right: 30px; top: 58%; height: 10px; background: #ff5d5d;"
           " border-radius: 5px; transform-origin: left center; }")
    body = (f'      <div class="row bad"><span class="k">BEFORE ✗</span><span class="t">{_e(before)}</span><div class="strike"></div></div>\n'
            f'      <div class="row good"><span class="k" style="color:{ACCENT}">AFTER ✓</span><span class="t">{_e(after)}</span></div>')
    script = ('tl.fromTo(".bad", { opacity: 0, x: -60 }, { opacity: 1, x: 0, duration: 0.5, ease: "power3.out" }, 0.3);\n'
              'tl.fromTo(".strike", { scaleX: 0 }, { scaleX: 1, duration: 0.5, ease: "power2.inOut" }, 1.1);\n'
              'tl.fromTo(".good", { opacity: 0, x: 60, scale: 0.96 }, { opacity: 1, x: 0, scale: 1, duration: 0.6, ease: "power3.out" }, 1.6);')
    return _page(cid, dur, box, body, css, script)


def timeline(cid, dur, box, p) -> str:
    """News timeline: a line draws down, each event's dot pops and its time + text slide in."""
    items = (p.get("items") or [])[:4]
    title = p.get("title", "")
    css = (".ttl { display: block; font-size: 80px; font-weight: 900; margin-bottom: 30px; opacity: 0; }"
           ".tl { position: relative; padding-left: 110px; }"
           f"#rail {{ position: absolute; left: 30px; top: 30px; bottom: 30px; width: 10px; border-radius: 5px; background: {ACCENT};"
           " transform-origin: top center; }"
           ".ev { position: relative; margin-bottom: 54px; opacity: 0; }"
           ".dot { position: absolute; left: -102px; top: 18px; width: 54px; height: 54px; border-radius: 50%;"
           f" background: #0b0f1a; border: 10px solid {ACCENT}; }}"
           f".tm {{ display: block; font-size: 70px; font-weight: 900; color: {ACCENT}; }}"
           ".tx { display: block; margin-top: 6px; font-size: 62px; font-weight: 700; line-height: 1.2; }")
    evs = "".join(f'<div class="ev" id="e{i}"><span class="dot"></span><span class="tm">{_e(it.get("time", ""))}</span>'
                  f'<span class="tx">{_e(it.get("text", ""))}</span></div>' for i, it in enumerate(items))
    body = (f'      {_txt(title, "ttl")}\n' if title else "") + f'      <div class="tl"><div id="rail"></div>{evs}</div>'
    gap = max(0.4, min(1.4, (dur - 2.0) / max(1, len(items))))
    lines = ['tl.fromTo(".ttl", { opacity: 0, y: 30 }, { opacity: 1, y: 0, duration: 0.5, ease: "power3.out" }, 0.3);',
             f'tl.fromTo("#rail", {{ scaleY: 0 }}, {{ scaleY: 1, duration: {gap * len(items):.2f}, ease: "power1.inOut" }}, 0.5);']
    for i in range(len(items)):
        t0 = 0.6 + i * gap
        lines.append(f'tl.fromTo("#e{i}", {{ opacity: 0, x: 60 }}, {{ opacity: 1, x: 0, duration: 0.5, ease: "power3.out" }}, {t0:.3f});')
        lines.append(f'tl.fromTo("#e{i} .dot", {{ scale: 0 }}, {{ scale: 1, duration: 0.4, ease: "power3.out" }}, {t0:.3f});')
    return _page(cid, dur, box, body, css, "\n".join(lines))


TEMPLATES.update({"photo": photo, "chat": chat, "bars": bars, "compare": compare, "timeline": timeline})


def cascade(cid, dur, box, p) -> str:
    """Server grid for a cascading failure. stage: "ok" = all healthy, a health check pulses across them;
    "fail" = the first `failed` servers turn red and switch off; "domino" = the rest overload one by one."""
    stage, n, failed = p.get("stage", "domino"), int(p.get("count", 6)), int(p.get("failed", 2))
    title = p.get("title", "")
    css = (".ttl { display: block; font-size: 76px; font-weight: 900; margin-bottom: 34px; opacity: 0; }"
           ".grid { display: grid; grid-template-columns: repeat(3, 1fr); gap: 34px; }"
           ".srv { position: relative; height: 330px; border-radius: 26px; background: #1c2233; border: 6px solid #2e3a52;"
           " padding: 30px; opacity: 0; }"
           ".slot { height: 26px; border-radius: 8px; background: #2e3a52; margin-bottom: 18px; }"
           ".led { position: absolute; right: 28px; bottom: 28px; width: 44px; height: 44px; border-radius: 50%; background: #2ecc71; }"
           ".ledr { position: absolute; right: 28px; bottom: 28px; width: 44px; height: 44px; border-radius: 50%; background: #ff4d4d;"
           " opacity: 0; box-shadow: 0 0 24px #ff4d4d; }"
           ".red { position: absolute; inset: -6px; border-radius: 26px; border: 6px solid #ff4d4d; background: rgba(255,77,77,.28);"
           " opacity: 0; }"
           ".x { position: absolute; left: 0; right: 0; top: 92px; text-align: center; font-size: 120px; font-weight: 900; color: #ff4d4d; opacity: 0; }"
           ".load { position: absolute; left: 30px; right: 90px; bottom: 36px; height: 26px; border-radius: 13px;"
           f" background: {ACCENT}; transform-origin: left center; }}"
           f"#scan {{ position: absolute; top: -20px; bottom: -20px; width: 26px; border-radius: 13px; background: {ACCENT};"
           " opacity: 0; box-shadow: 0 0 40px rgba(255,212,0,.8); }"
           ".cap { display: block; margin-top: 34px; font-size: 60px; font-weight: 800; color: rgba(255,255,255,.85); opacity: 0; }")
    cells = "".join(f'<div class="srv" id="s{i}"><div class="slot"></div><div class="slot"></div><div class="slot"></div>'
                    f'<div class="load" id="l{i}"></div><span class="led"></span><span class="ledr" id="d{i}"></span><div class="red" id="r{i}"></div>'
                    f'<span class="x" id="x{i}">\u2715</span></div>' for i in range(n))
    body = ((f'      {_txt(title, "ttl")}\n' if title else "") +
            f'      <div style="position:relative"><div class="grid">{cells}</div><div id="scan"></div></div>\n'
            f'      {_txt(p.get("caption", ""), "cap")}')
    L = ['tl.fromTo(".ttl", { opacity: 0, y: 30 }, { opacity: 1, y: 0, duration: 0.5, ease: "power3.out" }, 0.25);',
         'tl.fromTo(".srv", { opacity: 0, y: 40 }, { opacity: 1, y: 0, duration: 0.5, ease: "power3.out", stagger: 0.06 }, 0.35);',
         'tl.fromTo(".load", { scaleX: 0.25 }, { scaleX: 0.3, duration: 0.1 }, 0.35);']
    if stage == "ok":
        L += ['tl.fromTo("#scan", { opacity: 0, x: -40 }, { opacity: 1, x: -40, duration: 0.2 }, 1.0);',
              f'tl.fromTo("#scan", {{ x: -40 }}, {{ x: 1560, duration: {max(0.8, dur - 2.2):.2f}, ease: "power1.inOut" }}, 1.1);',
              'tl.fromTo(".cap", { opacity: 0, y: 20 }, { opacity: 1, y: 0, duration: 0.5, ease: "power3.out" }, 1.2);']
    else:
        pre = "domino" == stage
        t_fail = 0.5 if pre else 1.0
        for i in range(failed):
            L += [f'tl.fromTo("#r{i}, #d{i}", {{ opacity: 0 }}, {{ opacity: 1, duration: 0.3 }}, {t_fail + i * 0.35:.2f});',
                  f'tl.fromTo("#x{i}", {{ opacity: 0, scale: 0.6 }}, {{ opacity: 1, scale: 1, duration: 0.35, ease: "power3.out" }}, {t_fail + i * 0.35:.2f});',
                  f'tl.fromTo("#s{i}", {{ opacity: 1 }}, {{ opacity: 0.55, duration: 0.4, immediateRender: false }}, {t_fail + 0.4 + i * 0.35:.2f});']
        if pre:
            rest = list(range(failed, n))
            t0 = t_fail + failed * 0.35 + 0.3
            L.append(f'tl.fromTo(".load", {{ scaleX: 0.3 }}, {{ scaleX: 1, duration: 0.9, ease: "power2.in", immediateRender: false }}, {t0:.2f});')
            step = max(0.25, min(0.6, (dur - t0 - 1.6) / max(1, len(rest))))
            for k, i in enumerate(rest):
                t = t0 + 1.0 + k * step
                L += [f'tl.fromTo("#r{i}, #d{i}", {{ opacity: 0 }}, {{ opacity: 1, duration: 0.25 }}, {t:.2f});',
                      f'tl.fromTo("#x{i}", {{ opacity: 0, scale: 0.6 }}, {{ opacity: 1, scale: 1, duration: 0.3, ease: "power3.out" }}, {t:.2f});',
                      f'tl.fromTo("#s{i}", {{ rotation: 0 }}, {{ rotation: {(-1) ** k * 4}, duration: 0.3, ease: "power2.out" }}, {t:.2f});']
        L.append(f'tl.fromTo(".cap", {{ opacity: 0, y: 20 }}, {{ opacity: 1, y: 0, duration: 0.5, ease: "power3.out" }}, {t_fail + 0.3:.2f});')
    return _page(cid, dur, box, body, css, "\n".join(L))


TEMPLATES["cascade"] = cascade


# ── "how it works" scenes (explainer reels) ──────────────────────────────────
def pipeline(cid, dur, box, p) -> str:
    """How the code runs: stages light up one after another while a data packet travels down the rail;
    each finished stage gets a green check."""
    nodes = (p.get("nodes") or [])[:6]
    title = p.get("title", "")
    n = max(1, len(nodes))
    nh, gap = 100, 26
    css = (".ttl { display: block; font-size: 80px; font-weight: 900; margin-bottom: 34px; opacity: 0; }"
           ".flow { position: relative; padding-left: 110px; }"
           f"#rail, #fill {{ position: absolute; left: 40px; top: {nh // 2}px; width: 8px; height: {(n - 1) * (nh + gap)}px;"
           " border-radius: 4px; background: rgba(255,255,255,.14); }"
           f"#fill {{ background: {ACCENT}; transform-origin: top center; }}"
           f"#pkt {{ position: absolute; left: 24px; top: {nh // 2 - 20}px; width: 40px; height: 40px; border-radius: 50%;"
           f" background: {ACCENT}; box-shadow: 0 0 34px {ACCENT}; opacity: 0; }}"
           f".node {{ position: relative; height: {nh}px; margin-bottom: {gap}px; border-radius: 24px; background: #1c2233;"
           " border: 4px solid #2e3a52; display: flex; align-items: center; gap: 26px; padding: 0 34px; opacity: 0; }"
           f".on {{ position: absolute; inset: -4px; border-radius: 24px; border: 4px solid {ACCENT};"
           " background: rgba(255,212,0,.10); opacity: 0; }"
           f".num {{ font-size: 44px; font-weight: 900; color: {ACCENT}; width: 50px; }}"
           ".lb { font-size: 58px; font-weight: 800; }"
           ".sb { margin-left: auto; font-size: 40px; font-weight: 700; color: rgba(255,255,255,.62); }"
           ".ck { position: absolute; right: -14px; top: -14px; width: 54px; height: 54px; border-radius: 50%;"
           " background: #2ecc71; color: #0b0f1a; font-size: 34px; font-weight: 900; display: grid; place-items: center; opacity: 0; }")
    rows = "".join(f'<div class="node" id="nd{i}"><div class="on"></div><span class="num">{i + 1}</span>'
                   f'<span class="lb">{_e(nd.get("label", ""))}</span><span class="sb">{_e(nd.get("sub", ""))}</span>'
                   f'<span class="ck">✓</span></div>' for i, nd in enumerate(nodes))
    body = ((f'      {_txt(title, "ttl")}\n' if title else "")
            + f'      <div class="flow"><div id="rail"></div><div id="fill"></div><div id="pkt"></div>{rows}</div>')
    t_on = 0.4 + 0.08 * n + 0.3
    step = max(0.35, min(1.0, (dur - 0.9 - t_on) / n))
    js = ['tl.fromTo(".ttl", { opacity: 0, y: 30 }, { opacity: 1, y: 0, duration: 0.5, ease: "power3.out" }, 0.3);',
          'tl.fromTo(".node", { opacity: 0, x: -50 }, { opacity: 1, x: 0, duration: 0.45, ease: "power3.out", stagger: 0.08 }, 0.4);',
          f'tl.fromTo("#pkt", {{ opacity: 0, scale: 0.4 }}, {{ opacity: 1, scale: 1, duration: 0.3, ease: "power2.out" }}, {t_on:.3f});']
    for i in range(len(nodes)):
        t = t_on + i * step
        if i:
            js.append(f'tl.fromTo("#pkt", {{ y: {(i - 1) * (nh + gap)} }}, {{ y: {i * (nh + gap)}, duration: {step * 0.45:.3f},'
                      f' ease: "power2.inOut", immediateRender: false }}, {t - step * 0.45:.3f});')
            js.append(f'tl.fromTo("#fill", {{ scaleY: {(i - 1) / max(1, n - 1):.4f} }}, {{ scaleY: {i / max(1, n - 1):.4f},'
                      f' duration: {step * 0.45:.3f}, ease: "power2.inOut", immediateRender: {"true" if i == 1 else "false"} }},'
                      f' {t - step * 0.45:.3f});')
        js.append(f'tl.fromTo("#nd{i} .on", {{ opacity: 0 }}, {{ opacity: 1, duration: 0.2 }}, {t:.3f});')
        js.append(f'tl.fromTo("#nd{i}", {{ scale: 1 }}, {{ scale: 1.03, duration: {step * 0.3:.3f}, ease: "power2.out",'
                  f' yoyo: true, repeat: 1, immediateRender: false }}, {t:.3f});')
        js.append(f'tl.fromTo("#nd{i} .ck", {{ opacity: 0, scale: 0.3 }}, {{ opacity: 1, scale: 1, duration: 0.3,'
                  f' ease: "power3.out" }}, {t + step * 0.55:.3f});')
        js.append(f'tl.fromTo("#nd{i} .on", {{ opacity: 1 }}, {{ opacity: 0.35, duration: 0.3, immediateRender: false }},'
                  f' {t + step * 0.7:.3f});')
    return _page(cid, dur, box, body, css, "\n".join(js))


def _out_class(line: str) -> str:
    s = line.lstrip()
    return "ok" if s.startswith(("✓", "PASS")) else "bad" if s.startswith(("✗", "FAIL")) else \
        "warn" if s.startswith("→") else "dim"


def terminal(cid, dur, box, p) -> str:
    """A command runs: it types after the prompt, then the output prints line by line
    (✓/PASS green, ✗/FAIL red, → yellow)."""
    cmd, out = str(p.get("command", "")), [str(x) for x in (p.get("lines") or [])][:11]
    title = p.get("title", "Terminal")
    css = ("#card { padding: 0 0 40px; }"
           ".bar { display: flex; align-items: center; gap: 18px; padding: 34px 44px 26px; background: rgba(255,255,255,.06); }"
           ".bar i { width: 30px; height: 30px; border-radius: 50%; display: inline-block; }"
           ".bar b { margin-left: 18px; font-size: 40px; color: rgba(255,255,255,.6); font-weight: 700; }"
           ".scr { padding: 30px 48px 0; font-family: Menlo, monospace; font-size: 45px; line-height: 1.45; color: #d7dbe4;"
           " white-space: pre-wrap; word-break: break-word; }"
           f".pr {{ color: {ACCENT}; font-weight: 700; }}"
           f"#caret {{ display: inline-block; width: 26px; height: 52px; background: {ACCENT}; vertical-align: -10px; margin-left: 4px; }}"
           ".ol { display: block; opacity: 0; } .ok { color: #3ccf7a; } .bad { color: #ff6b6b; }"
           f".warn {{ color: {ACCENT}; }} .dim {{ color: rgba(255,255,255,.62); }}")
    lines_html = "".join(f'<span class="ol {_out_class(x)}" id="o{i}">{_e(x)}</span>' for i, x in enumerate(out))
    body = (f'      <div class="bar"><i style="background:#ff5f57"></i><i style="background:#febc2e"></i>'
            f'<i style="background:#28c840"></i><b>{_e(title)}</b></div>\n'
            f'      <div class="scr"><span class="ol" id="cl" style="opacity:1"><span class="pr">$ </span>'
            f'<span id="cmd"></span><span id="caret"></span></span>{lines_html}</div>')
    type_end = min(0.5 + len(cmd) * 0.03, max(1.0, dur * 0.3))
    k_step = (type_end - 0.5) / max(1, len(cmd))
    js = [f'tl.set("#cmd", {{ textContent: {json.dumps(cmd[:k])} }}, {0.5 + k * k_step:.4f});' for k in range(1, len(cmd) + 1)]
    js.append(f'tl.fromTo("#caret", {{ opacity: 1 }}, {{ opacity: 0, duration: 0.1, immediateRender: false }}, {type_end + 0.15:.3f});')
    step = max(0.18, min(0.7, (dur - 1.0 - type_end) / max(1, len(out))))
    for i in range(len(out)):
        js.append(f'tl.fromTo("#o{i}", {{ opacity: 0, x: -24 }}, {{ opacity: 1, x: 0, duration: 0.22, ease: "power2.out" }},'
                  f' {type_end + 0.3 + i * step:.3f});')
    return _page(cid, dur, box, body, css, "\n".join(js))


_PY_KW = {"for", "in", "if", "else", "elif", "break", "return", "def", "while", "not", "and", "or", "import",
          "from", "range", "True", "False", "None", "with", "as"}


def _py_html(line: str) -> str:
    """Tiny Python highlighter: comments, strings, keywords, numbers, calls."""
    import re
    out, pos = [], 0
    for m in re.finditer(r"(#.*$)|(\"[^\"]*\"|'[^']*')|(\b\d+(?:\.\d+)?\b)|(\b[A-Za-z_]\w*\b)(?=\s*\()|(\b[A-Za-z_]\w*\b)", line):
        out.append(_e(line[pos:m.start()]))
        tok = m.group(0)
        if m.group(1):
            out.append(f'<span class="cm">{_e(tok)}</span>')
        elif m.group(2):
            out.append(f'<span class="st">{_e(tok)}</span>')
        elif m.group(3):
            out.append(f'<span class="nu">{_e(tok)}</span>')
        elif m.group(4):
            out.append(f'<span class="{"kw" if tok in _PY_KW else "fn"}">{_e(tok)}</span>')
        else:
            out.append(f'<span class="kw">{_e(tok)}</span>' if tok in _PY_KW else _e(tok))
        pos = m.end()
    out.append(_e(line[pos:]))
    return "".join(out)


def code(cid, dur, box, p) -> str:
    """The code runs on screen: lines appear, then an execution pointer steps through them while a status
    chip says what is happening at each step (steps: [{line: 1-based, note, kind: ok|bad|""}])."""
    lines = [str(x) for x in (p.get("lines") or [])][:10]
    steps = (p.get("steps") or [])[:10]
    title = p.get("title", "main.py")
    lh = 70
    css = ("#card { padding: 0 0 40px; }"
           ".bar { display: flex; align-items: center; gap: 18px; padding: 34px 44px 26px; background: rgba(255,255,255,.06); }"
           ".bar i { width: 30px; height: 30px; border-radius: 50%; display: inline-block; }"
           ".bar b { margin-left: 18px; font-size: 40px; color: rgba(255,255,255,.6); font-weight: 700; }"
           ".ed { position: relative; padding: 26px 0 0; font-family: Menlo, monospace; font-size: 44px; color: #e6e9f0; }"
           f".ln {{ position: relative; display: flex; height: {lh}px; align-items: center; white-space: pre; opacity: 0; }}"
           ".g { width: 110px; text-align: right; padding-right: 34px; color: rgba(255,255,255,.3); flex: none; }"
           f"#hl {{ position: absolute; left: 0; right: 0; top: 26px; height: {lh}px; background: rgba(255,212,0,.15);"
           f" border-left: 10px solid {ACCENT}; opacity: 0; }}"
           ".kw { color: #c792ea; } .fn { color: #ffd866; } .st { color: #a9dc76; } .nu { color: #f78c6c; } .cm { color: #6b7385; }"
           ".note { display: block; margin: 34px 44px 0; padding: 24px 34px; border-radius: 24px; font-size: 52px; font-weight: 800;"
           " background: #2a3042; opacity: 0; }"
           ".note.ok { background: rgba(46,204,113,.18); color: #5fe39a; } .note.bad { background: rgba(255,93,93,.16); color: #ff8080; }")
    rows = "".join(f'<div class="ln"><span class="g">{i + 1}</span><span>{_py_html(x)}</span></div>' for i, x in enumerate(lines))
    body = (f'      <div class="bar"><i style="background:#ff5f57"></i><i style="background:#febc2e"></i>'
            f'<i style="background:#28c840"></i><b>{_e(title)}</b></div>\n'
            f'      <div class="ed"><div id="hl"></div>{rows}</div>\n      <span class="note" id="note"></span>')
    js = ['tl.fromTo(".ln", { opacity: 0, x: -30 }, { opacity: 1, x: 0, duration: 0.35, ease: "power2.out", stagger: 0.07 }, 0.35);']
    ts = 0.35 + 0.07 * len(lines) + 0.35
    sd = max(0.3, (dur - 0.7 - ts) / max(1, len(steps)))
    if steps:
        js.append(f'tl.fromTo("#hl", {{ opacity: 0 }}, {{ opacity: 1, duration: 0.2 }}, {ts:.3f});')
        js.append(f'tl.fromTo("#note", {{ opacity: 0 }}, {{ opacity: 1, duration: 0.2 }}, {ts:.3f});')
    prev = None
    for i, st in enumerate(steps):
        t = ts + i * sd
        y = (int(st.get("line", 1)) - 1) * lh
        if prev is None:
            js.append(f'tl.set("#hl", {{ y: {y} }}, {t:.3f});')
        else:
            js.append(f'tl.fromTo("#hl", {{ y: {prev} }}, {{ y: {y}, duration: 0.22, ease: "power2.inOut", immediateRender: false }}, {t:.3f});')
        prev = y
        js.append(f'tl.set("#note", {{ textContent: {json.dumps(str(st.get("note", "")))},'
                  f' attr: {{ class: {json.dumps("note " + str(st.get("kind", "")))} }} }}, {t:.3f});')
        js.append(f'tl.fromTo("#note", {{ y: 14 }}, {{ y: 0, duration: 0.22, ease: "power2.out", immediateRender: false }}, {t:.3f});')
    return _page(cid, dur, box, body, css, "\n".join(js))


def facebox(cid, dur, box, p) -> str:
    """Face-safe layout, live: the detector's real face box draws on the photo, grows into the no-graphics
    zone (face + clearance), then a graphic slides in and settles outside it. Steps list on the right."""
    src = p.get("src", "")
    iw0, ih0 = float(p.get("img_w", 1)), float(p.get("img_h", 1))
    fx0, fy0, fx1, fy1 = (float(v) for v in p.get("face", [0, 0, 1, 1]))
    side, top, bot = (float(p.get(k, d)) for k, d in (("side", .30), ("top", .45), ("bottom", .25)))
    ih = int(p.get("height", 760))
    s = ih / ih0
    iw = int(iw0 * s)
    fw, fh = fx1 - fx0, fy1 - fy0
    hz = (max(0, fx0 - side * fw), max(0, fy0 - top * fh), min(iw0, fx1 + side * fw), min(ih0, fy1 + bot * fh))
    px = lambda v: int(v * s)  # noqa: E731
    steps = [str(x) for x in (p.get("steps") or ["Find the face", "Add a safe zone", "Place graphics outside it"])][:3]
    score = p.get("score")
    css = (".wrap { display: flex; gap: 50px; align-items: flex-start; }"
           f".ph {{ position: relative; width: {iw}px; height: {ih}px; border-radius: 28px; overflow: hidden; background: #1c2233; flex: none; }}"
           ".ph img { position: absolute; inset: 0; width: 100%; height: 100%; }"
           f"#fb {{ position: absolute; left: {px(fx0)}px; top: {px(fy0)}px; width: {px(fw)}px; height: {px(fh)}px;"
           f" border: 6px solid {ACCENT}; border-radius: 10px; opacity: 0; }}"
           f"#fl {{ position: absolute; left: {px(fx0)}px; top: {px(fy0) - 56}px; padding: 4px 14px; background: {ACCENT}; color: #111;"
           " font-size: 30px; font-weight: 900; border-radius: 8px; opacity: 0; }"
           f"#hz {{ position: absolute; left: {px(hz[0])}px; top: {px(hz[1])}px; width: {px(hz[2] - hz[0])}px; height: {px(hz[3] - hz[1])}px;"
           " border: 6px dashed #ff5d5d; background: rgba(255,93,93,.14); border-radius: 16px; opacity: 0; }"
           "#hl2 { position: absolute; left: 12px; bottom: 10px; color: #ff8080; font-size: 30px; font-weight: 900; }"
           f"#mc {{ position: absolute; left: 6%; width: 88%; top: {min(ih - 130, px(hz[3]) + 24)}px; height: 110px; border-radius: 18px;"
           f" background: rgba(12,14,20,.92); border-top: 10px solid {ACCENT}; display: flex; align-items: center; padding: 0 26px;"
           " font-size: 38px; font-weight: 800; opacity: 0; }"
           ".steps { flex: 1; padding-top: 30px; }"
           ".st { display: flex; gap: 22px; align-items: flex-start; margin-bottom: 56px; font-size: 56px; font-weight: 800; line-height: 1.2; opacity: 0; }"
           f".st b {{ flex: none; width: 70px; height: 70px; border-radius: 50%; background: {ACCENT}; color: #111;"
           " display: grid; place-items: center; font-size: 40px; }")
    label = f"FACE {float(score):.0%}" if score is not None else "FACE"
    body = (f'      <div class="wrap"><div class="ph"><img src="{_e(src)}" />'
            f'<div id="hz"><span id="hl2">NO GRAPHICS</span></div><div id="fb"></div><span id="fl">{_e(label)}</span>'
            f'<div id="mc">Graphic goes here ✓</div></div>'
            '<div class="steps">' + "".join(f'<div class="st" id="s{i}"><b>{i + 1}</b><span>{_e(t)}</span></div>'
                                            for i, t in enumerate(steps)) + "</div></div>")
    a = max(0.9, (dur - 1.5) / 3)
    t1, t2, t3 = 0.5, 0.5 + a, 0.5 + 2 * a
    js = [f'tl.fromTo("#s0", {{ opacity: 0, x: 40 }}, {{ opacity: 1, x: 0, duration: 0.4, ease: "power3.out" }}, {t1:.3f});',
          f'tl.fromTo("#fb", {{ opacity: 0, scale: 1.35 }}, {{ opacity: 1, scale: 1, duration: 0.6, ease: "power3.out" }}, {t1 + 0.1:.3f});',
          f'tl.fromTo("#fl", {{ opacity: 0, y: 10 }}, {{ opacity: 1, y: 0, duration: 0.3 }}, {t1 + 0.5:.3f});',
          f'tl.fromTo("#s1", {{ opacity: 0, x: 40 }}, {{ opacity: 1, x: 0, duration: 0.4, ease: "power3.out" }}, {t2:.3f});',
          f'tl.fromTo("#hz", {{ opacity: 0, scale: 0.55 }}, {{ opacity: 1, scale: 1, duration: 0.7, ease: "power3.out" }}, {t2 + 0.1:.3f});',
          f'tl.fromTo("#s2", {{ opacity: 0, x: 40 }}, {{ opacity: 1, x: 0, duration: 0.4, ease: "power3.out" }}, {t3:.3f});',
          f'tl.fromTo("#mc", {{ opacity: 0, x: {iw} }}, {{ opacity: 1, x: 0, duration: 0.8, ease: "power3.out" }}, {t3 + 0.1:.3f});']
    return _page(cid, dur, box, body, css, "\n".join(js))


TEMPLATES.update({"pipeline": pipeline, "terminal": terminal, "code": code, "facebox": facebox})
