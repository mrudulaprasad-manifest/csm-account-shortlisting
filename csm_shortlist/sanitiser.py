"""Drops accounts that should not be pitched this week, using HubSpot as the source of truth.

An account is removed when its HubSpot company is no longer a customer, when its Service deal sits in a
churn stage, or when the owning CSM already has it in the upsell motion (those deals are worked, not shortlisted).
"""
import json
import re
from pathlib import Path

LIFECYCLE_CUSTOMER = "customer"
LIFECYCLE_LABELS = {
    "257502396": "Churned",
    "452134620": "Closed Lost",
    "2997142742": "Inactive",
    "5307990205": "Disqualified",
    "lead": "Lead",
    "opportunity": "Opportunity",
    "3411539171": "Pilot",
}
# Service BIK stages
CHURN_STAGES = {"328329446": "Churned", "5463045336": "Churn Notice"}
UPSELL_STAGES = {
    "5905854694": "Accounts for this week",
    "5905854695": "Solutioning completed",
    "5905854696": "Outreach Sent",
    "5905854697": "Solution pitched",
    "5905854698": "Solution adopted and live",
    "5905854699": "Escalated",
}
OWNER_BY_ID = {"30821638": "Luv", "1617806026": "Nishant", "34212172": "Rahul", "37412756": "Prial"}


def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def name_key(name):
    """Deal and company names carry suffixes such as '- New Deal' or '(clone)' that the sheet does not."""
    return norm(re.sub(r"\s*[-–(].*$", "", name or ""))


def same_brand(a, b):
    if not a or not b:
        return False
    return a == b or (min(len(a), len(b)) >= 6 and (a.startswith(b) or b.startswith(a)))


class Removal:
    def __init__(self, reason, source):
        self.reason = reason
        self.source = source


class AccountSanitiser:
    def __init__(self, companies_path, deals_path, overrides_path=None):
        self.companies = json.load(open(companies_path))
        self.deals = json.load(open(deals_path))
        self.overrides = json.load(open(overrides_path)) if overrides_path and Path(overrides_path).exists() else {}

    def _companies_for(self, brand, csm_first):
        key = name_key(brand)
        return [c for c in self.companies
                if (c.get("gsm_owner") or "").startswith(csm_first) and same_brand(name_key(c.get("name")), key)]

    def _deals_for(self, brand, csm_first):
        key = name_key(brand)
        return [d for d in self.deals
                if OWNER_BY_ID.get(d.get("hubspot_owner_id")) == csm_first and same_brand(name_key(d.get("dealname")), key)]

    def check(self, brand, csm):
        """Returns a Removal, or None when the account stays on the list."""
        first = csm.split()[0]
        if brand in self.overrides:
            return Removal(self.overrides[brand], "Manual override")
        deals = self._deals_for(brand, first)
        churned = [d for d in deals if d.get("dealstage") in CHURN_STAGES]
        # a churned sub-brand deal must not remove a brand that still has live deals
        if churned and len(churned) == len(deals):
            return Removal(f"Service deal in '{CHURN_STAGES[churned[0]['dealstage']]}' stage", "HubSpot deal")
        companies = self._companies_for(brand, first)
        if companies and all(c.get("lifecyclestage") != LIFECYCLE_CUSTOMER for c in companies):
            stage = companies[0].get("lifecyclestage")
            return Removal(f"HubSpot lifecycle stage is {LIFECYCLE_LABELS.get(stage, stage)}", "HubSpot company")
        for d in deals:
            if d.get("dealstage") in UPSELL_STAGES:
                return Removal(f"Already in upsell motion ('{UPSELL_STAGES[d['dealstage']]}')", "HubSpot deal")
        return None

    def company(self, brand, csm):
        """The HubSpot record for an account, used for renewal and last-touch dates.

        Brands often have duplicate company records, so prefer a customer record that carries a renewal date.
        """
        matches = self._companies_for(brand, csm.split()[0])
        customers = [c for c in matches if c.get("lifecyclestage") == LIFECYCLE_CUSTOMER]

        def dated(c):
            return bool(c.get("renewal_date") or c.get("bik_renewal_date"))

        for pool in (customers, matches):
            with_dates = [c for c in pool if dated(c)]
            if with_dates:
                return max(with_dates, key=lambda c: c.get("renewal_date") or c.get("bik_renewal_date"))
        return (customers or matches or [None])[0]
