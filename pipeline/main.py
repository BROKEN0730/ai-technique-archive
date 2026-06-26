"""主流程，嚴格按資料流順序。單一來源失敗不中斷；last_fetched 只在成功後更新。"""
import os
import sys
import traceback
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import db
import budget
import exporter
from crawler import crawl_source
from embedder import generate_embedding, to_pgvector
from ai_processor import layer1_relevance, layer2_extract

SIM_THRESHOLD = 0.85


def distinct_source(a, b):
    # 不同來源才算一次新驗證；任一為 None（如測試資料）視為不同，避免低估
    return a is None or b is None or a != b


def assemble_knowledge(k, staging_row, trust):
    """補上自動欄位：versions[0]、relations target_id、convergence、confidence。"""
    ym = datetime.now(timezone.utc).strftime("%Y-%m")
    k["versions"] = [{
        "version": 1, "date": ym, "summary_zh": k.get("summary_zh", ""),
        "improvement_over_previous": None, "source_url": staging_row["url"],
    }]
    resolved = []
    for r in k.get("relations", []) or []:
        tid = db.find_knowledge_id(r.get("target_title", ""))
        if tid:
            resolved.append({"target_id": tid, "relation_type": r.get("relation_type"), "note": r.get("note", "")})
    k["relations"] = resolved
    k["source_convergence_count"] = 1
    k["confidence_level"] = "official" if trust >= 90 else "single_source"
    return k


def run():
    state = {"run_status": "success", "items_processed": 0, "items_promoted": 0,
             "budget_exhausted": False, "ai_skipped": False}

    sources = db.get_active_sources()
    seen = db.existing_urls()                       # URL 去重先行
    fetched_ok = []

    for src in sources:
        try:
            articles = crawl_source(src)
        except Exception as e:
            print(f"[source-fail] {src['name']}: {e}")
            continue
        for a in articles:
            if a["url"] in seen:
                continue
            seen.add(a["url"])
            emb = generate_embedding(f"{a['title']} {a['raw_content']}")

            km = db.match_knowledge(emb, SIM_THRESHOLD)
            if km:                                  # 已知概念（已升級）
                db.bump_convergence(km["id"], src["trust_score"] >= 90)
                continue

            sm = db.match_staging(emb, SIM_THRESHOLD)
            if sm:                                  # 待觀察區既有概念
                existing = db.get_staging(sm["id"])
                inc = bool(existing) and distinct_source(existing.get("source_id"), src["id"])
                db.bump_verification(sm["id"], inc)
                continue

            # 全新概念
            summary = None
            if budget.check_budget():
                rel, it, ot = layer1_relevance(a["title"], a["raw_content"])
                budget.update_budget(it, ot)
                if not rel.get("relevant"):
                    continue
                summary = a["raw_content"][:280]
            else:
                state["budget_exhausted"] = True
                state["ai_skipped"] = True

            db.insert_staging({
                "url": a["url"], "title": a["title"], "content_summary": summary,
                "raw_content": a["raw_content"], "source_id": src["id"],
                "tags_machine": [], "language": a["language"],
                "embedding": emb,
            })
            state["items_processed"] += 1
        fetched_ok.append(src["id"])

    # 升級掃描
    titles = db.knowledge_titles()
    for st in db.get_promotable():
        if not budget.check_budget(est_input=2000, est_output=1500):
            state["budget_exhausted"] = True
            state["ai_skipped"] = True
            break
        k, it, ot = layer2_extract(st["title"], st.get("raw_content") or st.get("content_summary") or st["title"], titles)
        budget.update_budget(it, ot)
        if not k:
            continue
        k = assemble_knowledge(k, st, st["trust_score"])
        emb_text = st["embedding"] if isinstance(st["embedding"], str) else to_pgvector(st["embedding"])
        db.promote(st["id"], k, emb_text)
        db.update_trust(st["source_id"], "verified", "promoted to knowledge")
        titles.append(k["title_zh"])
        state["items_promoted"] += 1

    # 每日 feedback 彙整 + 封存
    db.aggregate_feedback()
    db.archive_old_staging()

    # 成功才更新 last_fetched
    now_iso = datetime.now(timezone.utc).isoformat()
    for sid in fetched_ok:
        db.set_last_fetched(sid, now_iso)

    # 靜態輸出
    supa_url = os.environ.get("SUPABASE_URL", "")
    anon = os.environ.get("SUPABASE_ANON_KEY", "")
    items = exporter.export_json(db.all_knowledge())
    exporter.export_stats(db.staging_stats())
    exporter.generate_index(supa_url, anon)
    exporter.generate_technique_pages(items, supa_url, anon)

    cur = budget.get_current_cost()
    if state["ai_skipped"]:
        state["run_status"] = "success_no_ai"
    state["budget_used_usd"] = round(cur, 4)
    state["budget_remaining_usd"] = round(max(0.0, budget.MONTHLY_BUDGET_USD - cur), 4)
    exporter.export_last_updated(state)
    print(f"[done] {state}")
    return state


if __name__ == "__main__":
    try:
        run()
    except Exception:
        traceback.print_exc()
        # 仍輸出最低限度的 last_updated，讓部署有東西可放
        try:
            exporter.export_last_updated({"run_status": "error", "items_processed": 0,
                                          "items_promoted": 0, "budget_exhausted": False, "ai_skipped": False})
        except Exception:
            pass
        sys.exit(1)
