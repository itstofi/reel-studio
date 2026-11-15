// Small DOM + formatting helpers shared by every view (no framework).

// Hyperscript: h('div.card#id', {onclick, class, style, ...}, ...children)
export const h = (sel, attrs = {}, ...children) => {
  if (attrs === null || typeof attrs !== 'object' || attrs instanceof Node || Array.isArray(attrs)) {
    children.unshift(attrs);
    attrs = {};
  }
  const [tag, ...rest] = sel.split(/(?=[.#])/);
  const el = document.createElement(tag || 'div');
  rest.forEach((part) => {
    if (part[0] === '.') el.classList.add(part.slice(1));
    else if (part[0] === '#') el.id = part.slice(1);
  });
  Object.entries(attrs).forEach(([k, v]) => {
    if (v === undefined || v === null || v === false) return;
    if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
    else if (k === 'class') el.className += (el.className ? ' ' : '') + v;
    else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
    else if (k === 'dataset') Object.assign(el.dataset, v);
    else if (k in el && typeof v !== 'string') el[k] = v; // value, checked, disabled, ...
    else el.setAttribute(k, v === true ? '' : v);
  });
  append(el, children);
  return el;
};

const append = (el, children) => {
  children.flat(Infinity).forEach((c) => {
    if (c === null || c === undefined || c === false) return;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  });
};

export const clear = (el) => { el.replaceChildren(); return el; };
export const mount = (el, ...children) => { el.replaceChildren(); append(el, children); return el; };

// ── Formatting ──────────────────────────────────────────────────────────────
export const fmtTime = (s) => {
  if (s === null || s === undefined || Number.isNaN(s)) return '–';
  const m = Math.floor(s / 60);
  const sec = s - m * 60;
  return `${m}:${sec.toFixed(2).padStart(5, '0')}`;
};
export const fmtSec = (s, d = 2) => (s === null || s === undefined ? '–' : `${Number(s).toFixed(d)}s`);
export const fmtPct = (x) => (x === null || x === undefined ? '–' : `${Math.round(x * 100)}%`);
export const fmtBytes = (n) => {
  if (!n) return '–';
  const units = ['B', 'KB', 'MB', 'GB'];
  const i = Math.min(units.length - 1, Math.floor(Math.log(n) / Math.log(1024)));
  return `${(n / 1024 ** i).toFixed(i ? 1 : 0)} ${units[i]}`;
};
export const fmtDate = (iso) => {
  if (!iso) return '–';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
};

// ── Small components ────────────────────────────────────────────────────────
export const badge = (text, kind = '') => h('span.badge', { class: kind ? `badge-${kind}` : '' }, text);

export const stateBadge = (state) => badge(state, {
  done: 'ok', pass: 'ok', running: 'run', queued: 'run', failed: 'bad', fail: 'bad',
  stale: 'warn', warn: 'warn', pending: '', skip: '',
}[state] ?? '');

export const button = (label, onclick, opts = {}) =>
  h('button.btn', { type: 'button', onclick, class: opts.kind ? `btn-${opts.kind}` : '', disabled: !!opts.disabled, title: opts.title }, label);

export const spinner = () => h('span.spinner', { 'aria-hidden': 'true' });

export const loading = (text = 'Loading…') => h('div.loading', spinner(), text);

export const empty = (title, text = '', ...actions) =>
  h('div.empty', h('h3', title), text ? h('p', text) : null, actions.length ? h('div.row', actions) : null);

export const errorBox = (err, retry) =>
  h('div.alert.alert-bad', { role: 'alert' },
    h('strong', err?.status ? `Error ${err.status}` : 'Error'),
    h('span', ` ${err?.message || String(err)}`),
    retry ? button('Retry', retry, { kind: 'ghost' }) : null);

export const alertBox = (kind, ...children) => h('div.alert', { class: `alert-${kind}`, role: 'status' }, children);

export const progressBar = (p, label = '') =>
  h('div.progress', { title: label },
    h('div.progress-fill', { style: { width: `${Math.round(Math.max(0, Math.min(1, p || 0)) * 100)}%` } }));

export const card = (title, ...children) =>
  h('section.card', title ? h('h2.card-title', title) : null, children);

export const field = (label, control, hint = '') =>
  h('label.field', h('span.field-label', label), control, hint ? h('span.field-hint', hint) : null);

export const select = (options, value, onchange, attrs = {}) =>
  h('select', { onchange: (e) => onchange(e.target.value), ...attrs },
    options.map((o) => {
      const [v, label, disabled] = Array.isArray(o) ? o : [o, o, false];
      return h('option', { value: v, selected: String(v) === String(value), disabled: !!disabled }, label);
    }));

export const toggle = (checked, onchange, label) =>
  h('label.toggle',
    h('input', { type: 'checkbox', checked: !!checked, onchange: (e) => onchange(e.target.checked, e) }),
    h('span.toggle-track'), label ? h('span', label) : null);

// ── Toasts ──────────────────────────────────────────────────────────────────
export const toast = (message, kind = 'info', ms = 4500) => {
  const host = document.getElementById('toasts');
  if (!host) return;
  const el = h('div.toast', { class: `toast-${kind}`, role: 'status' }, message);
  host.append(el);
  setTimeout(() => el.remove(), ms);
};

// Run an async action with a busy button + error toast; returns the result or undefined.
export const withBusy = async (btn, fn) => {
  const prev = btn?.textContent;
  if (btn) { btn.disabled = true; btn.classList.add('busy'); }
  try {
    return await fn();
  } catch (err) {
    toast(err?.message || String(err), 'bad', 7000);
    return undefined;
  } finally {
    if (btn) { btn.disabled = false; btn.classList.remove('busy'); btn.textContent = prev; }
  }
};

// Absolute path of a project file -> path relative to the project folder (for /files/).
export const projectRel = (pid, absPath) => {
  if (!absPath) return null;
  const marker = `/${pid}/`;
  const i = absPath.lastIndexOf(marker);
  return i >= 0 ? absPath.slice(i + marker.length) : absPath.replace(/^\/+/, '');
};
