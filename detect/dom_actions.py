from __future__ import annotations

from typing import Any, Dict, Optional


def annotate_controls(page, *, set_dom_id_attr: bool = True, enable_probe: bool = True, probe_max: int = 30, probe_wait_ms: int = 200, no_none: bool = False) -> Dict[str, Any]:
    """Annotate interactive controls in DOM with __actiontype/__selectorid/__domid.

    Tries JS helper (DetectHelpers.annotateControls) first; falls back to a minimal
    inline implementation when helper is unavailable. Always returns a dict with
    'ok' and 'count' fields.
    """
    try:
        res = page.evaluate(
            "opts => window.DetectHelpers && window.DetectHelpers.annotateControls && window.DetectHelpers.annotateControls(opts)",
            {
                "setDomIdAttr": bool(set_dom_id_attr),
                "enableProbe": bool(enable_probe),
                "probeMax": int(probe_max),
                "probeWaitMs": int(probe_wait_ms),
                "noNone": bool(no_none),
            },
        )
        if isinstance(res, dict) and res.get("ok"):
            return {"ok": True, "count": int(res.get("count") or 0)}
    except Exception:
        res = None

    # Fallback inline
    try:
        count = page.evaluate(
            """
            (()=>{ let c=0; const all=document.querySelectorAll('*');
              const isC=(el)=>{const t=(el.tagName||'').toLowerCase(); const r=(el.getAttribute('role')||'').toLowerCase();
                if(['button','input','select','textarea','a'].includes(t)) return true; if(['button','link','textbox','checkbox','radio','combobox'].includes(r)) return true;
                const tb=Number(el.getAttribute('tabindex')); if(!Number.isNaN(tb)&&tb>=0) return true; if(el.isContentEditable) return true; const cls=(el.className||'').toLowerCase(); if(cls&&cls.includes('btn')) return true; return false; };
              const act=(el)=>{const t=(el.tagName||'').toLowerCase(); const r=(el.getAttribute('role')||'').toLowerCase(); if(t==='input'){const it=(el.getAttribute('type')||'').toLowerCase();
                if(['checkbox','radio','switch','toggle'].includes(it)) return 'toggle'; if(['submit'].includes(it)) return 'submit'; if(['button','image','reset'].includes(it)) return 'click'; return 'type'; }
                if(t==='textarea') return 'type'; if(t==='select') return 'select'; if(t==='a'||r==='link') return 'navigate'; if(r==='button') return 'click'; return 'none'; };
              for(let i=0;i<all.length;i++){ const el=all[i]; try{ if(!isC(el)) continue; const a=act(el); const did=el.getAttribute('id')||''; if(%(set_id)s){ el.setAttribute('__selectorid','d'+i); } if(did) el.setAttribute('__domid',did); el.setAttribute('__actiontype',a); c++; }catch(_){}}
              return c; })()
            """ % {"set_id": "true" if set_dom_id_attr else "false"}
        )
        return {"ok": True, "count": int(count or 0)}
    except Exception:
        return {"ok": False, "count": 0}


def interactive_reveal(
    page,
    context,
    *,
    max_actions: int = 8,
    total_budget_ms: int = 15000,
    wait_ms: int = 800,
) -> Dict[str, Any]:
    """Run a limited set of safe interactions to reveal hidden panels.

    Uses JS helper when available. If navigation is detected, tries to go back and
    re-annotate to restore state. Returns a summary dict but never raises.
    """
    try:
        url_before = page.url
    except Exception:
        url_before = ""
    summary: Dict[str, Any] = {"ok": False, "actions": 0, "steps": [], "navigated": False}
    try:
        res = page.evaluate(
            "opts => window.DetectHelpers && window.DetectHelpers.revealInteractively && window.DetectHelpers.revealInteractively(opts)",
            {"maxActions": int(max_actions or 0), "totalBudgetMs": int(total_budget_ms or 0), "waitMs": int(wait_ms or 0)},
        )
        if isinstance(res, dict):
            summary.update({
                "ok": bool(res.get("ok")),
                "actions": int(res.get("actions") or 0),
                "steps": res.get("steps") or [],
                "navigated": bool(res.get("navigated")),
            })
    except Exception:
        # keep default summary
        pass

    # If navigation happened, go back and re-annotate
    try:
        if (summary.get("navigated") is True) or (url_before and page.url != url_before):
            try:
                page.go_back(wait_until='domcontentloaded', timeout=3000)
            except Exception:
                pass
            try:
                annotate_controls(page, set_dom_id_attr=True)
            except Exception:
                pass
    except Exception:
        pass
    return summary
