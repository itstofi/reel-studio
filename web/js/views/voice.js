// Voice lab: Arabic/English speech in Jweed's cloned voice (ElevenLabs / local Chatterbox) or macOS say.
import { api, fileUrl } from '../api.js';
import { h, card, field, button, select, badge, alertBox, toast, withBusy, projectRel, empty } from '../ui.js';

export const renderVoice = async (vc) => {
  const [engines, clips] = await Promise.all([api.voiceEngines(), api.voiceClips(vc.pid)]);
  const avail = engines.filter((e) => e.available);
  const text = h('textarea', { dir: 'auto', style: { minHeight: '110px' }, placeholder: 'خبر عاجل من عالم الذكاء الاصطناعي…' });
  let engine = (avail[0] || engines[0])?.name || 'say';
  let lang = 'ar';
  const gen = button('Generate', async () => withBusy(gen, async () => {
    if (!text.value.trim()) return toast('Type the text first', 'warn');
    const r = await api.generateVoice(vc.pid, { text: text.value, lang, engine, voice: 'jweed' });
    toast(`voice job ${r.job_id} queued`, 'info');
  }), { kind: 'primary' });
  vc.onJob((job) => { if (job.kind === 'voice' && job.state === 'done') vc.rerender(); });
  return h('div',
    alertBox('info', 'Generated voice never goes over his visible mouth (no lip-sync): use it for graphics-only inserts, intro/outro cards, narrated reels or an explicit dub.'),
    card('Engines', h('table.table', h('tbody', engines.map((e) => h('tr', h('td.mono', e.name), h('td', badge(e.available ? 'ready' : 'off', e.available ? 'ok' : '')), h('td.muted', e.detail), h('td', e.clone ? 'clones his voice' : 'stock voice')))))),
    card('Generate',
      h('div.row', field('Engine', select(engines.map((e) => [e.name, e.name, !e.available]), engine, (v) => { engine = v; })),
        field('Language', select([['ar', 'Arabic'], ['en', 'English']], lang, (v) => { lang = v; }))),
      field('Text', text), h('div.row', { style: { marginTop: '10px' } }, gen)),
    card(`Clips (${clips.length})`, clips.length ? h('table.table', h('tbody', clips.slice().reverse().map((c) => h('tr',
      h('td.mono', c.id), h('td', badge(c.engine)), h('td', { dir: 'auto' }, c.text), h('td', h('audio', { controls: true, src: fileUrl(vc.pid, projectRel(vc.pid, c.path)) })))))) : empty('No clips yet')));
};
