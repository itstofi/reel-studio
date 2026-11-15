// Helpers shared by several views: proxy video player, segment playback.
import { fileUrl } from '../api.js';
import { h } from '../ui.js';

export const STAGE_ORDER = ['ingest', 'analyze', 'plan', 'render', 'qa'];

// Relative path of a take's proxy inside the project (store.ProjectPaths.proxy).
export const proxyRel = (takeId) => `work/${takeId}/proxy.mp4`;

export const takeHasProxy = (project, takeId) => {
  const t = project?.takes?.find((x) => x.id === takeId);
  return !!t?.proxy_path;
};

// A <video> that can play a list of [take, in, out] ranges back to back from the proxies.
// Returns { el, playRanges(ranges, label), stop() }.
export const segmentPlayer = (pid) => {
  const video = h('video.player', { controls: true, preload: 'metadata', playsInline: true, muted: false });
  const label = h('div.player-label.muted', 'Press ▶ on a beat to play its segments from the proxy.');
  let queue = [];
  let current = null;

  const stop = () => { queue = []; current = null; video.pause(); };

  const playNext = () => {
    current = queue.shift() || null;
    if (!current) { video.pause(); return; }
    const src = fileUrl(pid, proxyRel(current.take_id));
    const start = () => {
      video.currentTime = current.src_in;
      video.play().catch((err) => { label.textContent = `Playback blocked: ${err.message}`; });
    };
    if (!video.src.endsWith(src)) {
      video.src = src;
      video.addEventListener('loadedmetadata', start, { once: true });
    } else start();
  };

  video.addEventListener('timeupdate', () => {
    if (current && video.currentTime >= current.src_out) playNext();
  });
  video.addEventListener('error', () => {
    label.textContent = 'Could not load the proxy — run Ingest first (proxy.mp4 missing or unreadable).';
  });

  const playRanges = (ranges, text = '') => {
    stop();
    queue = ranges.slice();
    label.textContent = text;
    playNext();
  };
  return { el: h('div.player-wrap', video, label), video, playRanges, stop };
};
