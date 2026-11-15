// Projects list + create form + system doctor.
import { api } from '../api.js';
import { h, mount, loading, empty, errorBox, button, stateBadge, badge, card, field, select, toast, withBusy, fmtDate } from '../ui.js';
import { href, navigate } from '../router.js';
import { STAGE_ORDER } from './shared.js';

const createForm = () => {
  const name = h('input', { type: 'text', placeholder: 'e.g. Claude weapons reel', required: true, maxlength: 120 });
  let lang = 'ar';
  const submit = h('button.btn.btn-primary', { type: 'submit' }, 'Create project');
  const onsubmit = (e) => {
    e.preventDefault();
    const n = name.value.trim();
    if (!n) { name.focus(); return; }
    withBusy(submit, async () => {
      const p = await api.createProject(n, lang);
      toast(`Created ${p.name}`, 'ok');
      navigate(href(p.id, 'takes'));
    });
  };
  return h('form.create-form', { onsubmit },
    field('Name', name),
    field('Language of the cut', select([['ar', 'Arabic (English captions)'], ['en', 'English']], lang, (v) => { lang = v; })),
    submit);
};

const stageStrip = (p) => h('div.mini-stages', STAGE_ORDER.map((s) => {
  const st = p.stages?.[s]?.state || 'pending';
  return h('span.mini-stage', { class: `mini-${st}`, title: `${s}: ${st}` }, s);
}));

const projectRow = (p) => h('a.project-row', { href: href(p.id, 'takes') },
  h('div.project-main',
    h('div.project-name', p.name),
    h('div.project-meta', `${p.id} · created ${fmtDate(p.created_at)}`)),
  h('div.project-badges',
    badge(p.settings?.lang === 'en' ? 'EN' : 'AR', 'info'),
    badge(`${p.takes?.length || 0} takes`),
    p.final_path ? badge('delivered', 'ok') : null),
  stageStrip(p));

const doctorCard = () => {
  const body = h('div', h('p.muted', 'Checks tools, models and integrations (ffmpeg, whisper, YuNet, HyperFrames, Claude, voice).'));
  const run = button('Run doctor', (e) => withBusy(e.currentTarget, async () => {
    mount(body, loading('Checking…'));
    try {
      const res = await api.doctor();
      const checks = res?.checks || [];
      mount(body, checks.length
        ? h('table.table', h('tbody', checks.map((c) => h('tr',
          h('td', stateBadge(c.ok ? 'pass' : 'fail')),
          h('td.mono', c.name),
          h('td', c.detail || ''),
          h('td.muted', c.ok ? '' : c.fix || '')))))
        : empty('No checks returned'));
    } catch (err) { mount(body, errorBox(err)); }
  }), { kind: 'ghost' });
  return card('System', h('div.row', run), body);
};

export const renderProjects = async (root) => {
  const listEl = h('div.project-list', loading());
  mount(root, h('div.page',
    h('div.page-head', h('h1', 'Projects'), h('p.muted', 'One project per language cut. Originals are symlinked, never modified.')),
    h('div.grid-2',
      card('Your reels', listEl),
      h('div.stack', card('New project', createForm()), doctorCard()))));
  const load = async () => {
    mount(listEl, loading());
    try {
      const projects = await api.projects();
      mount(listEl, projects?.length
        ? projects.map(projectRow)
        : empty('No projects yet', 'Create one to import takes and build a reel.'));
    } catch (err) { mount(listEl, errorBox(err, load)); }
  };
  load();
};
