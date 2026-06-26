"""Claude API 兩層處理。所有輸出過 parse_json_safe 容錯。回傳 (dict|None, in_tokens, out_tokens)。"""
import os
import re
import json
from anthropic import Anthropic

client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
MODEL = "claude-sonnet-4-6"


def parse_json_safe(text, fallback):
    text = re.sub(r"```json|```", "", text or "").strip()
    m = re.search(r"\{[\s\S]*\}", text)
    if m:
        try:
            return json.loads(m.group())
        except Exception:
            pass
    return fallback


def _call(system, user, max_tokens):
    r = client.messages.create(
        model=MODEL, max_tokens=max_tokens, system=system,
        messages=[{"role": "user", "content": user}],
    )
    text = "".join(b.text for b in r.content if b.type == "text")
    return text, r.usage.input_tokens, r.usage.output_tokens


LAYER1_SYSTEM = """你是一個嚴格的內容分類器。
判斷輸入文章是否包含「AI工具的使用技法或使用方式」的實質內容。
合格必須包含具體做法描述，不只是概念介紹。
不合格：純新聞、純概念介紹、廣告業配、與AI使用無關的內容。
只輸出JSON不輸出任何其他文字不加markdown標記：
{"relevant": true, "reason": "一句話"}"""


def layer1_relevance(title, content):
    text, it, ot = _call(LAYER1_SYSTEM, f"標題：{title}\n\n內容：{(content or '')[:3000]}", 200)
    return parse_json_safe(text, {"relevant": False, "reason": "解析失敗"}), it, ot


LAYER2_SYSTEM = """你是一個AI技法知識萃取器和繁體中文技術編輯。
tags_human規則：用使用者遇到的困境語言，繁體中文，例如「AI改太多」「結果跑偏」。
access_level規則：green只需瀏覽器/yellow需system prompt/orange需安裝/red需終端機。
diagnostic_checklist必須輸出此格式3-5步：[{"step":1,"check":"確認問題","if_no":"解決方式"}]
relations規則：比對現有標題{existing_titles}，有關聯才輸出，格式：[{"target_title":"標題","relation_type":"extends_from","note":"說明"}]
只輸出JSON不加markdown：
{"title_zh":"","title_en":"","summary_zh":"","mechanism_zh":"","success_criteria":"","copyable_prompt_template":"","diagnostic_checklist":[],"onboarding_prompt_template":"","access_level":"green","applicable_scenarios":[],"failure_conditions":[],"tags_machine":[],"tags_human":[],"relations":[],"content_summary":""}"""


def layer2_extract(title, content, existing_titles):
    system = LAYER2_SYSTEM.replace("{existing_titles}", json.dumps(existing_titles, ensure_ascii=False))
    text, it, ot = _call(system, f"標題：{title}\n\n內容：{(content or '')[:6000]}", 1500)
    parsed = parse_json_safe(text, None)
    if parsed and parsed.get("title_zh") and parsed.get("summary_zh") and parsed.get("mechanism_zh"):
        return parsed, it, ot
    return None, it, ot  # 解析失敗：此筆跳過
