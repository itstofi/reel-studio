// Project workspace: header, stage rail (Ingest -> QA), tab bar, active tab view.
import { api } from '../api.js';
import { h, mount, loading, empty, errorBox, button, stateBadge, progressBar, toast, withBusy, badge, fmtDate } from '../ui.js';
import { getState, setState, projectJobs, isActive } from '../state.js';
import { href, navigate, TABS } from '../router.js';
import { renderTakes } from './takes.js';
import { renderScript } from './script.js';
import { renderAnalysis } from './analysis.js';
import { renderPlan } from './plan.js';
import { renderRender } from './render.js';
import { renderQA } from './qa.js';
import { renderVoice } from './voice.js';

const VIEWS = {
  takes: renderTakes, script: renderScript, analysis: renderAnalysis, plan: renderPlan,
  render: renderRender, qa: renderQA, voice: renderVoice,
};
const TAB_LABELS = {
  takes: 'Takes', script: 'Script', analysis: 'Analysis', plan: 'Plan',
  render: 'Render', qa: 'QA', voice: 'Voice lab',
};
// Which finished job kinds change the data a tab shows.
const TAB_DEPS = {
  takes: ['ingest', 'prepare'], script: [], analysis: ['ingest', 'analyze', 'prepare'],
  plan: ['plan', 'prepare'], render: ['render', 'preview'], qa: ['qa'], voice: ['voice'],
};

// The rail: pipeline stages + the creator's approval gate between Plan and Render.
const RAIL = [
  { key: 'ingest', label: 'Ingest', hint: 'Probe takes, HDR detect, audio + mirrored SDR proxy' },
  { key: 'analyze', label: 'Analyze', hint: 'Speech map (-52 dB), blocks, transcripts, face box' },
  { key: 'plan', label: 'Plan', hint: 'Align to script (last clean repeat wins), gap A/B, beats' },
  { key: 'approve', label: 'Approve', hint: 'You review the beat-by-beat plan before anything renders' },
  { key: 'render', label: 'Render', hint: '4K60 HEVC hvc1 Rec.709, English captions, graphics' },
  { key: 'qa', label: 'QA', hint: 'Adversarial checks on the delivered file' },
];

let ctx = null; // { pid, tab, root, project, planInfo, railEl, tabsEl, bodyEl, onJob }

// ── Data ────────────────────────────────────────────────────────────────────
const loadPlanInfo = async (pid) => {
  try {
    const res = await api.plan(pid);
    const plan = res?.plan;
    return plan ? { exists: true, approved: !!plan.approved, approved_at: plan.approved_at, beats: plan.beats?.length ?? 0 } : { exists: false };
  } catch {
    return { exists: false, error: true };
  }
};

// Re-read the project + plan flags and redraw the rail. Returns the fresh project.
const reload = async () => {
  if (!ctx) return null;
  const { pid } = ctx;
  const [project, planInfo] = await Promise.all([api.project(pid), loadPlanInfo(pid)]);
  if (!ctx || ctx.pid !== pid) return project;
  ctx.project = project;
  ctx.planInfo = planInfo;
  setState({ project });
  renderHeaderBar();
  renderRail();
  return project;
};

// ── Rail ────────────────────────────────────────────────────────────────────
const approveState = (project, planInfo) => {
  if (!planInfo?.exists) return 'pending';
  if (project.stages?.plan?.state === 'stale') return 'stale';
  return planInfo.approved ? 'done' : 'waiting';
};

const runStage = (stage, btn) => withBusy(btn, async () => {
  const { job_id: jobId } = await api.run(ctx.pid, stage);
  toast(`${stage} queued${jobId ? ` (job ${jobId})` : ''}`, 'info');
  // Optimistic placeholder until SSE delivers the real job.
  if (jobId && !getState().jobs[jobId]) {
    setState((s) => ({ jobs: { ...s.jobs, [jobId]: { id: jobId, project_id: ctx.pid, kind: stage, state: 'queued', progress: 0, message: 'queued', created_at: new Date().toISOString() } } }));
  }
  renderRail();
});

const jobFor = (pid, kinds) => projectJobs(pid).find((j) => kinds.includes(j.kind) && isActive(j));

const stageRow = (stage, project, planInfo) => {
  const pid = project.id;
  const approved = !!planInfo?.approved;
  if (stage.key === 'approve') {
    const st = approveState(project, planInfo);
    return h('li.stage', { class: `stage-${st}` },
      h('div.stage-head', h('span.stage-dot'), h('span.stage-name', stage.label), stateBadge(st === 'waiting' ? 'waiting for you' : st)),
      h('div.stage-msg', planInfo?.approved ? `Approved ${fmtDate(planInfo.approved_at)}` : stage.hint),
      h('div.stage-actions',
        button(planInfo?.approved ? 'View plan' : 'Review plan', () => navigate(href(pid, 'plan')), { kind: st === 'waiting' ? 'primary' : 'ghost', disabled: !planInfo?.exists })));
  }
  const status = project.stages?.[stage.key] || { state: 'pending', message: '' };
  const kinds = stage.key === 'render' ? ['render', 'preview'] : [stage.key];
  const job = jobFor(pid, kinds);
  const prepJob = ['ingest', 'analyze', 'plan'].includes(stage.key) && status.state === 'running' ? jobFor(pid, ['prepare']) : null;
  const live = job || prepJob;
  const state = live ? (live.state === 'queued' && !prepJob ? 'queued' : 'running') : status.state;
  const needsApproval = (stage.key === 'render' || stage.key === 'qa') && !approved;
  const blockedTitle = needsApproval ? 'Approve the plan first — render refuses an unapproved plan' : undefined;
  return h('li.stage', { class: `stage-${state}` },
    h('div.stage-head', h('span.stage-dot'), h('span.stage-name', stage.label), stateBadge(state)),
    h('div.stage-msg', live?.message || status.message || stage.hint),
    live ? progressBar(live.progress ?? status.progress) : null,
    status.state === 'failed' && status.message ? h('div.stage-err', status.message) : null,
    h('div.stage-actions',
      stage.key === 'render'
        ? [
          button('Preview', (e) => runStage('preview', e.currentTarget), { kind: 'ghost', disabled: !!job || needsApproval, title: blockedTitle || '1080x1920/30 H.264 check render' }),
          button('Final', (e) => runStage('render', e.currentTarget), { disabled: !!job || needsApproval, title: blockedTitle || '2160x3840/60 HEVC hvc1 delivery render' }),
        ]
        : button('Run', (e) => runStage(stage.key, e.currentTarget), { disabled: !!job || (stage.key === 'qa' && !project.final_path), title: stage.key === 'qa' && !project.final_path ? 'Render the final file first' : stage.hint })));
};

const renderRail = () => {
  if (!ctx?.railEl) return;
  const { project, planInfo } = ctx;
  const prep = jobFor(project.id, ['prepare']);
  mount(ctx.railEl,
    h('div.rail-top',
      button(prep ? 'Preparing…' : 'Prepare', (e) => runStage('prepare', e.currentTarget), { kind: 'primary', disabled: !!prep || !project.takes?.length, title: project.takes?.length ? 'Ingest + Analyze + Plan, then stop for your approval' : 'Import takes first' }),
      h('span.rail-hint', 'Ingest → Analyze → Plan, then stops for your approval')),
    prep ? h('div.rail-prep', h('span', prep.message || prep.state), progressBar(prep.progress)) : null,
    h('ol.stages', RAIL.map((s) => stageRow(s, project, planInfo))),
    recentJobs(project.id));
};

const recentJobs = (pid) => {
  const jobs = projectJobs(pid).slice(0, 6);
  if (!jobs.length) return null;
  return h('details.jobs', { open: jobs.some((j) => j.state === 'failed' || isActive(j)) },
    h('summary', 'Recent jobs'),
    h('ul', jobs.map((j) => h('li.job', { class: `job-${j.state}` },
      h('div.job-head', h('span.mono', j.kind), stateBadge(j.state)),
      isActive(j) ? progressBar(j.progress) : null,
      j.message ? h('div.job-msg', j.message) : null,
      j.error ? h('pre.job-err', j.error) : null))));
};

// ── Header + tabs ───────────────────────────────────────────────────────────
const renderHeaderBar = () => {
  if (!ctx?.headEl) return;
  const p = ctx.project;
  mount(ctx.headEl,
    h('a.back', { href: '#/' }, '← Projects'),
    h('h1.ws-title', p.name),
    badge(p.settings?.lang === 'en' ? 'English cut' : 'Arabic cut', 'info'),
    badge(`${p.takes?.length || 0} takes`),
    p.final_path ? badge('final.mov', 'ok') : null);
};

const renderTabs = () => {
  if (!ctx?.tabsEl) return;
  mount(ctx.tabsEl, TABS.map((t) =>
    h('a.tab', { href: href(ctx.pid, t), class: t === ctx.tab ? 'active' : '', 'aria-current': t === ctx.tab ? 'page' : null }, TAB_LABELS[t])));
};

const renderTab = async () => {
  if (!ctx) return;
  const { tab, pid } = ctx;
  ctx.onJob = null;
  setState({ dirty: false });
  mount(ctx.bodyEl, loading());
  const view = VIEWS[tab];
  try {
    const node = await view({
      pid,
      get project() { return ctx?.project; },
      reload,
      rerender: renderTab,
      setDirty: (d) => setState({ dirty: !!d }),
      onJob: (fn) => { if (ctx) ctx.onJob = fn; },
      planInfo: () => ctx?.planInfo,
    });
    if (ctx?.tab === tab && ctx.pid === pid) mount(ctx.bodyEl, node);
  } catch (err) {
    if (ctx?.tab === tab) mount(ctx.bodyEl, errorBox(err, renderTab));
  }
};

// ── Entry ───────────────────────────────────────────────────────────────────
export const renderWorkspace = async (root, route) => {
  if (ctx && ctx.pid === route.pid && root.contains(ctx.shell)) {
    if (ctx.tab !== route.tab) { ctx.tab = route.tab; renderTabs(); renderTab(); }
    return;
  }
  ctx = null;
  mount(root, loading('Loading project…'));
  let project;
  let planInfo;
  try {
    [project, planInfo] = await Promise.all([api.project(route.pid), loadPlanInfo(route.pid)]);
  } catch (err) {
    if (err.status === 404) {
      mount(root, empty('Project not found', `No project with id "${route.pid}".`, h('a.btn', { href: '#/' }, 'All projects')));
    } else mount(root, errorBox(err, () => renderWorkspace(root, route)));
    return;
  }
  const headEl = h('div.ws-head');
  const railEl = h('aside.rail', { 'aria-label': 'Pipeline stages' });
  const tabsEl = h('nav.tabs', { 'aria-label': 'Project views' });
  const bodyEl = h('div.tab-body');
  const shell = h('div.workspace', headEl, h('div.ws-grid', railEl, h('div.ws-main', tabsEl, bodyEl)));
  ctx = { pid: route.pid, tab: route.tab, root, shell, project, planInfo, headEl, railEl, tabsEl, bodyEl, onJob: null };
  setState({ project });
  mount(root, shell);
  renderHeaderBar();
  renderRail();
  renderTabs();
  renderTab();
};

// Called by app.js for every SSE job update.
let reloadTimer = null;
export const onJobEvent = (job, prev) => {
  if (!ctx || job.project_id !== ctx.pid) return;
  renderRail();
  ctx.onJob?.(job, prev);
  const finished = (job.state === 'done' || job.state === 'failed') && prev?.state !== job.state;
  if (finished) {
    toast(job.state === 'done' ? `${job.kind} finished` : `${job.kind} failed: ${job.error || job.message || ''}`, job.state === 'done' ? 'ok' : 'bad', job.state === 'done' ? 4000 : 10000);
    const tab = ctx.tab;
    reload().then(() => {
      if (ctx?.tab === tab && TAB_DEPS[tab]?.includes(job.kind)) {
        if (getState().dirty) toast('New results are available — save or discard your edits, then reopen this tab.', 'warn', 8000);
        else renderTab();
      }
    }).catch((err) => toast(err.message, 'bad'));
  } else if (isActive(job) && !reloadTimer) {
    // Stage states change inside long jobs (prepare runs 3 stages): poll the project lightly.
    reloadTimer = setTimeout(() => { reloadTimer = null; reload().catch(() => {}); }, 1500);
  }
};
