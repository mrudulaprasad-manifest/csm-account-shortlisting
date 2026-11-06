"""Build the weekly CSM account list from HubSpot deal exports.

Accounts are companies on a non-churned Service BIK / Service Manifest deal owned by one of the CSMs.
MRR is the "amount in company currency" (USD) of the company's most recent closed-won deal in the
India Commercial + Midmarket or US pipeline. Only the latest won deal counts: older won deals are
earlier plans or renewals of the same subscription, so summing them would overstate MRR.
"""
import argparse
import csv
from pathlib import Path

ENTERPRISE_MIN = 500
MM_MIN = 200


class WonDeals:
    """Latest closed-won deal per company."""

    def __init__(self, path):
        self.latest = {}
        for r in csv.DictReader(open(path)):
            amount = self._amount(r)
            if amount is None:
                continue
            cur = self.latest.get(r["company_id"])
            if cur is None or r["closedate"] > cur[0]:
                self.latest[r["company_id"]] = (r["closedate"], amount, r["pipeline"])

    @staticmethod
    def _amount(row):
        for field in ("amount_in_home_currency", "amount"):
            try:
                return float(row[field])
            except ValueError:
                continue
        return None

    def mrr(self, company_id):
        hit = self.latest.get(company_id)
        return hit[1] if hit else 0.0


class ServiceAccounts:
    """Non-churned service deals, one row per (CSM, company)."""

    def __init__(self, path):
        self.rows = {}
        for r in csv.DictReader(open(path)):
            if "churn" in r["stage_label"].lower():
                continue
            self.rows.setdefault((r["csm"], r["company_id"]), r["company_name"].strip())


def bucket(mrr):
    if mrr >= ENTERPRISE_MIN:
        return "Enterprise + Key accounts"
    return "MM" if mrr >= MM_MIN else "SMB"


def build(service_path, won_path, out_path):
    won = WonDeals(won_path)
    accounts = ServiceAccounts(service_path)
    missing = []
    with open(out_path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["Brand name", "CSM", "Subscription July", "Subscription August", "Revenue Bucket (Based on MRR)"])
        for (csm, company_id), name in sorted(accounts.rows.items()):
            mrr = won.mrr(company_id)
            if mrr <= 0:
                missing.append((csm, name))
                continue
            w.writerow([name, csm, "", round(mrr, 2), bucket(mrr)])
    return len(accounts.rows), missing


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--service", required=True)
    ap.add_argument("--won", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    total, missing = build(a.service, a.won, a.out)
    print(f"{total} active service accounts, {total - len(missing)} with a won-deal MRR, {len(missing)} without")
    Path(a.out).with_suffix(".missing_mrr.csv").write_text(
        "csm,account\n" + "\n".join(f'{c},"{n}"' for c, n in missing))
