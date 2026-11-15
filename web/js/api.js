// REST + SSE client for the Reel Studio API (DESIGN.md "Web API").

export class ApiError extends Error {
  constructor(status, message, body) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

// FastAPI errors: {detail: str | [{msg, loc}]}
const detailOf = (body, fallback) => {
  const d = body?.detail ?? body?.error ?? body?.message;
  if (Array.isArray(d)) return d.map((x) => `${(x.loc || []).slice(1).join('.')}: ${x.msg}`).join('; ');
  if (typeof d === 'string') return d;
  return fallback;
};

export const request = async (method, path, body, { raw = false } = {}) => {
  const init = { method, headers: {} };
  if (body instanceof FormData) init.body = body;
  else if (body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  let res;
  try {
    res = await fetch(`/api${path}`, init);
  } catch (err) {
    throw new ApiError(0, `Server unreachable (${err.message}). Is "reel serve" running?`);
  }
  if (raw) return res;
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = text; }
  if (!res.ok) throw new ApiError(res.status, detailOf(data, `${res.status} ${res.statusText}`), data);
  return data;
};

export const get = (path) => request('GET', path);
export const post = (path, body = {}) => request('POST', path, body);
export const put = (path, body) => request('PUT', path, body);
export const patch = (path, body) => request('PATCH', path, body);
export const del = (path) => request('DELETE', path);

// GET that maps 404 to null (artifact not produced yet).
export const getOptional = async (path) => {
  try {
    return await get(path);
  } catch (err) {
    if (err.status === 404) return null;
    throw err;
  }
};

const enc = encodeURIComponent;
const relEnc = (rel) => rel.split('/').map(enc).join('/');

// ── Endpoints ───────────────────────────────────────────────────────────────
export const api = {
  health: () => get('/health'),
  doctor: () => get('/doctor'),
  projects: () => get('/projects'),
  createProject: (name, lang, mode = 'talking_head') => post('/projects', { name, lang, mode }),
  project: (pid) => get(`/projects/${enc(pid)}`),
  patchProject: (pid, body) => patch(`/projects/${enc(pid)}`, body),
  importTakes: (pid, paths, lang, mirror) => post(`/projects/${enc(pid)}/takes/import`, { paths, lang: lang || null, mirror }),
  uploadTakes: (pid, files) => {
    const fd = new FormData();
    [...files].forEach((f) => fd.append('files', f, f.name));
    return request('POST', `/projects/${enc(pid)}/takes/upload`, fd);
  },
  patchTake: (pid, tid, body) => patch(`/projects/${enc(pid)}/takes/${enc(tid)}`, body),
  deleteTake: (pid, tid) => del(`/projects/${enc(pid)}/takes/${enc(tid)}`),
  putScript: (pid, ar_text, en_text) => put(`/projects/${enc(pid)}/script`, { ar_text, en_text }),
  run: (pid, stage) => post(`/projects/${enc(pid)}/run/${enc(stage)}`),
  jobs: () => get('/jobs'),
  job: (jid) => get(`/jobs/${enc(jid)}`),
  analysis: (pid) => getOptional(`/projects/${enc(pid)}/analysis`),
  alignment: (pid) => getOptional(`/projects/${enc(pid)}/alignment`),
  plan: (pid) => getOptional(`/projects/${enc(pid)}/plan`),
  putPlan: (pid, plan) => put(`/projects/${enc(pid)}/plan`, plan),
  choose: (pid, beat_id, attempt_index) => post(`/projects/${enc(pid)}/plan/choose`, { beat_id, attempt_index }),
  approve: (pid) => post(`/projects/${enc(pid)}/plan/approve`),
  captions: (pid) => getOptional(`/projects/${enc(pid)}/captions`),
  qa: (pid) => getOptional(`/projects/${enc(pid)}/qa`),
  reveal: (pid, relpath) => post(`/projects/${enc(pid)}/reveal`, { relpath }),
  voiceEngines: () => get('/voice/engines'),
  voiceClips: (pid) => get(`/projects/${enc(pid)}/voice`),
  generateVoice: (pid, body) => post(`/projects/${enc(pid)}/voice`, body),
  draftScript: (topic, length) => post('/script/draft', { topic, length }),
  recordSession: (pid) => get(`/projects/${enc(pid)}/record`),
};

// Media URL served with HTTP Range support (video seeking).
export const fileUrl = (pid, relpath, bust = '') =>
  `/api/projects/${enc(pid)}/files/${relEnc(relpath)}${bust ? `?v=${enc(bust)}` : ''}`;

// SSE: `event: job` + `data: Job`. EventSource reconnects on its own.
export const subscribeEvents = ({ onJob, onStatus }) => {
  const es = new EventSource('/api/events');
  es.addEventListener('job', (e) => {
    try { onJob(JSON.parse(e.data)); } catch (err) { console.warn('bad SSE payload', err); }
  });
  es.onopen = () => onStatus?.('live');
  es.onerror = () => onStatus?.(es.readyState === EventSource.CLOSED ? 'closed' : 'reconnecting');
  return () => es.close();
};
