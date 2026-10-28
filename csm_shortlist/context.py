"""Conversation context from past customer calls (Fireflies), used to tailor recommendations.

The JSON is produced per CSM by the context-extraction step and maps an account name to what the
customer has said: pain points, goals, open items and risk flags, each tagged with a theme from
THEMES. Nothing here reads call text directly; it only reads that distilled file.
"""
import json
from pathlib import Path

# theme -> {feature key: how directly the feature answers that theme (0-1)}
THEMES = {
    "cart_recovery": {"ABANDONED_CART_FLOW": 1.0, "PRODUCT_REMOVE_FROM_CART": 0.6, "WA_JOURNEYS": 0.6, "BIK_PRODUCT": 0.5},
    "upsell_cross_sell": {"PRODUCT_ADD_TO_CART": 1.0, "AI_PRODUCT_ASSISTANT": 0.6, "PRODUCT_HISTORY_RECOMMENDATION": 0.5,
                          "CHECKOUT_COMPLETE_FLOW": 0.4},
    "support_automation": {"AGENTS": 1.0, "BIK_AI_AGENT": 1.0, "AI_KNOWLEDGE_BUTTON": 0.6, "INTEGRATIONS": 0.5},
    "order_tracking_returns": {"AGENTS": 1.0, "INTEGRATIONS": 0.8, "BIK_AI_AGENT": 0.6},
    "helpdesk_inbox": {"CRM": 1.0, "BIK_AI_AGENT": 0.5, "WA_CALLING": 0.4},
    "lead_capture": {"LEADS": 1.0, "WIDGETS": 1.0},
    "whatsapp_marketing": {"WA_CAMPAIGNS": 1.0, "RCS": 0.3, "BIK_PRODUCT": 0.6},
    "whatsapp_automation": {"WA_JOURNEYS": 1.0, "BIK_PRODUCT": 0.6},
    "email_channel": {"EMAIL": 1.0},
    "instagram": {"INSTAGRAM": 1.0},
    "rcs_sms": {"RCS": 1.0, "SMS": 1.0},
    "voice_calling": {"WA_CALLING": 1.0, "CRM": 0.5},
    "product_discovery": {"AI_PRODUCT_ASSISTANT": 1.0, "AI_SHOPPING_PAGE": 0.8, "ASK_AI_BAR": 0.7,
                          "SEARCH_PAGE_RECOMMENDATIONS": 0.7, "AI_PRODUCT_SPECIALIST": 0.7, "MANIFEST_PRODUCT": 0.6},
    "on_site_engagement": {"AI_HOME_SPECIALIST": 1.0, "DISENGAGED_ON_THE_COLLECTION_PAGE": 0.8, "AI_FLOATING_BAR": 0.6,
                           "WEBPUSH": 0.4, "MANIFEST_PRODUCT": 0.7},
    "integrations": {"INTEGRATIONS": 1.0},
    "festive_sale_planning": {"WA_CAMPAIGNS": 1.0, "EMAIL": 0.6, "WEBPUSH": 0.4, "RCS": 0.4, "SMS": 0.4},
    "cost_pricing": {"EMAIL": 0.5},
    "analytics_reporting": {},
    "deliverability_quality": {},
}
CHURN_WORDS = ("churn", "discontinu", "cancel", "terminat")
USABLE_CONFIDENCE = ("high", "medium")


class Statement:
    """One thing the customer said or is waiting on, with the call it came from."""

    def __init__(self, raw, kind, last_meeting):
        self.text = raw.get("text", "") if isinstance(raw, dict) else str(raw)
        self.theme = raw.get("theme") if isinstance(raw, dict) else None
        self.kind = kind
        self.last_meeting = last_meeting


class AccountContext:
    def __init__(self, raw):
        self.business = raw.get("business_context") or ""
        self.last_meeting = raw.get("last_meeting_date")
        self.confidence = raw.get("confidence") or "low"
        self.risk_flags = raw.get("risk_flags") or []
        self.competitors = raw.get("competitors_mentioned") or []
        self.meetings = raw.get("meetings") or []
        self.themes = set(raw.get("themes") or [])
        self.pains = [Statement(p, "pain", self.last_meeting) for p in raw.get("pain_points") or []]
        self.goals = [Statement(g, "goal", self.last_meeting) for g in raw.get("stated_goals") or []]
        self.open_items = [Statement(o, "open", self.last_meeting) for o in raw.get("open_items") or []]
        self.themes |= {s.theme for s in self.pains + self.goals + self.open_items if s.theme}

    @property
    def usable(self):
        return bool(self.themes) and self.confidence in USABLE_CONFIDENCE

    @property
    def churning(self):
        return any(w in flag.lower() for flag in self.risk_flags for w in CHURN_WORDS)

    @property
    def watch_flags(self):
        return [] if self.churning else self.risk_flags

    def fit(self, feature_key):
        """How directly the customer's own words call for this feature, and the statement that says so."""
        if not self.usable:
            return 0.0, None
        best, said = 0.0, None
        for s in self.pains + self.goals + self.open_items:
            strength = THEMES.get(s.theme, {}).get(feature_key, 0.0)
            if s.kind == "goal":
                strength *= 0.9
            if strength > best:
                best, said = strength, s
        for theme in self.themes:
            strength = 0.8 * THEMES.get(theme, {}).get(feature_key, 0.0)
            if strength > best:
                best, said = strength, None
        return best, said


class ContextBook:
    """All accounts' context, loaded from one or more extraction files."""

    def __init__(self, paths=()):
        self.by_account = {}
        for path in paths:
            for account, raw in json.load(open(path)).items():
                self.by_account[account] = AccountContext(raw)

    @classmethod
    def from_glob(cls, folder, pattern):
        return cls(sorted(Path(folder).glob(pattern)))

    def get(self, account):
        return self.by_account.get(account)
