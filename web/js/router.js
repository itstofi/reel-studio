// Hash router: #/ (projects), #/p/<pid>/<tab>

export const TABS = ['takes', 'script', 'analysis', 'plan', 'render', 'qa', 'voice'];

export const parseHash = (hash = location.hash) => {
  const parts = hash.replace(/^#\/?/, '').split('/').filter(Boolean).map(decodeURIComponent);
  if (parts[0] === 'record' && parts[1]) return { view: 'record', pid: parts[1] };
  if (parts[0] === 'p' && parts[1]) {
    const tab = TABS.includes(parts[2]) ? parts[2] : 'takes';
    return { view: 'workspace', pid: parts[1], tab };
  }
  return { view: 'projects' };
};

export const href = (pid, tab) => (pid ? `#/p/${encodeURIComponent(pid)}/${tab || 'takes'}` : '#/');

export const navigate = (hashHref) => { location.hash = hashHref; };

export const onRoute = (fn) => {
  const fire = () => fn(parseHash());
  window.addEventListener('hashchange', fire);
  fire();
};
