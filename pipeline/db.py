"""Supabase 用戶端 + 所有 DB 操作。促進/彙整/封存皆走 RPC（在 Postgres 端 atomic）。"""
import os
from supabase import create_client

supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_KEY"])


def get_active_sources():
    return supabase.table("sources").select("*").eq("is_active", True).execute().data


def existing_urls():
    rows = supabase.table("staging").select("url").execute().data
    return {r["url"] for r in rows}


def match_staging(emb, threshold=0.85, count=1):
    r = supabase.rpc("match_staging", {
        "query_embedding": emb, "match_threshold": threshold, "match_count": count
    }).execute()
    return r.data[0] if r.data else None


def match_knowledge(emb, threshold=0.85, count=1):
    r = supabase.rpc("match_knowledge", {
        "query_embedding": emb, "match_threshold": threshold, "match_count": count
    }).execute()
    return r.data[0] if r.data else None


def get_staging(sid):
    r = supabase.table("staging").select("*").eq("id", sid).maybe_single().execute()
    return r.data if r else None


def bump_verification(sid, increment):
    supabase.rpc("bump_verification", {"p_id": sid, "p_increment": increment}).execute()


def bump_convergence(kid, official):
    supabase.rpc("bump_convergence", {"p_id": kid, "p_official": official}).execute()


def insert_staging(row):
    supabase.table("staging").insert(row).execute()


def get_promotable():
    return supabase.rpc("get_promotable", {}).execute().data or []


def knowledge_titles():
    rows = supabase.table("knowledge").select("title_zh").execute().data
    return [r["title_zh"] for r in rows]


def find_knowledge_id(title_zh):
    r = supabase.table("knowledge").select("id").eq("title_zh", title_zh).limit(1).execute()
    return r.data[0]["id"] if r.data else None


def promote(staging_id, knowledge_json, embedding_text):
    return supabase.rpc("promote", {
        "p_staging_id": staging_id, "p_knowledge": knowledge_json, "p_embedding": embedding_text or ""
    }).execute().data


def update_trust(source_id, event, reason):
    if source_id:
        supabase.rpc("update_trust", {"p_source_id": source_id, "p_event": event, "p_reason": reason}).execute()


def aggregate_feedback():
    supabase.rpc("aggregate_feedback", {}).execute()


def archive_old_staging():
    supabase.rpc("archive_old_staging", {}).execute()


def set_last_fetched(source_id, iso):
    supabase.table("sources").update({"last_fetched": iso}).eq("id", source_id).execute()


def all_knowledge():
    return supabase.table("knowledge").select("*").order("promoted_at", desc=True).execute().data


def staging_stats():
    def count(q):
        return q.execute().count or 0
    waiting = count(supabase.table("staging").select("id", count="exact").eq("status", "waiting"))
    promoted = count(supabase.table("staging").select("id", count="exact")
                     .eq("status", "promoted").gte("promoted_at", _week_ago()))
    archived = count(supabase.table("staging").select("id", count="exact")
                     .eq("status", "archived").gte("last_seen", _week_ago()))
    return {"waiting_count": waiting, "promoted_this_week": promoted, "archived_this_week": archived}


def _week_ago():
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
