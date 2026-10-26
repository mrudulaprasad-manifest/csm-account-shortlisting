"""Weekly CSM upsell shortlist.

Candidates come from the CSM sheet (CSM, MRR and bucket only). Accounts the owning CSM already
contacted this month in HubSpot are dropped. Feature gaps come from the Manifest and BIK GSM
dashboards, so BIK-only, Manifest-only and BIK+Manifest accounts are all scored.
"""
import csv
import json
import math
import re
import sys
from pathlib import Path

from recommender import FeatureCatalog, PeerStats, Recommender

CSMS = ("Luv Jain", "Nishant Kurivella", "Rahul Singh", "Prial Gupta")
BUCKET_WEIGHT = {"Enterprise + Key accounts": 1.0, "MM": 0.7, "SMB": 0.4}
BUCKET_LABEL = {"Enterprise + Key accounts": "Enterprise/key account", "MM": "Mid-market account", "SMB": "SMB account"}

SIGNALS = {
    "ABANDONED_CART_FLOW": "Abandoned Checkout Recovery Nudge",
    "PRODUCT_ADD_TO_CART": "Add to Cart Popup Nudge",
    "PRODUCT_REMOVE_FROM_CART": "Removed from Cart Nudge",
    "CHECKOUT_COMPLETE_FLOW": "Post-Checkout Nudge",
    "AI_HOME_SPECIALIST": "Home Page Welcome Nudge",
    "AI_PRODUCT_SPECIALIST": "Product Page Welcome Nudge",
    "DISENGAGED_ON_THE_COLLECTION_PAGE": "Collection Page Welcome Nudge",
    "SEARCH_PAGE_RECOMMENDATIONS": "Search Results Nudge",
    "PRODUCT_HISTORY_RECOMMENDATION": "Browsing-History Recommendations",
    "AI_FLOATING_BAR": "AI Floating Bar",
    "ASK_AI_BAR": "Ask AI Bar",
    "AI_KNOWLEDGE_BUTTON": "Ask Me Anything Button",
    "AI_PRODUCT_ASSISTANT": "AI Product Assistant",
    "AI_SHOPPING_PAGE": "AI Shopping Page",
    "AGENTS": "AI Agents",
    "LEADS": "Lead Generation",
    "INTEGRATIONS": "Integrations",
}
# BIK dashboard signal -> (label, columns where any value > 0 means the feature is in use)
BIK_SIGNALS = {
    "WA_CAMPAIGNS": ("WhatsApp Campaigns", ["Campaign Metrics-numberOfWhatsappMarketingTemplatesSent", "Campaign Metrics-numberOfCampaigns"]),
    "WA_JOURNEYS": ("WhatsApp Journeys", ["Journey Metrics-activeWhatsappJourneys", "Journey Metrics-numberOfWhatsappTemplatesSent"]),
    "EMAIL": ("Email Campaigns/Journeys", ["Campaign Metrics-numberOfEmailsSent", "Journey Metrics-activeEmailJourneys"]),
    "INSTAGRAM": ("Instagram Automation", ["Journey Metrics-activeInstagramJourneys", "Ai metrics-aiDirectRevenueInstagram"]),
    "RCS": ("RCS Messaging", ["Overall Health-totalRCSMessages", "Campaign Metrics-numberOfRCSMessages"]),
    "SMS": ("SMS", ["Overall Health-totalSMSMessages"]),
    "WEBPUSH": ("Web Push", ["Campaign Metrics-numberOfWebpushMessages"]),
    "BIK_AI_AGENT": ("BIK AI Agent", ["Ai metrics-activeAIAgents"]),
    "WIDGETS": ("Lead/Offer Widgets", ["Widget Revenue-activeWidgets"]),
    "CRM": ("CRM / Helpdesk Inbox", ["crm metrics-activeAgents", "crm metrics-ticketsClosed", "crm metrics-crmMessageCount"]),
    "WA_CALLING": ("WhatsApp Calling", ["crm metrics-waTotalCalls"]),
}
TOTAL_SIGNALS = len(SIGNALS) + len(BIK_SIGNALS)


def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def domain_key(url):
    host = re.sub(r"^https?://(www\.)?", "", (url or "").lower()).split("/")[0]
    return norm(host.rsplit(".", 1)[0] if "." in host else host)


class OutreachLog:
    """Companies already touched this month (calls, meetings, notes, outbound email, active upsell deals)."""

    def __init__(self, path):
        self.rows = [r for r in csv.DictReader(open(path))]

    def hit(self, brand, csm):
        """Only the owning CSM's own activity (or an open upsell deal on the account) counts."""
        b = norm(brand)
        owner = csm.split()[0]
        for r in self.rows:
            if r["by"] not in (owner, "CSM"):
                continue
            k = r["key"]
            if b.startswith(k) or (len(k) >= 6 and k in b):
                return r
        return None


class Store:
    def __init__(self, raw):
        self.raw = raw
        self.name = (raw["Basic Metrics-storeName"] or "").strip()
        self.url_key = domain_key(raw["Basic Metrics-storeUrl"])

    def matches(self, b):
        n = norm(self.name)
        if n and (n.startswith(b) or b.startswith(n) or (len(b) >= 6 and b in n)):
            return True
        return len(b) >= 6 and self.url_key.startswith(b)


class ManifestStore(Store):
    def __init__(self, raw):
        super().__init__(raw)
        self.orders = raw["Revenue-totalOrders"] or 0
        self.gmv = raw["Revenue-totalOrderVolume"] or 0

    def used_signals(self):
        used = set()
        for key in SIGNALS:
            if key in ("AGENTS", "LEADS", "INTEGRATIONS"):
                continue
            if (self.raw.get(f"Interaction Rate-{key}-total-sessions") or 0) > 0:
                used.add(key)
        if (self.raw["Support Metrics-agentsCreated"] or 0) > 0:
            used.add("AGENTS")
        if (self.raw["Leads-leadsCollected"] or 0) > 0:
            used.add("LEADS")
        if str(self.raw["Leads-integrations"] or "").strip() not in ("", "None", "0"):
            used.add("INTEGRATIONS")
        return used


class BikStore(Store):
    def __init__(self, raw):
        super().__init__(raw)
        self.messages = raw.get("Overall Health-totalMessagesSent") or 0

    def used_signals(self):
        return {k for k, (_, cols) in BIK_SIGNALS.items()
                if any((self.raw.get(c) or 0) > 0 for c in cols)}


class DashboardIndex:
    def __init__(self, files, store_cls):
        seen, self.stores = set(), []
        for f in files:
            t = json.load(open(f))["table"]
            for r in t["rows"]:
                raw = dict(zip(t["columns"], r))
                if raw["storeId"] in seen:
                    continue
                seen.add(raw["storeId"])
                self.stores.append(store_cls(raw))

    def match(self, brand):
        b = norm(brand)
        if len(b) < 4:
            return []
        return [s for s in self.stores if self._eligible(s) and s.matches(b)]

    def _eligible(self, s):
        return True


class ManifestIndex(DashboardIndex):
    def __init__(self, files):
        super().__init__(files, ManifestStore)

    def _eligible(self, s):
        return s.orders > 0


class BikIndex(DashboardIndex):
    def __init__(self, files):
        super().__init__(files, BikStore)

    def _eligible(self, s):
        return not re.fullmatch(r"(my store|mein shop|my store \d+)", s.name.lower())


class Account:
    def __init__(self, row, m_stores, b_stores, recommender):
        self.recommender = recommender
        self.brand = row["Brand name"].strip()
        self.csm = row["CSM"].strip()
        self.sub_jul = float(row["Subscription July"] or 0)
        self.mrr = float(row["Subscription August"] or 0)
        self.bucket = row["Revenue Bucket (Based on MRR)"].strip()
        self.m_stores = m_stores
        self.b_stores = b_stores

    @property
    def matched(self):
        return bool(self.m_stores or self.b_stores)

    @property
    def mix(self):
        if self.m_stores and self.b_stores:
            return "BIK + Manifest"
        if self.m_stores:
            return "Manifest only"
        if self.b_stores:
            return "BIK only"
        return "Not in either dashboard"

    def used_m(self):
        out = set()
        for s in self.m_stores:
            out |= s.used_signals()
        return out

    def used_b(self):
        out = set()
        for s in self.b_stores:
            out |= s.used_signals()
        return out

    def missing_m(self):
        return [k for k in SIGNALS if k not in self.used_m()]

    def missing_b(self):
        return [k for k in BIK_SIGNALS if k not in self.used_b()]

    @property
    def orders(self):
        return sum(s.orders for s in self.m_stores)

    @property
    def gmv(self):
        return sum(s.gmv for s in self.m_stores)

    @property
    def messages(self):
        return sum(s.messages for s in self.b_stores)

    def used_count(self):
        return len(self.used_m()) + len(self.used_b())

    def missing_count(self):
        return len(self.missing_m()) + len(self.missing_b())

    def missing_names(self):
        if not self.matched:
            return []
        out = []
        if self.m_stores:
            out += [f"Manifest: {SIGNALS[k]}" for k in self.missing_m()]
        else:
            out.append("Manifest product not adopted (all 17 modules)")
        if self.b_stores:
            out += [f"BIK: {BIK_SIGNALS[k][0]}" for k in self.missing_b()]
        else:
            out.append("BIK product not adopted (all 11 modules)")
        return out

    def momentum(self):
        return 1.0 if self.mrr > self.sub_jul * 1.1 > 0 else 0.0

    def engagement(self):
        return min(1.0, self.used_count() / 12) if self.matched else 0.0

    def brand_size(self):
        if self.m_stores:
            return min(1.0, math.log10(max(self.orders, 1)) / 5)
        if self.b_stores:
            return min(1.0, math.log10(self.messages + 1) / 6)
        return 0.4

    def score(self):
        size = min(1.0, math.log10(self.mrr + 1) / math.log10(2000))
        gap = self.missing_count() / TOTAL_SIGNALS if self.matched else 0.5
        return round(100 * (0.30 * size + 0.25 * self.recommender.strength(self) + 0.10 * self.engagement()
                            + 0.15 * self.brand_size() + 0.10 * BUCKET_WEIGHT.get(self.bucket, 0.4)
                            + 0.05 * gap + 0.05 * self.momentum()), 1)

    def picks(self):
        return self.recommender.pick(self)

    def also_consider(self):
        shown = {r.spec.key for r in self.picks()}
        return [r.spec.name for r in self.recommender.rank(self) if r.spec.key not in shown][:4]

    def play(self):
        if not self.matched:
            return "No usage on record in either dashboard: confirm what is live, then introduce the core features"
        total = (len(SIGNALS) if self.m_stores else 0) + (len(BIK_SIGNALS) if self.b_stores else 0)
        return f"{self.mix}: {self.used_count()} of {total} features live. Lead with {self.picks()[0].spec.name}." \
            if self.picks() else f"{self.mix}: every measurable feature is live. Discuss plan upgrade and new channels."

    def reasoning(self, news=""):
        parts = [f"{BUCKET_LABEL.get(self.bucket, self.bucket)} paying ${self.mrr:,.0f}/mo"]
        if self.mrr > self.sub_jul * 1.1 > 0:
            parts.append(f"spend grew from ${self.sub_jul:,.0f} to ${self.mrr:,.0f}, so there is appetite to buy more")
        if self.m_stores:
            parts.append(f"{self.orders:,} orders and ${self.gmv:,.0f} in sales through Manifest in Sep")
        if self.b_stores:
            parts.append(f"{self.messages:,.0f} BIK messages sent in Sep")
        if not self.matched:
            parts.append("not found in the Manifest or BIK dashboards for Sep, so feature gaps are unknown")
        if news:
            parts.append(news.split(" (")[0].rstrip("."))
        return "; ".join(parts) + "."


class Shortlister:
    def __init__(self, sheet, outreach, manifest, bik):
        self.rows = list(csv.DictReader(open(sheet)))
        self.outreach = outreach
        self.manifest = manifest
        self.bik = bik
        self.recommender = Recommender(FeatureCatalog(), PeerStats(FeatureCatalog(), manifest.stores, bik.stores))

    def accounts(self):
        best = {}
        for r in self.rows:
            if r["CSM"].strip() not in CSMS:
                continue
            k = (r["CSM"].strip(), norm(r["Brand name"]))
            if k not in best or float(r["Subscription August"] or 0) > float(best[k]["Subscription August"] or 0):
                best[k] = r
        return [Account(r, self.manifest.match(r["Brand name"]), self.bik.match(r["Brand name"]), self.recommender)
                for r in best.values() if float(r["Subscription August"] or 0) > 0]

    def run(self, per_csm=10):
        eligible, skipped = [], []
        for a in self.accounts():
            hit = self.outreach.hit(a.brand, a.csm)
            (skipped if hit else eligible).append((a, hit))
        top = {}
        for csm in CSMS:
            mine = sorted((a for a, _ in eligible if a.csm == csm), key=lambda a: -a.score())
            top[csm] = mine[:per_csm]
        return top, skipped, len(eligible)


class ReportWriter:
    def __init__(self, week, outdir):
        self.week = week
        self.outdir = Path(outdir)

    def write(self, top, skipped, news):
        self.outdir.mkdir(parents=True, exist_ok=True)
        self._write_csv(top, news)
        self._write_excluded(skipped)
        self._write_markdown(top, news)

    def _write_csv(self, top, news):
        header = ["csm", "rank", "account", "bucket", "score", "mrr_aug", "product_mix",
                  "manifest_modules_used_of_17", "bik_modules_used_of_11", "manifest_orders_sep26", "play", "reasoning"]
        for n in range(1, 4):
            header += [f"rec{n}_feature", f"rec{n}_pitch", f"rec{n}_evidence", f"rec{n}_peers", f"rec{n}_ask"]
        header += ["also_consider", "news"]
        with open(self.outdir / "shortlist.csv", "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(header)
            for csm, accts in top.items():
                for i, a in enumerate(accts, 1):
                    row = [csm, i, a.brand, a.bucket, a.score(), a.mrr, a.mix,
                           len(a.used_m()) if a.m_stores else "n/a", len(a.used_b()) if a.b_stores else "n/a",
                           a.orders or "", a.play(), a.reasoning(news.get(a.brand, ""))]
                    picks = a.picks()
                    for n in range(3):
                        rec = picks[n] if n < len(picks) else None
                        row += ([rec.spec.name, rec.spec.benefit, rec.evidence, rec.peer_note, rec.spec.ask]
                                if rec else [""] * 5)
                    row += ["; ".join(a.also_consider()), news.get(a.brand, "")]
                    w.writerow(row)

    def _write_excluded(self, skipped):
        with open(self.outdir / "excluded_outreach_mtd.csv", "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["csm", "account", "mrr_aug", "touched_by", "channel"])
            for a, h in sorted(skipped, key=lambda x: (x[0].csm, -x[0].mrr)):
                w.writerow([a.csm, a.brand, a.mrr, h["by"], h["channel"]])

    def _write_markdown(self, top, news):
        lines = [f"# CSM upsell shortlist, {self.week}", ""]
        for csm, accts in top.items():
            lines += [f"## {csm}", ""]
            for i, a in enumerate(accts, 1):
                lines += self._account_block(i, a, news.get(a.brand, ""))
        (self.outdir / "shortlist.md").write_text("\n".join(lines))

    def _account_block(self, rank, a, news):
        lines = [f"### {rank}. {a.brand} ({a.mix}, priority {a.score()})",
                 f"- **Why now:** {a.reasoning(news)}",
                 f"- **Where they stand:** {a.play()}"]
        picks = a.picks()
        if not picks:
            lines.append("- **Lead with:** nothing measurable is missing; discuss plan upgrade and new channels.")
        for n, rec in enumerate(picks, 1):
            lines.append(f"- **Lead with #{n}, {rec.spec.name}:** {rec.spec.benefit}")
            lines.append(f"  - Evidence: {rec.evidence}." + (f" {rec.peer_note.capitalize()}." if rec.peer_note else ""))
            lines.append(f"  - Open with: \"{rec.spec.ask}\"")
        if a.also_consider():
            lines.append(f"- **Also on the table:** {', '.join(a.also_consider())}")
        if news:
            lines.append(f"- **News:** {news}")
        lines.append("")
        return lines


class CsmResolver:
    """Maps a typed name ('luv', 'Rahul S') to the sheet's CSM label."""

    @staticmethod
    def resolve(typed):
        if not typed or typed.strip().lower() == "all":
            return list(CSMS)
        t = norm(typed)
        hits = [c for c in CSMS if t in norm(c)]
        if len(hits) != 1:
            raise SystemExit(f"CSM '{typed}' is not one of: {', '.join(CSMS)}")
        return hits


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="Weekly CSM upsell shortlist")
    ap.add_argument("--csm", default="all", help="CSM name (Luv, Nishant, Rahul, Prial) or 'all'")
    ap.add_argument("--manifest", required=True, help="comma-separated Manifest GSM json files")
    ap.add_argument("--bik", required=True, help="comma-separated BIK GSM json files")
    ap.add_argument("--sheet", default=str(Path(__file__).resolve().parent / "assets" / "csm_accounts.csv"))
    ap.add_argument("--outreach", default="data/outreach_mtd.csv")
    ap.add_argument("--news", default="data/news.json")
    ap.add_argument("--week", default="2026-W43")
    ap.add_argument("--out", default=None)
    ap.add_argument("--per-csm", type=int, default=10)
    args = ap.parse_args()

    chosen = CsmResolver.resolve(args.csm)
    sl = Shortlister(args.sheet, OutreachLog(args.outreach), ManifestIndex(args.manifest.split(",")),
                     BikIndex(args.bik.split(",")))
    news = json.load(open(args.news)) if Path(args.news).exists() else {}
    top, skipped, n = sl.run(args.per_csm)
    top = {c: v for c, v in top.items() if c in chosen}
    skipped = [(a, h) for a, h in skipped if a.csm in chosen]
    out = args.out or f"reports/{args.week}" + ("" if len(chosen) > 1 else "/" + chosen[0].split()[0].lower())
    ReportWriter(args.week, out).write(top, skipped, news)
    print(out, {c: len(v) for c, v in top.items()}, "excluded", len(skipped))
