// QA: adversarial checks on the DELIVERED file.
import { api } from '../api.js';
import { h, card, badge, empty, alertBox, button, toast } from '../ui.js';

export const renderQA = async (vc) => {
  const rep = await api.qa(vc.pid);
  const rerun = button('Run QA again', async () => { const r = await api.run(vc.pid, 'qa'); toast(`QA queued (${r.job_id})`); });
  if (!rep) return h('div', empty('No QA report yet', 'Render the final, then run QA.'), rerun);
  const kind = { pass: 'ok', warn: 'warn', fail: 'bad', skip: '' };
  return h('div',
    rep.passed ? alertBox('ok', h('strong', 'QA PASSED'), ` — ${rep.file}`) : alertBox('bad', h('strong', 'QA FAILED'), ' — do not deliver until every fail is fixed.'),
    h('div.row', { style: { marginBottom: '10px' } }, rerun, h('span.muted', rep.created_at)),
    card('Checks', h('table.table', h('tbody', rep.checks.map((c) => h('tr',
      h('td', badge(c.status, kind[c.status])), h('td.mono', c.name), h('td', c.summary,
        Object.keys(c.details || {}).length ? h('details', h('summary.muted', 'details'), h('pre.mono', { style: { whiteSpace: 'pre-wrap', maxHeight: '300px', overflow: 'auto' } }, JSON.stringify(c.details, null, 2))) : null)))))));
};
