"""費用保護。.maybe_single() 避免首次 crash；含單次預估緩衝避免擊穿上限。"""
import os
from datetime import datetime
from db import supabase

MONTHLY_BUDGET_USD = float(os.environ.get("MONTHLY_BUDGET_USD", "10"))
COST_PER_1K_INPUT = 0.003
COST_PER_1K_OUTPUT = 0.015
SAFETY_BUFFER = 0.5


def _ym():
    return datetime.now().strftime("%Y-%m")


def get_current_cost():
    try:
        r = supabase.table("budget_tracking").select("estimated_cost_usd").eq("year_month", _ym()).maybe_single().execute()
        return float(r.data["estimated_cost_usd"]) if r and r.data else 0.0
    except Exception:
        return 0.0


def check_budget(est_input=300, est_output=500):
    estimated = est_input / 1000 * COST_PER_1K_INPUT + est_output / 1000 * COST_PER_1K_OUTPUT
    return (get_current_cost() + estimated) < (MONTHLY_BUDGET_USD - SAFETY_BUFFER)


def update_budget(input_tokens, output_tokens):
    cost = input_tokens / 1000 * COST_PER_1K_INPUT + output_tokens / 1000 * COST_PER_1K_OUTPUT
    supabase.table("budget_tracking").upsert({
        "year_month": _ym(),
        "estimated_cost_usd": get_current_cost() + cost,
        "updated_at": datetime.now().isoformat(),
    }, on_conflict="year_month").execute()
