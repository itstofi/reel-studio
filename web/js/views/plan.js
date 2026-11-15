// Plan: the beat-by-beat plan the creator approves BEFORE anything renders.
import { api } from '../api.js';
import { h, card, button, badge, empty, toast, withBusy, alertBox, select, fmtSec, toggle } from '../ui.js';
import { segmentPlayer } from './shared.js';

export const renderPlan = async (vc) => {
  const [info, alignment] = await Promise.all([api.plan(vc.pid), api.alignment(vc.pid)]);
  if (!info) return empty('No plan yet', 'Run Plan (or Prepare). The plan is shown here for approval before any render.');
  const plan = info.plan;
  const lines = Object.fromEntries((vc.project.script?.lines || []).map((l) => [l.id, l]));
  const attemptsFor = (lineId) => alignment?.lines?.find((l) => l.line_id === lineId);
  const player = segmentPlayer(vc.pid);
  const dirty = () => vc.setDirty(true);

  const beatCard = (b) => {
    const la = b.line_ids.length ? attemptsFor(b.line_ids[0]) : null;
    const dur = b.segments.reduce((s, x) => s + (x.src_out - x.src_in), 0);
    const cap = h('input', { value: b.caption_en, style: { width: '100%' }, oninput: (e) => { b.caption_en = e.target.value; dirty(); } });
    const choose = la && la.attempts.length > 1 ? select(la.attempts.map((a, i) => [i, `attempt ${i + 1}: ${a.take_id} @${a.start.toFixed(1)}s · ${Math.round(a.coverage * 100)}%${a.clean ? ' · clean' : ''}`]),
      la.chosen ?? '', async (v) => { await api.choose(vc.pid, b.id, Number(v)); toast(`${b.id}: attempt ${Number(v) + 1}`, 'ok'); vc.rerender(); }) : null;
    const line = lines[b.line_ids[0]] || {};
    return h('div.beat', { class: b.enabled ? '' : 'disabled' },
      h('div.beat-head',
        h('div.row', h('span.beat-id', b.id), badge(`${fmtSec(dur)}`), b.segments.length ? null : badge('missing', 'bad'), h('span.muted', b.note)),
        h('div.row',
          b.segments.length ? button('▶ Play', () => player.playRanges(b.segments, `${b.id}: ${b.caption_en}`)) : null,
          choose, toggle(b.enabled, (on) => { b.enabled = on; dirty(); }, 'use'))),
      line.ar || line.en ? h('div.beat-lines', h('div', { dir: 'rtl' }, line.ar || ''), h('div.muted', line.en || '')) : null,
      h('label.field', h('span.field-label', 'English caption'), cap),
      b.gaps.length ? h('div.row', { style: { marginTop: '8px' } }, b.gaps.map((g) => h('span.chip', `gap ${g.start.toFixed(2)}–${g.end.toFixed(2)}s (${(g.end - g.start).toFixed(2)}s ${g.kind}) → ${g.action}`,
        g.verified === true ? badge('A/B ok', 'ok') : g.verified === false ? badge('A/B diff — kept', 'warn') : null))) : null,
      h('div.row', { style: { marginTop: '8px' } }, b.graphics.map((g, i) => h('span.chip', `${g.template}: ${g.params.text || g.params.label || g.params.source || g.params.name || g.params.value || ''}`,
        h('a', { href: '#', onclick: (e) => { e.preventDefault(); b.graphics.splice(i, 1); dirty(); vc.rerender(); } }, '✕')))));
  };

  const save = button('Save edits', async () => withBusy(save, async () => { await api.putPlan(vc.pid, plan); vc.setDirty(false); toast('Plan saved (needs approval again)', 'ok'); vc.rerender(); }));
  const approve = button(plan.approved ? 'Approved ✓' : 'Approve plan', async () => withBusy(approve, async () => {
    await api.putPlan(vc.pid, plan); await api.approve(vc.pid); vc.setDirty(false); toast('Plan approved — you can render now', 'ok'); await vc.reload(); vc.rerender();
  }), { kind: 'primary' });

  return h('div',
    plan.approved ? alertBox('ok', `Approved ${plan.approved_at || ''}. Any edit needs a new approval.`)
      : alertBox('warn', 'Review every beat, then approve. Nothing renders until the plan is approved.'),
    plan.warnings.length ? alertBox('bad', h('strong', 'Warnings'), h('ul', plan.warnings.map((w) => h('li', w)))) : null,
    h('div.row', { style: { marginBottom: '12px' } }, approve, save, h('span.muted', `${plan.beats.length} beats · created by ${plan.created_by}`)),
    h('div.plan-layout', h('div', plan.beats.map(beatCard)), h('div', card('Preview (proxy)', player.el))));
};
