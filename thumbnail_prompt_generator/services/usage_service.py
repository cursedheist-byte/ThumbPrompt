"""
Usage tracking service. All plan-limit logic lives here so routes never
touch the database directly.

Real payments (see services/payment_service.py) are the only way a
user's plan/credits change now - there is deliberately no
"change_plan(user_id, plan)" helper here anymore; that used to be a
demo shortcut that let a client just POST a plan name and get it for
free, which is exactly what real payment verification replaces.
"""

from config import Config
from database import database


class UsageLimitExceededError(Exception):
    def __init__(self, plan, prompts_remaining):
        self.plan = plan
        self.prompts_remaining = prompts_remaining
        super().__init__(
            f"Usage limit reached for plan '{plan}' (0 prompts remaining)."
        )


def get_usage_summary(user):
    plan_info = Config.PLANS.get(user["plan"], Config.PLANS[Config.DEFAULT_PLAN])
    plan_expires_at = user.get("plan_expires_at")
    # Callers are expected to pass a user row from
    # database.get_user_enforcing_expiry(), so by the time we get here an
    # expired plan has already been reverted to Free - this is just
    # reading the (already-consistent) state, not re-deriving it.
    topup_available = user["plan"] != Config.DEFAULT_PLAN and bool(plan_expires_at)
    return {
        "plan": user["plan"],
        "plan_label": plan_info["label"],
        "prompt_limit": plan_info["prompt_limit"],
        "prompts_used": user["prompts_used"],
        "prompts_remaining": user["prompts_remaining"],
        "ads_enabled": plan_info.get("ads_enabled", True),
        "plan_expires_at": plan_expires_at,
        "topup_available": topup_available,
    }


def ensure_has_usage(user):
    """Raise if the user has no prompts remaining. Does NOT decrement —
    used to gate the free concept-analysis step."""
    if user["prompts_remaining"] <= 0:
        raise UsageLimitExceededError(user["plan"], user["prompts_remaining"])


def charge_usage(user_id, generation_id, action):
    """Decrement usage and return the updated summary, or raise if the
    user had nothing left (defends against races / repeated clicks)."""
    updated = database.decrement_usage(user_id, generation_id=generation_id, action=action)
    if updated is None:
        user = database.get_user(user_id)
        raise UsageLimitExceededError(user["plan"], user["prompts_remaining"])
    return get_usage_summary(updated)


def list_plans():
    return Config.PLANS


def topup_config():
    return Config.TOPUP
