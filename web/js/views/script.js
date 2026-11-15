// Script: Arabic + English, line-aligned. [brackets] = on-screen text. Captions are always English.
import { api } from '../api.js';
import { h, card, field, button, toast, withBusy, alertBox } from '../ui.js';

export const renderScript = async (vc) => {
  const lines = vc.project.script?.lines || [];
  const ar = h('textarea', { dir: 'rtl', lang: 'ar', oninput: () => vc.setDirty(true) }, lines.map((l) => l.ar || '').join('\n'));
  const en = h('textarea', { oninput: () => vc.setDirty(true) }, lines.map((l) => l.en || '').join('\n'));
  const count = (t) => t.value.split('\n').filter((s) => s.trim()).length;
  const status = h('div.muted');
  const upd = () => { status.textContent = `Arabic ${count(ar)} lines · English ${count(en)} lines` + (count(ar) && count(en) && count(ar) !== count(en) ? ' — counts must match' : ''); };
  ar.addEventListener('input', upd); en.addEventListener('input', upd); upd();
  const save = button('Save script', async () => withBusy(save, async () => {
    await api.putScript(vc.pid, ar.value, en.value); vc.setDirty(false); toast('Script saved — re-run Plan', 'ok'); await vc.reload();
  }), { kind: 'primary' });
  const topic = h('input', { placeholder: 'e.g. Anthropic released a new Claude model today', style: { flex: 1 } });
  const length = h('select', h('option', { value: '15-30s' }, '15–30 s'), h('option', { value: '30-60s', selected: true }, '30–60 s'), h('option', { value: '60-90s' }, '60–90 s'));
  const draft = button('Draft with Claude', async () => {
    if (!topic.value.trim()) return toast('Give Claude a topic', 'warn');
    await withBusy(draft, async () => {
      const r = await api.draftScript(topic.value, length.value);
      const pick = (s) => (Array.isArray(s) ? s.join('\n') : String(s || ''));
      ar.value = pick(r.ar); en.value = pick(r.en); vc.setDirty(true); upd();
      toast('Draft ready — edit, then save', 'ok');
    });
  });
  return h('div',
    alertBox('info', 'One line per spoken line, Arabic and English paired line by line. Put on-screen text in [brackets]. ',
      'The Arabic cut gets the English line as its caption — captions are always English.'),
    card('Draft a script', h('div.row', topic, length, draft)),
    card('Script', h('div.grid-2', field('Arabic (العربية)', ar), field('English', en)), h('div.row', { style: { marginTop: '10px' } }, save, status)));
};
