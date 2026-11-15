// Analysis: speech map (-52 dB), isolate-transcribed blocks, face box on the mirrored proxy.
import { api, fileUrl } from '../api.js';
import { h, card, empty, badge, fmtSec, fmtPct } from '../ui.js';

const speechCanvas = (sp, blocks) => {
  const c = h('canvas.speech', { width: 1200, height: 40 });
  requestAnimationFrame(() => {
    const g = c.getContext('2d');
    const k = c.width / Math.max(0.001, sp.duration);
    g.fillStyle = '#2a3140'; blocks.forEach((b) => g.fillRect(b.start * k, 0, Math.max(1, (b.end - b.start) * k), 40));
    g.fillStyle = '#ffd400'; sp.runs.forEach((r) => g.fillRect(r.start * k, 10, Math.max(1, (r.end - r.start) * k), 20));
  });
  return c;
};

const faceView = (pid, t) => {
  if (!t.face?.face) return h('div.muted', t.face ? 'No face detected — graphics will avoid the top 60% of the frame.' : 'No face data.');
  const wrap = h('div.face-wrap');
  const W = t.face.canvas_w; const H = t.face.canvas_h;
  const box = (b, cls) => h(`div.${cls}`, { style: { left: `${(b.x0 / W) * 100}%`, top: `${(b.y0 / H) * 100}%`, width: `${((b.x1 - b.x0) / W) * 100}%`, height: `${((b.y1 - b.y0) / H) * 100}%` } });
  const video = h('video', { src: fileUrl(pid, `work/${t.take_id}/proxy.mp4#t=1`), muted: true, preload: 'auto', playsInline: true, style: { height: '320px', borderRadius: '6px', display: 'block' } });
  wrap.append(video, box(t.face.face, 'face-box'), box(t.face.head, 'head-box'));
  return h('div', wrap, h('div.muted', { style: { marginTop: '6px' } }, `Yellow = face (YuNet, ${t.face.detections}/${t.face.samples} frames) · red dashed = off-limits head region incl. clearance + shadow budget`));
};

export const renderAnalysis = async (vc) => {
  const a = await api.analysis(vc.pid);
  if (!a || !a.takes?.length) return empty('Not analysed yet', 'Run Analyze (or Prepare) to build the speech map, transcripts and face boxes.');
  return h('div', a.takes.map((t) => card(`${t.take_id}`,
    h('div.row.muted', `speech floor −52 dB · ${t.speech.runs.length} speech runs · ${t.blocks.length} blocks · digital zero ${fmtPct(t.speech.digital_zero_ratio)} · noise ${t.speech.noise_floor_db?.toFixed?.(1) ?? '–'} dB`),
    speechCanvas(t.speech, t.blocks),
    h('div.grid-2', { style: { marginTop: '12px' } },
      h('table.table', h('tbody', t.blocks.map((b) => h('tr', h('td.mono', b.id.split('_').pop()), h('td.mono', `${fmtSec(b.start)}–${fmtSec(b.end)}`),
        h('td', { dir: 'auto' }, b.text || h('span.muted', '(no words)')), h('td', b.language ? badge(b.language) : ''))))),
      faceView(vc.pid, t)))));
};
