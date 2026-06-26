"""靜態輸出：白名單 JSON + technique 詳情頁（re.sub 替換，不用 Jinja2）。"""
import os
import re
import json
import html
from datetime import datetime, timezone
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PUBLIC = os.path.join(ROOT, "public")
FRONTEND = os.path.join(ROOT, "frontend")

WHITELIST = ["id", "title_zh", "title_en", "summary_zh", "mechanism_zh", "success_criteria",
             "copyable_prompt_template", "diagnostic_checklist", "onboarding_prompt_template",
             "access_level", "applicable_scenarios", "failure_conditions", "tags_human",
             "versions", "relations", "source_convergence_count", "confidence_level",
             "verified_count", "partial_count", "failed_count", "first_recorded", "promoted_at"]

ACCESS_LABEL = {"green": "只需瀏覽器", "yellow": "需 system prompt", "orange": "需安裝", "red": "需終端機"}
REL_ZH = {"extends_from": "延伸自", "conflicts_with": "和此技法衝突",
          "combinable_with": "可搭配使用", "superseded_by": "已被取代", "inspired_by": "概念相關"}


def _now():
    return datetime.now(timezone.utc).isoformat()


def _write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def export_json(knowledge_rows):
    items = [{k: r.get(k) for k in WHITELIST} for r in knowledge_rows]
    _write(os.path.join(PUBLIC, "knowledge.json"),
           {"generated_at": _now(), "total_count": len(items), "items": items})
    # 第 4 個 JSON：tags 聚合，給前端標籤過濾用
    tags = Counter(t for r in knowledge_rows for t in (r.get("tags_human") or []))
    _write(os.path.join(PUBLIC, "tags.json"),
           {"generated_at": _now(), "tags": [{"tag": t, "count": c} for t, c in tags.most_common()]})
    return items


def export_stats(stats):
    _write(os.path.join(PUBLIC, "staging_stats.json"), {"generated_at": _now(), **stats})


def export_last_updated(d):
    _write(os.path.join(PUBLIC, "last_updated.json"), {"timestamp": _now(), **d})


def _esc(s):
    return html.escape(str(s if s is not None else ""))


def _ul(items):
    return "\n".join(f"<li>{_esc(x)}</li>" for x in (items or [])) or "<li class='muted'>—</li>"


def _checklist(steps):
    out = []
    for s in steps or []:
        out.append(f"<li><b>{_esc(s.get('check'))}</b>"
                   f"<div class='muted'>否 → {_esc(s.get('if_no'))}</div></li>")
    return "\n".join(out) or "<li class='muted'>—</li>"


def _versions(vs):
    out = []
    for v in vs or []:
        imp = v.get("improvement_over_previous")
        out.append(f"<li>v{_esc(v.get('version'))} · {_esc(v.get('date'))} — {_esc(v.get('summary_zh'))}"
                   + (f"<div class='muted'>改進：{_esc(imp)}</div>" if imp else "") + "</li>")
    return "\n".join(out) or "<li class='muted'>—</li>"


def _relations(rs):
    out = []
    for r in rs or []:
        out.append(f"<li>{_esc(REL_ZH.get(r.get('relation_type'), r.get('relation_type')))}："
                   f"{_esc(r.get('note'))} <span class='muted'>({_esc(r.get('target_id'))})</span></li>")
    return "\n".join(out) or "<li class='muted'>—</li>"


def generate_technique_pages(items, supabase_url, anon_key):
    tpl_path = os.path.join(FRONTEND, "technique_template.html")
    with open(tpl_path, encoding="utf-8") as f:
        tpl = f.read()
    out_dir = os.path.join(PUBLIC, "technique")
    os.makedirs(out_dir, exist_ok=True)
    for it in items:
        repl = {
            "{{id}}": _esc(it["id"]),
            "{{title_zh}}": _esc(it["title_zh"]),
            "{{title_en}}": _esc(it.get("title_en")),
            "{{summary_zh}}": _esc(it["summary_zh"]),
            "{{mechanism_zh}}": _esc(it["mechanism_zh"]),
            "{{success_criteria}}": _esc(it["success_criteria"]),
            "{{copyable_prompt_template}}": _esc(it["copyable_prompt_template"]),
            "{{onboarding_prompt_template}}": _esc(it.get("onboarding_prompt_template")),
            "{{access_level}}": _esc(it.get("access_level")),
            "{{access_label}}": _esc(ACCESS_LABEL.get(it.get("access_level"), "")),
            "{{confidence_level}}": _esc(it.get("confidence_level")),
            "{{convergence}}": _esc(it.get("source_convergence_count")),
            "{{verified}}": _esc(it.get("verified_count")),
            "{{partial}}": _esc(it.get("partial_count")),
            "{{failed}}": _esc(it.get("failed_count")),
            "{{diagnostic_html}}": _checklist(it.get("diagnostic_checklist")),
            "{{scenarios_html}}": _ul(it.get("applicable_scenarios")),
            "{{failures_html}}": _ul(it.get("failure_conditions")),
            "{{versions_html}}": _versions(it.get("versions")),
            "{{relations_html}}": _relations(it.get("relations")),
            "{{tags_html}}": " ".join(f"<span class='tag'>{_esc(t)}</span>" for t in (it.get("tags_human") or [])),
            "{{SUPABASE_URL}}": supabase_url or "",
            "{{SUPABASE_ANON_KEY}}": anon_key or "",
        }
        page = tpl
        for k, v in repl.items():
            page = page.replace(k, v)
        with open(os.path.join(out_dir, f"{it['id']}.html"), "w", encoding="utf-8") as f:
            f.write(page)


def generate_index(supabase_url, anon_key):
    with open(os.path.join(FRONTEND, "index_template.html"), encoding="utf-8") as f:
        page = f.read()
    page = page.replace("{{SUPABASE_URL}}", supabase_url or "").replace("{{SUPABASE_ANON_KEY}}", anon_key or "")
    with open(os.path.join(PUBLIC, "index.html"), "w", encoding="utf-8") as f:
        f.write(page)
