// Entry point: router + SSE wiring. Views live in ./views/.
import { api, subscribeEvents } from './api.js';
import { h, mount, toast } from './ui.js';
import { getState, setState, subscribe, upsertJob } from './state.js';
import { onRoute } from './router.js';
import { renderProjects } from './views/projects.js';
import { renderWorkspace, onJobEvent } from './views/workspace.js';
import { renderRecord, leaveRecord } from './views/record.js';

const main = document.getElementById('main');
const sseDot = document.getElementById('sse-status');
const jobsBadge = document.getElementById('jobs-active');
const versionEl = document.getElementById('version');

// Surface unexpected errors instead of failing silently.
window.addEventListener('error', (e) => toast(`UI error: ${e.message}`, 'bad', 8000));
window.addEventListener('unhandledrejection', (e) => toast(`UI error: ${e.reason?.message || e.reason}`, 'bad', 8000));

// Warn before navigating away from unsaved edits.
window.addEventListener('beforeunload', (e) => {
  if (getState().dirty) { e.preventDefault(); e.returnValue = ''; }
});

const renderHeader = (s) => {
  sseDot.className = `sse sse-${s.sse}`;
  sseDot.title = `Live updates: ${s.sse}`;
  const n = Object.values(s.jobs).filter((j) => j.state === 'running' || j.state === 'queued').length;
  jobsBadge.textContent = n ? `${n} job${n > 1 ? 's' : ''} running` : '';
  jobsBadge.hidden = !n;
};
subscribe(renderHeader);

let lastRoute = null;
const route = (r) => {
  const prev = lastRoute;
  const sameProject = prev && prev.view === 'workspace' && r.view === 'workspace' && prev.pid === r.pid;
  if (getState().dirty && !(sameProject && prev.tab === r.tab)) {
    // Unsaved edits: confirm before leaving the view.
    if (!confirm('You have unsaved changes. Leave this view and discard them?')) {
      history.replaceState(null, '', prev ? (prev.view === 'workspace' ? `#/p/${encodeURIComponent(prev.pid)}/${prev.tab}` : '#/') : '#/');
      return;
    }
    setState({ dirty: false });
  }
  lastRoute = r;
  setState({ route: r });
  document.querySelectorAll('.nav a').forEach((a) => a.classList.toggle('active', a.dataset.view === r.view));
  leaveRecord();
  main.classList.remove('page-rec');
  if (r.view === 'record') renderRecord(main, r);
  else if (r.view === 'workspace') renderWorkspace(main, r);
  else { setState({ project: null }); renderProjects(main); }
};

const boot = async () => {
  mount(main, h('div.loading', 'Loading…'));
  try {
    const health = await api.health();
    versionEl.textContent = health?.version ? `v${health.version}` : '';
  } catch (err) {
    versionEl.textContent = 'offline';
    toast(err.message, 'bad', 8000);
  }
  try {
    const jobs = await api.jobs();
    setState({ jobs: Object.fromEntries((jobs || []).map((j) => [j.id, j])) });
  } catch { /* the jobs list is optional at boot */ }
  subscribeEvents({
    onJob: (job) => {
      const prev = getState().jobs[job.id];
      upsertJob(job);
      onJobEvent(job, prev);
    },
    onStatus: (sse) => setState({ sse }),
  });
  onRoute(route);
};

boot();
