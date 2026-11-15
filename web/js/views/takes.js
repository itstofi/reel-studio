// Takes: import originals (symlinked, never modified), per-take language + mirror rule.
import { api } from '../api.js';
import { h, card, field, button, select, toggle, badge, empty, toast, withBusy, fmtBytes, fmtSec, alertBox } from '../ui.js';

const hdrBadge = (info) => (info?.hdr && info.hdr !== 'sdr' ? badge(info.hdr.toUpperCase() + ' → SDR', 'warn') : badge('SDR'));

export const renderTakes = async (vc) => {
  const p = vc.project;
  const paths = h('textarea', { placeholder: '~/Movies/IMG_0001.MOV\n~/Movies/IMG_0002.MOV', style: { minHeight: '90px' } });
  const fileInput = h('input', { type: 'file', multiple: true, accept: 'video/*' });
  const importBtn = button('Import paths', async () => {
    const list = paths.value.split('\n').map((s) => s.trim()).filter(Boolean);
    if (!list.length) return toast('Paste at least one absolute path', 'warn');
    await withBusy(importBtn, async () => { await api.importTakes(vc.pid, list, null, true); paths.value = ''; await vc.reload(); vc.rerender(); });
  }, { kind: 'primary' });
  const uploadBtn = button('Upload files', async () => {
    if (!fileInput.files.length) return toast('Choose files first', 'warn');
    await withBusy(uploadBtn, async () => { await api.uploadTakes(vc.pid, fileInput.files); await vc.reload(); vc.rerender(); });
  });

  const rows = p.takes.map((t) => h('tr',
    h('td.mono', t.id), h('td', t.filename),
    h('td', t.info ? `${t.info.width}×${t.info.height} · ${t.info.fps.toFixed(2)} fps · ${fmtSec(t.info.duration, 1)} · ${fmtBytes(t.info.size_bytes)}` : h('span.muted', 'not probed')),
    h('td', t.info ? hdrBadge(t.info) : ''),
    h('td', select([['', 'project'], ['ar', 'Arabic'], ['en', 'English']], t.lang || '',
      async (v) => { await api.patchTake(vc.pid, t.id, { lang: v || null }); await vc.reload(); })),
    h('td', toggle(t.mirror, async (on) => {
      if (!on && !confirm('Mirroring is a HARD RULE for his footage (his camera records him mirrored). Turn it off only for footage that is not his. Continue?')) { vc.rerender(); return; }
      await api.patchTake(vc.pid, t.id, { mirror: on }); await vc.reload();
    }, 'hflip')),
    h('td', button('Remove', async () => { if (confirm(`Remove ${t.filename} from the project? (the original file is not touched)`)) { await api.deleteTake(vc.pid, t.id); await vc.reload(); vc.rerender(); } }, { kind: 'danger' }))));

  return h('div',
    card('Import takes',
      h('div.grid-2',
        field('Absolute paths (one per line)', paths, 'Originals are symlinked into the project — never copied or modified.'),
        field('…or upload', h('div.stack', fileInput, h('div.muted', 'Uploads are copied into the project folder.')))),
      h('div.row', { style: { marginTop: '10px' } }, importBtn, uploadBtn)),
    card(`Takes (${p.takes.length})`,
      p.takes.length ? h('table.table', h('thead', h('tr', ['ID', 'File', 'Media', 'Colour', 'Language', 'Mirror', ''].map((x) => h('th', x)))), h('tbody', rows))
        : empty('No takes yet', 'Import his raw takes above, then run Prepare.'),
      p.takes.some((t) => !t.mirror) ? alertBox('warn', 'A take is NOT mirrored. His camera records him mirrored — every take of his footage must be flipped.') : null));
};
