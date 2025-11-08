(function () {
  const toInt = (v) => {
    const n = Math.round(Number(v) || 0);
    return Number.isFinite(n) ? n : 0;
  };

  function isInViewportRect(r) {
    const vw = Math.max(document.documentElement?.clientWidth || 0, window.innerWidth || 0);
    const vh = Math.max(document.documentElement?.clientHeight || 0, window.innerHeight || 0);
    return r.bottom > 0 && r.right > 0 && r.top < vh && r.left < vw;
  }

  function isVisibleAdvanced(e, r, cs) {
    if (!r || r.width <= 0 || r.height <= 0) return false;
    if (!cs) cs = getComputedStyle(e);
    if (cs.display === 'none' || cs.visibility === 'hidden' || cs.opacity === '0') return false;
    if (e.getAttribute && e.getAttribute('aria-hidden') === 'true') return false;
    // pointer-events:none elements usually not interactable
    if (cs.pointerEvents === 'none') return false;
    return true;
  }

  function occlusionRatio(e, r, step = 8) {
    // Sample grid points in the element rect and test if elementFromPoint hits e (or its descendant)
    if (!r || r.width <= 0 || r.height <= 0) return 1.0;
    const vw = Math.max(document.documentElement?.clientWidth || 0, window.innerWidth || 0);
    const vh = Math.max(document.documentElement?.clientHeight || 0, window.innerHeight || 0);
    const cols = Math.max(1, Math.floor(r.width / step));
    const rows = Math.max(1, Math.floor(r.height / step));
    let total = 0, hit = 0;
    for (let i = 0; i <= cols; i++) {
      for (let j = 0; j <= rows; j++) {
        const x = Math.min(vw - 1, Math.max(0, r.left + (i / Math.max(1, cols)) * r.width));
        const y = Math.min(vh - 1, Math.max(0, r.top + (j / Math.max(1, rows)) * r.height));
        if (x < 0 || y < 0 || x >= vw || y >= vh) continue;
        total++;
        const top = document.elementFromPoint(x, y);
        if (top && (top === e || e.contains(top))) hit++;
      }
    }
    if (total === 0) return 1.0;
    const visibleRatio = hit / total;
    return 1 - visibleRatio; // occluded portion
  }

  function getInteractiveScore(e, tag, role) {
    // Rough heuristic to help downstream candidate generation
    tag = (tag || '').toLowerCase();
    role = (role || '').toLowerCase();
    let score = 0;
    const clickableTags = new Set(['button', 'a', 'input']);
    const inputTypesClick = new Set(['button', 'submit', 'image', 'reset']);
    if (clickableTags.has(tag)) score += 0.5;
    if (role && /(button|link|textbox|checkbox|radio|combobox)/i.test(role)) score += 0.5;
    if (tag === 'input') {
      const t = (e.getAttribute('type') || '').toLowerCase();
      if (t && inputTypesClick.has(t)) score += 0.2; else score += 0.1;
    }
    if (e.hasAttribute && e.hasAttribute('onclick')) score += 0.2;
    return Math.min(1, score);
  }

  function getLabels(e) {
    // Associated label text for inputs
    const labels = [];
    if (e.labels && e.labels.length) {
      for (const l of e.labels) labels.push((l.innerText || '').trim());
    }
    const ariaLabel = e.getAttribute && e.getAttribute('aria-label');
    if (ariaLabel) labels.push(ariaLabel);
    const title = e.getAttribute && e.getAttribute('title');
    if (title) labels.push(title);
    return labels.filter(Boolean);
  }

  function getDomSummary(limit = 20000) {
    const nodes = Array.from(document.querySelectorAll('*'));
    const result = [];
    const clamp = (s, n = 160) => (s || '').slice(0, n);
    for (let i = 0; i < nodes.length; i++) {
      const e = nodes[i];
      const r = e.getBoundingClientRect();
      const cs = getComputedStyle(e);
      const aria = {};
      for (const attr of e.getAttributeNames()) {
        if (attr.startsWith('aria-')) aria[attr] = e.getAttribute(attr);
      }
      const visible = (r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none');
      result.push({
        index: i,
        tag: (e.tagName || '').toLowerCase(),
        id: e.id || null,
        class: e.className || null,
        role: e.getAttribute('role') || null,
        name: e.getAttribute('name') || null,
        aria,
        bbox: [toInt(r.x), toInt(r.y), toInt(r.width), toInt(r.height)],
        visible,
        text: clamp(e.innerText)
      });
      if (result.length >= limit) break;
    }
    return result;
  }

  function getDomSummaryAdvanced(limit = 20000, options = {}) {
    const nodes = Array.from(document.querySelectorAll('*'));
    const result = [];
    const clamp = (s, n = 160) => (s || '').slice(0, n);
    const occStep = toInt(options.occlusionStep || 8);
    const getParentIndex = (e) => {
      const p = e.parentElement;
      if (!p) return null;
      const idx = nodes.indexOf(p);
      return idx >= 0 ? idx : null;
    };
    const parseBorderRadius = (borderRadius) => {
      if (!borderRadius) return 0;
      // border-radius 可能为 "8px" 或 "8px 8px / 8px 8px" 等形式
      const nums = (borderRadius.match(/\d+(?:\.\d+)?/g) || []).map(Number);
      if (!nums.length) return 0;
      const sum = nums.reduce((a, b) => a + (isFinite(b) ? b : 0), 0);
      const avg = sum / Math.max(1, nums.length);
      return isFinite(avg) ? avg : 0;
    };
    for (let i = 0; i < nodes.length; i++) {
      const e = nodes[i];
      const r = e.getBoundingClientRect();
      const cs = getComputedStyle(e);
      const aria = {};
      for (const attr of e.getAttributeNames()) {
        if (attr.startsWith('aria-')) aria[attr] = e.getAttribute(attr);
      }
      const basicVisible = (r.width > 0 && r.height > 0 && cs.visibility !== 'hidden' && cs.display !== 'none');
      const advVisible = isVisibleAdvanced(e, r, cs);
      const inViewport = isInViewportRect(r);
      const occ = inViewport && advVisible ? occlusionRatio(e, r, occStep) : 1.0;
      const labels = getLabels(e);
      const tag = (e.tagName || '').toLowerCase();
      const role = e.getAttribute('role') || null;
      const br = parseBorderRadius(cs.borderRadius || '');
      const isControl = (() => {
        // 控件启发式：标签/角色/交互得分
        const interactive = getInteractiveScore(e, tag, role);
        if (interactive >= 0.5) return true;
        if (tag === 'button' || tag === 'input' || tag === 'select' || tag === 'textarea' || tag === 'a') return true;
        if (role && /(button|link|textbox|checkbox|radio|combobox)/i.test(role)) return true;
        // clickable class hint
        if ((e.className || '').toString().toLowerCase().includes('btn')) return true;
        return false;
      })();
      result.push({
        index: i,
        tag,
        id: e.id || null,
        class: e.className || null,
        role,
        name: e.getAttribute('name') || null,
        aria,
        bbox: [toInt(r.x), toInt(r.y), toInt(r.width), toInt(r.height)],
        visible: basicVisible,
        visible_adv: advVisible,
        in_viewport: inViewport,
        occlusion_ratio: Number.isFinite(occ) ? Number(occ.toFixed(3)) : 1.0,
        z_index: cs.zIndex || null,
        opacity: cs.opacity || null,
        pointer_events: cs.pointerEvents || null,
        text: clamp(e.innerText),
        labels,
        interactive_score: getInteractiveScore(e, tag, role),
        parent_index: getParentIndex(e),
        border_radius: br,
        is_control: isControl
      });
      if (result.length >= limit) break;
    }
    return result;
  }

  function getNavigationTiming() {
    const nav = performance.getEntriesByType('navigation')[0];
    if (nav) return nav.toJSON ? nav.toJSON() : nav;
    const t = performance.timing;
    return t ? JSON.parse(JSON.stringify(t)) : {};
  }

  function getDocMetrics() {
    const scrollHeight = Math.max(
      document.body?.scrollHeight || 0,
      document.documentElement?.scrollHeight || 0
    );
    const clientHeight = Math.max(
      document.documentElement?.clientHeight || 0,
      window.innerHeight || 0
    );
    return { scrollHeight, clientHeight };
  }

  function getUserAgent() {
    return navigator.userAgent || '';
  }

  function scrollStep() {
    const y = window.scrollY || window.pageYOffset || 0;
    const h = window.innerHeight || 0;
    const sh = Math.max(
      document.body?.scrollHeight || 0,
      document.documentElement?.scrollHeight || 0
    );
    if (y + h >= sh - 2) return true;
    window.scrollBy(0, Math.max(64, Math.floor(h * 0.9)));
    return false;
  }

  // ===== 容器感知（用于长截图，有限额保护的上层逻辑在 Python） =====
  function cssPath(el, maxDepth = 6) {
    if (!el) return '';
    if (el.id) return `#${el.id}`;
    const parts = [];
    let e = el;
    for (let depth = 0; e && depth < maxDepth; depth++) {
      let seg = (e.tagName || '').toLowerCase();
      if (!seg) break;
      let i = 1, sib = e;
      while ((sib = sib.previousElementSibling)) {
        if (sib.tagName === e.tagName) i++;
      }
      seg += `:nth-of-type(${i})`;
      parts.unshift(seg);
      if (e.id) { parts[0] = `#${e.id}`; break; }
      e = e.parentElement;
      if (e && (e.tagName === 'BODY' || e.tagName === 'HTML')) break;
    }
    return parts.join(' > ');
  }

  function findMainScrollContainer() {
    let best = null;
    let bestScore = 0;
    const nodes = Array.from(document.querySelectorAll('*'));
    for (const e of nodes) {
      if (!e || e === document.body || e === document.documentElement) continue;
      const cs = getComputedStyle(e);
      const oy = cs.overflowY;
      if (!(oy === 'auto' || oy === 'scroll')) continue;
      const sh = e.scrollHeight || 0;
      const ch = e.clientHeight || 0;
      if (sh <= ch + 50) continue;
      const rect = e.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0) continue;
      const sizeScore = Math.min(rect.height / (window.innerHeight || 1), 1);
      const score = sh * (1 + 0.1 * sizeScore);
      if (score > bestScore) {
        bestScore = score;
        best = { selector: cssPath(e, 6), scrollHeight: sh, clientHeight: ch, overflowY: oy };
      }
    }
    return best;
  }

  function scrollContainerTo(selector, top) {
    const e = document.querySelector(selector);
    if (!e) return false;
    const maxTop = Math.max(0, (e.scrollHeight || 0) - (e.clientHeight || 0));
    e.scrollTop = Math.max(0, Math.min(maxTop, top|0));
    return Math.abs(e.scrollTop - maxTop) < 2;
  }

  function getContainerMetrics(selector) {
    const e = document.querySelector(selector);
    if (!e) return null;
    return {
      scrollHeight: e.scrollHeight || 0,
      clientHeight: e.clientHeight || 0,
      scrollTop: e.scrollTop || 0,
    };
  }


  window.DetectHelpers = {
    getDomSummary,
    getDomSummaryAdvanced,
    getNavigationTiming,
    getDocMetrics,
    getUserAgent,
    scrollStep,
    // container-aware helpers
    findMainScrollContainer,
    scrollContainerTo,
    getContainerMetrics,
  };
})();
