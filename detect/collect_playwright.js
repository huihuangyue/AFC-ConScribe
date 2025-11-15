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
      if (!e || typeof e.getBoundingClientRect !== 'function') continue;
      const r = e.getBoundingClientRect && e.getBoundingClientRect();
      if (!r || typeof r.width === 'undefined' || typeof r.height === 'undefined') continue;
      const cs = getComputedStyle(e);
      const aria = {};
      try {
        const attrs = (typeof e.getAttributeNames === 'function') ? e.getAttributeNames() : [];
        for (const attr of attrs) {
          if (attr && attr.startsWith('aria-')) aria[attr] = e.getAttribute(attr);
        }
      } catch (_) {}
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
        page_bbox: [
          toInt(r.x + (window.scrollX || window.pageXOffset || 0)),
          toInt(r.y + (window.scrollY || window.pageYOffset || 0)),
          toInt(r.width),
          toInt(r.height)
        ],
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
      if (!e || typeof e.getBoundingClientRect !== 'function') continue;
      const r = e.getBoundingClientRect && e.getBoundingClientRect();
      if (!r || typeof r.width === 'undefined' || typeof r.height === 'undefined') continue;
      const cs = getComputedStyle(e);
      const aria = {};
      try {
        const attrs = (typeof e.getAttributeNames === 'function') ? e.getAttributeNames() : [];
        for (const attr of attrs) {
          if (attr && attr.startsWith('aria-')) aria[attr] = e.getAttribute(attr);
        }
      } catch (_) {}
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
        page_bbox: [
          toInt(r.x + (window.scrollX || window.pageXOffset || 0)),
          toInt(r.y + (window.scrollY || window.pageYOffset || 0)),
          toInt(r.width),
          toInt(r.height)
        ],
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

  // ===== 背景图（CSS background-image）就绪检测与预加载 =====
  function parseCssUrls(bg) {
    if (!bg) return [];
    // 支持多个背景：url("...") , url('...') , url(...)，过滤非 url()（如渐变）
    const urls = [];
    const re = /url\((?:\"([^\"]+)\"|'([^']+)'|([^\)]+))\)/g;
    let m;
    while ((m = re.exec(bg)) !== null) {
      const u = (m[1] || m[2] || m[3] || '').trim();
      if (u && !u.startsWith('data:')) urls.push(u);
    }
    return urls;
  }

  function getVisibleBackgroundImageUrls(limit = 64) {
    const vw = Math.max(document.documentElement?.clientWidth || 0, window.innerWidth || 0);
    const vh = Math.max(document.documentElement?.clientHeight || 0, window.innerHeight || 0);
    const nodes = Array.from(document.querySelectorAll('*'));
    const urls = [];
    const seen = new Set();
    for (let i = 0; i < nodes.length && urls.length < limit; i++) {
      const e = nodes[i];
      if (!e || typeof e.getBoundingClientRect !== 'function') continue;
      const r = e.getBoundingClientRect && e.getBoundingClientRect();
      if (!r || typeof r.width === 'undefined' || typeof r.height === 'undefined') continue;
      if (!(r.width > 0 && r.height > 0)) continue;
      if (!(r.bottom > 0 && r.right > 0 && r.top < vh && r.left < vw)) continue;
      const cs = getComputedStyle(e);
      const bg = cs.backgroundImage || '';
      if (!bg || bg === 'none') continue;
      for (const u of parseCssUrls(bg)) {
        if (!seen.has(u)) { seen.add(u); urls.push(u); }
        if (urls.length >= limit) break;
      }
    }
    for (const el of [document.body, document.documentElement]) {
      if (!el) continue;
      const cs = getComputedStyle(el);
      const bg = cs.backgroundImage || '';
      for (const u of parseCssUrls(bg)) { if (!seen.has(u)) { seen.add(u); urls.push(u); } }
    }
    return urls;
  }

  function preloadImage(url) {
    return new Promise((resolve) => {
      try {
        const img = new Image();
        img.onload = () => resolve(true);
        img.onerror = () => resolve(false);
        try { if (img.decode) img.decode().catch(()=>{}); } catch(_) {}
        img.src = url;
      } catch (_) {
        resolve(false);
      }
    });
  }

  async function waitViewportBackgrounds(limit = 64, timeoutMs = 10000) {
    const urls = getVisibleBackgroundImageUrls(limit);
    if (!urls.length) return true;
    const to = Math.max(1, Number(timeoutMs) || 1);
    let timedOut = false;
    const timer = setTimeout(() => { timedOut = true; }, to);
    try {
      await Promise.all(urls.map(u => preloadImage(u)));
    } catch (_) {
      // ignore
    } finally {
      clearTimeout(timer);
    }
    return !timedOut || true;
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
      // 仅考虑接近“主视口”的容器：宽/高需覆盖视口的大部分，避免误选水平条、卡片滑动容器等
      const vw = Math.max(document.documentElement?.clientWidth || 0, window.innerWidth || 0);
      const vh = Math.max(document.documentElement?.clientHeight || 0, window.innerHeight || 0);
      if (rect.width < vw * 0.7 || rect.height < vh * 0.6) continue;
      const sizeScore = Math.min(rect.height / (vh || 1), 1);
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
    getVisibleBackgroundImageUrls,
    waitViewportBackgrounds,
    scrollStep,
    // container-aware helpers
    findMainScrollContainer,
    scrollContainerTo,
    getContainerMetrics,
    // --- injected helpers (lightweight, safe-return) ---
    annotateControls: async function(opts){
      try{
        const setDomIdAttr = !!(opts && opts.setDomIdAttr);
        const enableProbe = !!(opts && opts.enableProbe);
        const probeMax = Math.max(0, Number(opts && opts.probeMax) || 30);
        const probeWaitMs = Math.max(0, Number(opts && opts.probeWaitMs) || 200);
        const noNone = !!(opts && opts.noNone);
        const nodes = Array.from(document.querySelectorAll('*'));
        let count = 0;
        const inViewport = (el) => {
          const r = el.getBoundingClientRect && el.getBoundingClientRect();
          if (!r) return false;
          return isInViewportRect(r) && isVisibleAdvanced(el, r);
        };
        const styleOf = (el) => (el && el.ownerDocument && el.ownerDocument.defaultView) ? el.ownerDocument.defaultView.getComputedStyle(el) : getComputedStyle(el);
        const isFill = (el) => {
          const tag = (el.tagName||'').toLowerCase();
          const role = (el.getAttribute('role')||'').toLowerCase();
          if (tag==='input' || tag==='textarea') return true;
          if (role==='textbox' || role==='combobox') return true;
          if (el.isContentEditable) return true;
          if (tag==='label' && el.getAttribute('for')) return true; // 作为代理
          return false;
        };
        const isClick = (el) => {
          const tag = (el.tagName||'').toLowerCase();
          const role = (el.getAttribute('role')||'').toLowerCase();
          const it = (el.getAttribute('type')||'').toLowerCase();
          if (tag==='button') return true;
          if (tag==='input' && ['button','submit','reset','image'].includes(it)) return true;
          if (role==='button') return true;
          if (tag==='a') return true;
          if (el.hasAttribute && el.hasAttribute('onclick')) return true;
          const cs = styleOf(el); if (cs && String(cs.cursor||'').toLowerCase()==='pointer') return true;
          const tabindex = Number(el.getAttribute && el.getAttribute('tabindex')); if (!Number.isNaN(tabindex) && tabindex>=0) return true;
          return false;
        };
        const isHoverCandidate = (el) => {
          const role = (el.getAttribute('role')||'').toLowerCase();
          if (el.getAttribute('aria-haspopup')==='true') return true;
          const cls = (el.className||'').toLowerCase();
          if (/dropdown|tooltip|menu|hoverable|popover/.test(cls)) return true;
          if (/(menubar|menu|navigation)/.test(role)) return true;
          return false;
        };
        const setAttrs = (el, i, primary, reason, conf, extra={}) => {
          try{
            const domId = el.getAttribute && el.getAttribute('id') || '';
            if (setDomIdAttr) el.setAttribute('__selectorid', 'd'+i);
            if (domId) el.setAttribute('__domid', domId);
            if (primary || !noNone) el.setAttribute('__actiontype', primary||'none');
            if (conf!=null) el.setAttribute('__act_confidence', String(conf));
            if (reason) el.setAttribute('__act_reason', String(reason));
            if (extra && typeof extra==='object'){
              if (extra.mightNavigate!=null) el.setAttribute('__might_navigate', String(!!extra.mightNavigate));
            }
            count++;
          }catch(_){ }
        };
        const safeWait = (ms)=>new Promise(r=>setTimeout(r, Math.max(0,ms||0)));
        // popup-like 检测
        const popupLikeCount = () => {
          const sel = [
            '[role="dialog"]','[role="listbox"]','[role="tooltip"]','[role="menu"]','[role="grid"]',
            '.dropdown','.menu','.tooltip','.calendar','.datepicker','.panel','.popup','.popover','.hs_hot-city-picker', '.hs_star-choice', '.hotel-search-box-roomguest-choice'
          ].join(',');
          return document.querySelectorAll(sel).length;
        };
        const preventOnce = () => {
          const handler = (ev)=>{ try{ ev.preventDefault(); ev.stopPropagation(); }catch(_){ } };
          document.addEventListener('click', handler, true);
          return () => { try{ document.removeEventListener('click', handler, true); }catch(_){ } };
        };
        let probed = 0;
        for (let i=0;i<nodes.length;i++){
          const el = nodes[i];
          try{
            if (!el || typeof el.getBoundingClientRect!=='function') continue;
            if (!inViewport(el)) continue;
            // 静态判定
            const tag = (el.tagName||'').toLowerCase();
            const role = (el.getAttribute('role')||'').toLowerCase();
            const href = (el.getAttribute('href')||'');
            const primaryStatic = (isFill(el) ? 'fill' : (isClick(el) ? 'click' : (isHoverCandidate(el) ? 'hover' : null)));
            if (primaryStatic==='fill'){
              setAttrs(el, i, 'fill', tag==='label'?'label-for':'static-fill', 0.9, {mightNavigate:false});
              continue;
            }
            if (primaryStatic==='click'){
              const mightNav = (tag==='a' && href && !/^\s*#/.test(href) && !/^\s*javascript:/i.test(href));
              setAttrs(el, i, 'click', 'static-click', mightNav?0.8:0.7, {mightNavigate: mightNav});
              continue;
            }
            // 需要探针或 hover 候选
            let decided = false;
            if (enableProbe && probed < probeMax){
              probed++;
              const before = popupLikeCount();
              // hover 探针
              try{ el.dispatchEvent(new MouseEvent('mouseover',{bubbles:true})); }catch(_){ }
              try{ el.dispatchEvent(new MouseEvent('mouseenter',{bubbles:true})); }catch(_){ }
              try{ el.focus && el.focus(); }catch(_){ }
              await safeWait(probeWaitMs);
              const afterHover = popupLikeCount();
              if (afterHover > before){
                setAttrs(el, i, 'hover', 'hover_probe_popup', 0.8, {mightNavigate:false});
                decided = true;
              }
              if (!decided){
                // click 探针（阻断默认）
                const undo = preventOnce();
                try{ el.click && el.click(); }catch(_){ }
                await safeWait(probeWaitMs);
                const afterClick = popupLikeCount();
                try{ undo && undo(); }catch(_){ }
                if (afterClick > before){
                  setAttrs(el, i, 'click', 'click_probe_popup', 0.8, {mightNavigate:false});
                  decided = true;
                }
              }
            }
            if (!decided){
              // 兜底：有点击信号则 click，否则 none/跳过
              const cs = styleOf(el);
              const pointer = cs && String(cs.cursor||'').toLowerCase()==='pointer';
              const tabindex = Number(el.getAttribute && el.getAttribute('tabindex'));
              if (pointer || (!Number.isNaN(tabindex) && tabindex>=0) || el.hasAttribute('onclick')){
                setAttrs(el, i, 'click', 'fallback-click', 0.6, {mightNavigate:false});
              }else{
                if (!noNone) setAttrs(el, i, 'none', 'uncertain', 0.1, {mightNavigate:false});
              }
            }
          }catch(_){ /* ignore */ }
        }
        return { ok:true, count };
      }catch(e){
        return { ok:false, error: String(e), count:0 };
      }
    },
    revealInteractively: function(opts){
      try{
        const maxActions = Math.max(0, Number(opts && opts.maxActions) || 8);
        const totalBudgetMs = Math.max(0, Number(opts && opts.totalBudgetMs) || 15000);
        const waitMs = Math.max(0, Number(opts && opts.waitMs) || 800);
        const steps = [];
        let actions = 0;
        const t0 = Date.now();
        const rafFrames = (n)=>new Promise(r=>{ let i=0; const step=()=>{ i++; if(i>=Math.max(1,n||1)) return r(true); requestAnimationFrame(step); }; requestAnimationFrame(step); });
        const nodes = Array.from(document.querySelectorAll('[__actiontype]'));
        let navDetected = false;
        for (const el of nodes){
          if (actions>=maxActions) break;
          if ((Date.now()-t0)>=totalBudgetMs) break;
          let ok=false; let navigated=false; let sel=null; let act=null;
          try{
            act = (el.getAttribute('__actiontype')||'').toLowerCase();
            const sid = (el.getAttribute('__selectorid')||'');
            sel = sid ? `[__selectorid="${sid}"]` : null;
            const before = String(location.href);
            if (act==='click' || act==='toggle' || act==='select'){
              el.click(); ok=true; actions++;
            } else if (act==='type'){
              el.focus(); try{ document.execCommand('insertText', false, 'a'); }catch(_){ /* noop */ }
              ok=true; actions++;
            } else if (act==='navigate'){
              // Avoid real navigation: hover + focus only
              try{ el.dispatchEvent(new MouseEvent('mouseover', {bubbles:true})); }catch(_){ }
              try{ el.focus(); }catch(_){ }
              ok=true; actions++;
            }
            // wait a little for UI
            try{ /* two frames */ }catch(_){ }
            // eslint-disable-next-line no-unused-expressions
            rafFrames && rafFrames(2);
            try{ /* fixed wait */ }catch(_){ }
            const p = new Promise(r=>setTimeout(r, waitMs));
            try{ p && (void 0); }catch(_){ }
            // detect nav
            const after = String(location.href);
            if (after !== before) navigated = true;
          }catch(_){ ok=false; }
          steps.push({ sel, act, ok, navigated });
          if (navigated) { navDetected = true; break; }
        }
        return { ok:true, actions, steps, navigated: navDetected };
      }catch(e){
        return { ok:false, error: String(e), actions:0, steps:[], navigated:false };
      }
    },
    getOuterHTMLs: function(items){
      try{
        const out = [];
        for (const it of (items||[])){
          try{
            const sel = it && it.selector; const id = it && it.id; const type = it && it.type;
            const el = sel ? document.querySelector(sel) : null;
            if (el){ out.push({ id, selector: sel, type, html: el.outerHTML || '', found:true }); }
            else { out.push({ id, selector: sel, type, found:false }); }
          }catch(ex){ out.push({ id: it && it.id, selector: it && it.selector, type: it && it.type, found:false, error:String(ex) }); }
        }
        return { ok:true, items: out };
      }catch(e){
        return { ok:false, error: String(e), items: [] };
      }
    },
  };
})();
