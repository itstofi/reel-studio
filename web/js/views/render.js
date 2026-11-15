// Render: preview (1080p/30) and final (2160x3840/60 HEVC hvc1 Rec.709) + player.
import { api, fileUrl } from '../api.js';
import { h, card, button, alertBox, empty, projectRel, toast } from '../ui.js';

export const renderRender = async (vc) => {
  const p = vc.project;
  const info = await api.plan(vc.pid);
  const approved = info?.plan?.approved;
  const run = (stage) => async () => { const r = await api.run(vc.pid, stage); toast(`${stage} queued (${r.job_id})`, 'info'); };
  const video = (abs, title) => {
    const rel = projectRel(vc.pid, abs);
    return card(title,
      h('video.player', { src: fileUrl(vc.pid, rel, p.stages?.render?.finished_at || ''), controls: true, playsInline: true }),
      h('div.row', { style: { marginTop: '8px' } }, h('span.mono.muted', abs), button('Reveal in Finder', () => api.reveal(vc.pid, rel))));
  };
  return h('div',
    approved ? null : alertBox('warn', 'The plan is not approved yet — approve it on the Plan tab first.'),
    card('Render',
      h('p.muted', 'Final: 2160×3840 · 60 fps · HEVC hvc1 · .mov · Rec.709 SDR · −14 LUFS. Every take mirrored, HLG converted, English captions, graphics off the face.'),
      h('div.row', button('Preview render (fast)', run('preview'), { disabled: !approved }), button('Final render', run('render'), { kind: 'primary', disabled: !approved }), button('Run QA', run('qa'), { disabled: !p.final_path }))),
    p.final_path ? video(p.final_path, 'Final') : null,
    p.preview_path ? video(p.preview_path, 'Preview') : null,
    !p.final_path && !p.preview_path ? empty('Nothing rendered yet') : null);
};
