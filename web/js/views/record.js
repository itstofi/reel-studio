// Record studio: teleprompter + mic picker + level meter. One take per script line; every take is cleaned
// and heard back on the server (voice/recorded.py) — last clean take wins, like his camera takes.
// Keys: Space record/stop · G hear the AI guide · P play my take · ←/→ line · Enter next line.
import { api, fileUrl, post, request } from '../api.js';
import { h, mount, toast } from '../ui.js';

const enc = encodeURIComponent;
const MIC_KEY = 'reel.mic';
const COUNTDOWN_S = 2.4;   // room tone is captured during the countdown (noise profile for the cleanup)

let S = null; // page state

const safeGet = (k) => { try { return localStorage.getItem(k); } catch { return null; } };
const safeSet = (k, v) => { try { localStorage.setItem(k, v); } catch { /* private mode */ } };

// ── audio engine ────────────────────────────────────────────────────────────
const stopStream = () => {
  S.stream?.getTracks().forEach((t) => t.stop());
  S.node?.disconnect();
  S.ctx?.close().catch(() => {});
  Object.assign(S, { stream: null, node: null, ctx: null });
};

const startStream = async (deviceId) => {
  stopStream();
  const audio = { echoCancellation: false, noiseSuppression: false, autoGainControl: false, channelCount: 1 };
  if (deviceId) audio.deviceId = { exact: deviceId };
  S.stream = await navigator.mediaDevices.getUserMedia({ audio });
  S.ctx = new AudioContext();
  await S.ctx.audioWorklet.addModule('/static/js/rec-worklet.js');
  const src = S.ctx.createMediaStreamSource(S.stream);
  S.node = new AudioWorkletNode(S.ctx, 'rec-tap');
  const mute = S.ctx.createGain();
  mute.gain.value = 0;
  src.connect(S.node).connect(mute).connect(S.ctx.destination);
  S.node.port.onmessage = (e) => {
    const buf = e.data;
    let sum = 0, pk = 0;
    for (let i = 0; i < buf.length; i++) { sum += buf[i] * buf[i]; pk = Math.max(pk, Math.abs(buf[i])); }
    S.level = Math.max(S.level * 0.9, 20 * Math.log10(Math.sqrt(sum / buf.length) + 1e-9));
    S.peak = Math.max(S.peak * 0.97, pk);
    if (S.mode === 'countdown' || S.mode === 'recording') S.chunks.push(buf);
  };
  const label = S.stream.getAudioTracks()[0]?.label || 'microphone';
  S.micLabel = label;
};

const listMics = async () => {
  const devs = (await navigator.mediaDevices.enumerateDevices()).filter((d) => d.kind === 'audioinput');
  return devs.filter((d) => d.deviceId !== 'default' && d.deviceId !== 'communications').length
    ? devs.filter((d) => d.deviceId !== 'default' && d.deviceId !== 'communications') : devs;
};

const pickDefault = (mics) => {
  const saved = safeGet(MIC_KEY);
  return mics.find((m) => m.deviceId === saved)
    || mics.find((m) => /macbook|built-in|internal/i.test(m.label)) || mics[0];
};

const wavBlob = (chunks, sr) => {
  const n = chunks.reduce((a, c) => a + c.length, 0);
  const buf = new ArrayBuffer(44 + n * 2);
  const v = new DataView(buf);
  const str = (o, s) => [...s].forEach((c, i) => v.setUint8(o + i, c.charCodeAt(0)));
  str(0, 'RIFF'); v.setUint32(4, 36 + n * 2, true); str(8, 'WAVE'); str(12, 'fmt ');
  v.setUint32(16, 16, true); v.setUint16(20, 1, true); v.setUint16(22, 1, true);
  v.setUint32(24, sr, true); v.setUint32(28, sr * 2, true); v.setUint16(32, 2, true); v.setUint16(34, 16, true);
  str(36, 'data'); v.setUint32(40, n * 2, true);
  let o = 44;
  for (const c of chunks) for (let i = 0; i < c.length; i++, o += 2) v.setInt16(o, Math.max(-1, Math.min(1, c[i])) * 0x7fff, true);
  return new Blob([buf], { type: 'audio/wav' });
};

// ── recording flow ──────────────────────────────────────────────────────────
const cur = () => S.data.lines[S.i];

const startTake = () => {
  if (!S.ctx || S.mode !== 'idle') return;
  stopPlayback();
  S.ctx.resume();
  S.chunks = [];
  S.mode = 'countdown';
  S.t0 = performance.now();
  draw();
};

const stopTake = async () => {
  if (S.mode === 'countdown') { S.mode = 'idle'; S.chunks = []; draw(); return; }
  if (S.mode !== 'recording') return;
  S.mode = 'checking';
  draw();
  const line = cur().line;
  const blob = wavBlob(S.chunks, S.ctx.sampleRate);
  S.chunks = [];
  const fd = new FormData();
  fd.append('file', blob, `line_${line}.wav`);
  try {
    const st = await request('POST', `/projects/${enc(S.pid)}/record/${line}`, fd);
    Object.assign(S.data.lines[S.i], st);
    S.data.missing = S.data.lines.filter((l) => !l.chosen).map((l) => l.line);
    const t = st.takes[st.takes.length - 1];
    S.mode = 'idle';
    if (t.ok) {
      toast(`Line ${line}: clean take ✓`, 'ok');
      if (S.auto && S.i < S.data.lines.length - 1) setTimeout(() => { if (S.mode === 'idle') go(S.i + 1); }, 1100);
    } else toast(`Line ${line}: retake — ${t.problems[0]}`, 'warn', 6000);
  } catch (err) {
    S.mode = 'idle';
    toast(err.message, 'bad', 8000);
  }
  draw();
};

const go = (i) => {
  if (S.mode !== 'idle' || i < 0 || i >= S.data.lines.length) return;
  stopPlayback();
  S.i = i;
  draw();
};

const stopPlayback = () => { if (S.audio) { S.audio.pause(); S.audio = null; } };
const play = (rel) => {
  stopPlayback();
  if (!rel) return;
  S.audio = new Audio(fileUrl(S.pid, rel, String(Date.now())));
  S.audio.play().catch((e) => toast(e.message, 'bad'));
};
const lastClean = (ln) => ln.takes.find((t) => t.n === ln.chosen);

const build = async () => {
  try {
    const { job_id } = await post(`/projects/${enc(S.pid)}/record/build`);
    S.job = { id: job_id, progress: 0, message: 'queued' };
    draw();
    const poll = async () => {
      if (!S) return;
      const j = await api.job(job_id);
      S.job = j;
      draw();
      if (j.state === 'running' || j.state === 'queued') setTimeout(poll, 1500);
      else if (j.state === 'done') {
        S.data = await api.recordSession(S.pid);
        toast(j.result?.qa_passed ? 'Video built — QA passed ✓' : 'Video built — QA flagged something', j.result?.qa_passed ? 'ok' : 'warn', 8000);
        draw();
      } else toast(`Build failed: ${j.error || j.state}`, 'bad', 10000);
    };
    poll();
  } catch (err) { toast(err.message, 'bad', 8000); }
};

// ── drawing ─────────────────────────────────────────────────────────────────
const statusOf = (ln) => (ln.chosen ? 'ok' : ln.takes.length ? 'bad' : 'todo');

const lineWords = (ln, lit) => ln.words.map((w, k) => h('span.rw', {
  class: [ln.stress.includes(k) ? 'rw-stress' : '', k < lit ? 'rw-lit' : ''].join(' '),
}, w, ln.pauses.includes(k) ? h('span.rw-pause', ' ‖') : null, ' '));

// weight per word: longer words + a breath after punctuation take longer
const schedule = (ln) => {
  const wts = ln.words.map((w, k) => 1 + w.length / 6 + (ln.pauses.includes(k) ? 0.8 : 0));
  const tot = wts.reduce((a, b) => a + b, 0);
  let acc = 0;
  return wts.map((x) => (acc += x) / tot * ln.target_s);
};

const meter = () => {
  const pct = Math.max(0, Math.min(100, (S.level + 60) / 60 * 100));
  const pk = 20 * Math.log10(S.peak + 1e-9);
  const kind = pk > -1 ? 'bad' : S.level < -42 ? 'quiet' : 'ok';
  return { pct, kind, text: pk > -1 ? 'Too loud — back off' : S.level < -48 ? 'Silent' : S.level < -42 ? 'Quiet — come closer' : 'Good level' };
};

const draw = () => {
  if (!S) return;
  const { data } = S;
  const ln = cur();
  const rtl = data.lang === 'ar';

  const side = h('div.rec-side', data.lines.map((l, i) => h('button.rec-li', {
    class: `${i === S.i ? 'active' : ''} st-${statusOf(l)}`, onclick: () => go(i),
  }, h('span.rec-dot'), h('span.rec-li-n', String(l.line)),
  h('span.rec-li-t', { dir: rtl ? 'rtl' : 'ltr' }, l.text))));

  const prompter = h('div.rec-prompter', { dir: rtl ? 'rtl' : 'ltr' }, data.lines.map((l, i) => h('div.rec-line', {
    class: i === S.i ? 'current' : i < S.i ? 'past' : 'future',
  }, i === S.i ? lineWords(l, 0) : l.text)));

  const center = h('div.rec-count');
  const m = meter();
  const takes = ln.takes.slice().reverse().map((t) => h('div.rec-take', { class: t.ok ? 'ok' : 'bad' },
    h('div.row',
      h('b', `Take ${t.n}`), h('span', t.ok ? '✓ clean' : '✗ retake'), h('span.muted', `${t.duration}s`),
      t.n === ln.chosen ? h('span.badge.badge-ok', 'in the video') : null,
      h('span.grow'),
      t.clean ? h('button', { onclick: () => play(t.clean) }, '▶ cleaned') : null,
      h('button', { onclick: () => play(t.raw) }, '▶ raw'),
      t.n !== ln.chosen && t.clean ? h('button', { onclick: async () => {
        Object.assign(S.data.lines[S.i], await post(`/projects/${enc(S.pid)}/record/${ln.line}/pin`, { take: t.n }));
        S.data.missing = S.data.lines.filter((l) => !l.chosen).map((l) => l.line);
        draw();
      } }, 'use this') : null),
    t.problems.length ? h('div.rec-prob', t.problems.join(' · ')) : null,
    t.warnings.length ? h('div.rec-warn', t.warnings.join(' · ')) : null,
    h('div.muted.mono', { dir: 'auto' }, `heard: ${t.heard_text}`),
    h('div.muted.mono', `mic ${t.metrics.speech_db} dB · noise ${t.metrics.noise_db} dB · peak ${t.metrics.peak_db} dB`
      + (t.gaps_closed.length ? ` · ${t.gaps_closed.length} long pause tightened` : ''))));

  const job = S.job;
  const jobBox = job && (job.state === 'running' || job.state === 'queued' || !job.state)
    ? h('div.rec-job', h('span.spinner'), `${job.message || 'building'} · ${Math.round((job.progress || 0) * 100)}%`,
      h('div.progress', h('div.progress-fill', { style: { width: `${(job.progress || 0) * 100}%` } })))
    : null;

  mount(S.root,
    h('div.rec-top',
      h('a', { href: `#/p/${enc(S.pid)}/render` }, '← project'),
      h('div.rec-title', data.title || data.name, h('span.muted', ' · record your voice')),
      h('span.grow'),
      h('label.rec-mic', '🎙 ', h('select', { onchange: async (e) => {
        safeSet(MIC_KEY, e.target.value);
        try { await startStream(e.target.value); } catch (err) { toast(err.message, 'bad'); }
        draw();
      } }, S.mics.map((d) => h('option', { value: d.deviceId, selected: d.deviceId === S.micId }, d.label || 'Microphone')))),
      h('div.rec-meter', { title: m.text }, h('div.rec-meter-fill', { class: `k-${m.kind}`, style: { width: `${m.pct}%` } })),
      h('span.rec-meter-t', { class: `k-${m.kind}` }, m.text),
      h('label.toggle', h('input', { type: 'checkbox', checked: S.auto, onchange: (e) => { S.auto = e.target.checked; } }), 'auto-next'),
      h('button.btn-primary', { disabled: data.missing.length > 0 || !!jobBox, onclick: build,
        title: data.missing.length ? `still to record: ${data.missing.join(', ')}` : 'cut, clean, captions, graphics, QA' },
      data.missing.length ? `Build video (${data.missing.length} lines left)` : 'Build video with my voice')),
    jobBox,
    data.final && !jobBox ? h('div.rec-final', h('video', { src: fileUrl(S.pid, data.final, job?.id || ''), controls: true, playsinline: true }),
      h('div', h('b', 'Your video'), h('div.muted.mono', data.final))) : null,
    h('div.rec-grid', side,
      h('div.rec-main',
        h('div.rec-stage', { class: `m-${S.mode}` }, prompter, center),
        h('div.rec-note', h('b', `Line ${ln.line}/${data.lines.length} · `), ln.note,
          h('span.muted', ` · aim ~${ln.target_s}s `), h('span.rec-timer'), h('span.rec-over')),
        ln.caption ? h('div.rec-cap', 'English caption: ', ln.caption) : null,
        h('div.rec-ctrl',
          h('button.rec-btn', { class: S.mode === 'recording' || S.mode === 'countdown' ? 'on' : '',
            disabled: S.mode === 'checking' || !S.ctx,
            onclick: () => (S.mode === 'idle' ? startTake() : stopTake()) },
          S.mode === 'checking' ? 'Checking…' : S.mode === 'idle' ? '● Record  (Space)' : '■ Stop  (Space)'),
          ln.guide ? h('button', { onclick: () => play(ln.guide) }, '▶ How it should sound  (G)') : null,
          lastClean(ln) ? h('button', { onclick: () => play(lastClean(ln).clean) }, '▶ My take  (P)') : null,
          h('button', { onclick: () => go(S.i - 1), disabled: S.i === 0 }, '←'),
          h('button', { onclick: () => go(S.i + 1), disabled: S.i === data.lines.length - 1 }, '→')),
        h('div.rec-takes', takes.length ? takes : h('div.muted', 'No takes yet. Press Space, wait for GO, read the line, press Space.')))));

  const curEl = S.root.querySelector('.rec-line.current');
  const box = S.root.querySelector('.rec-prompter');
  if (curEl && box) box.style.transform = `translateY(${box.parentElement.clientHeight / 2 - curEl.offsetTop - curEl.offsetHeight / 2}px)`;
};

// live parts only (no full redraw while recording: buttons must stay clickable)
const live = () => {
  const m = meter();
  const f = S.root.querySelector('.rec-meter-fill');
  const t = S.root.querySelector('.rec-meter-t');
  if (f) { f.style.width = `${m.pct}%`; f.className = `rec-meter-fill k-${m.kind}`; }
  if (t) { t.textContent = m.text; t.className = `rec-meter-t k-${m.kind}`; }
  const since = (performance.now() - S.t0) / 1000;
  const c = S.root.querySelector('.rec-count');
  if (c) c.textContent = S.mode === 'countdown' ? String(Math.max(1, Math.ceil((COUNTDOWN_S - since) / (COUNTDOWN_S / 3)))) : '';
  if (S.mode === 'recording') {
    const ln = cur();
    const el = since - COUNTDOWN_S;
    const lit = schedule(ln).filter((x) => x <= el + 0.15).length;
    S.root.querySelectorAll('.rec-line.current .rw').forEach((w, k) => w.classList.toggle('rw-lit', k < lit));
    const o = S.root.querySelector('.rec-over');
    if (o) o.textContent = el > ln.target_s + 0.6 ? ' · wrap it up' : '';
    const tm = S.root.querySelector('.rec-timer');
    if (tm) tm.textContent = `${el.toFixed(1)}s`;
  }
};

const tick = () => {
  if (!S) return;
  if (S.mode === 'countdown' && (performance.now() - S.t0) / 1000 >= COUNTDOWN_S) { S.mode = 'recording'; draw(); }
  live();
  S.raf = requestAnimationFrame(tick);
};

const onKey = (e) => {
  if (!S || e.target.tagName === 'SELECT' || e.target.tagName === 'INPUT') return;
  if (e.code === 'Space') { e.preventDefault(); S.mode === 'idle' ? startTake() : stopTake(); }
  else if (e.key === 'ArrowRight' || e.key === 'Enter') go(S.i + 1);
  else if (e.key === 'ArrowLeft') go(S.i - 1);
  else if (e.key.toLowerCase() === 'g') play(cur().guide);
  else if (e.key.toLowerCase() === 'p') { const t = lastClean(cur()); if (t) play(t.clean); }
};

export const leaveRecord = () => {
  if (!S) return;
  cancelAnimationFrame(S.raf);
  stopPlayback();
  stopStream();
  window.removeEventListener('keydown', onKey);
  S = null;
};

export const renderRecord = async (root, { pid }) => {
  leaveRecord();
  mount(root, h('div.loading', 'Opening the record studio…'));
  let data;
  try { data = await api.recordSession(pid); } catch (err) { mount(root, h('div.alert.alert-bad', err.message)); return; }
  const first = data.lines.findIndex((l) => !l.chosen);
  S = { pid, root, data, i: first < 0 ? 0 : first, mode: 'idle', level: -90, peak: 0, chunks: [], mics: [], auto: true };
  root.classList.add('page-rec');
  window.addEventListener('keydown', onKey);
  try {
    await startStream(null);                     // permission prompt first: labels appear after it
    S.mics = await listMics();
    const def = pickDefault(S.mics);
    if (def) {
      S.micId = def.deviceId;
      await startStream(def.deviceId);
    }
    navigator.mediaDevices.ondevicechange = async () => { if (S) { S.mics = await listMics(); draw(); } };
  } catch (err) {
    toast(`Microphone: ${err.message} — allow microphone access for this page`, 'bad', 10000);
  }
  draw();
  S.raf = requestAnimationFrame(tick);
};
