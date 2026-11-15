// Tiny app store: one state object, shallow-merged updates, change listeners.

let state = {
  route: { view: 'projects' },
  project: null,          // current Project (workspace views)
  jobs: {},               // id -> Job (from /api/jobs + SSE)
  sse: 'connecting',      // live | reconnecting | closed | connecting
  dirty: false,           // the current view holds unsaved edits
};
const listeners = new Set();

export const getState = () => state;

export const setState = (patch) => {
  const prev = state;
  state = { ...state, ...(typeof patch === 'function' ? patch(state) : patch) };
  listeners.forEach((fn) => fn(state, prev));
};

export const subscribe = (fn) => { listeners.add(fn); return () => listeners.delete(fn); };

export const upsertJob = (job) => setState((s) => ({ jobs: { ...s.jobs, [job.id]: job } }));

const ACTIVE = new Set(['queued', 'running']);
export const isActive = (job) => ACTIVE.has(job?.state);

// Jobs of one project, newest first.
export const projectJobs = (pid) =>
  Object.values(state.jobs)
    .filter((j) => j.project_id === pid)
    .sort((a, b) => String(b.created_at).localeCompare(String(a.created_at)));

export const activeJobs = (pid) => projectJobs(pid).filter(isActive);

// Most recent job of a given kind for a project (or undefined).
export const latestJob = (pid, kinds) => {
  const set = new Set([].concat(kinds));
  return projectJobs(pid).find((j) => set.has(j.kind));
};
