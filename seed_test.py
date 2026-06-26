"""語意去重 / verification_count 驗證。需要 SUPABASE_URL + SUPABASE_KEY 環境變數。
預期：article-2 進來後 article-1 的 verification_count = 2。不呼叫 Claude API。"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "pipeline"))

import db
from embedder import generate_embedding

MOCK_ARTICLES = [
    {"url": "https://test.example.com/article-1", "title": "Chain of Thought Prompting Guide",
     "content": "When you add 'Let's think step by step' to your prompt the model reasons through intermediate steps before answering, improving accuracy on multi-step problems."},
    {"url": "https://test.example.com/article-2", "title": "Step by Step Thinking in AI Prompts",
     "content": "Adding step-by-step instruction to prompts helps the AI reason through each stage, a chain of thought technique that boosts correctness."},
]

SIM = 0.85


def process(a):
    """精簡版 main 流程：URL 去重 → 語意去重 → 全新則插入。回傳動作字串。"""
    if a["url"] in db.existing_urls():
        return "url-dup (skipped)"
    emb = generate_embedding(f"{a['title']} {a['content']}")
    sm = db.match_staging(emb, SIM)
    if sm:
        db.bump_verification(sm["id"], True)  # 測試資料 source 皆為 None → distinct_source 視為不同 → +1
        return f"semantic-match → verification +1 (staging {sm['id']})"
    db.insert_staging({
        "url": a["url"], "title": a["title"], "content_summary": a["content"][:280],
        "raw_content": a["content"][:2000], "source_id": None,
        "tags_machine": [], "language": "en", "embedding": emb,
    })
    return "inserted as new"


def main():
    for a in MOCK_ARTICLES:
        print(f"{a['url']}: {process(a)}")

    r = db.supabase.table("staging").select("verification_count").eq("url", MOCK_ARTICLES[0]["url"]).single().execute()
    vc = r.data["verification_count"]
    print(f"\narticle-1 verification_count = {vc}")
    assert vc == 2, f"預期 2，實得 {vc}"
    print("PASS ✅")


if __name__ == "__main__":
    main()
