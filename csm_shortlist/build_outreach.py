"""Turn this month's HubSpot activity into data/outreach_mtd.csv rows for one CSM.

Inputs are the raw HubSpot results saved by the tool:
  --activity: TSV text from query_crm_data (MEETING/NOTE/CALL), first column 'Company name'
  --emails:   JSON from search_crm_objects on EMAIL (outbound only), uses hs_email_to_email
"""
import argparse
import csv
import json
import re


def norm(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


class OutreachBuilder:
    def __init__(self, csm_first_name, sheet_path):
        self.by = csm_first_name
        self.brands = {norm(r["Brand name"]): r["Brand name"]
                       for r in csv.DictReader(open(sheet_path)) if r["CSM"].split()[0] == csm_first_name}
        self.rows = {}

    def _add(self, key, channel):
        if key:
            self.rows.setdefault(key, set()).add(channel)

    def from_activity(self, tsv_path, channel):
        for line in open(tsv_path).read().splitlines()[1:]:
            name = line.split("\t")[0]
            if name and not name.startswith("Showing"):
                self._add(norm(name), channel)

    def from_emails(self, json_path):
        for r in json.load(open(json_path))["results"]:
            to = r["properties"].get("hs_email_to_email") or ""
            for addr in to.split(";"):
                label = norm(addr.split("@")[-1].rsplit(".", 1)[0])
                for b in self.brands:
                    if len(label) >= 5 and len(b) >= 4 and (b.startswith(label) or label.startswith(b)):
                        self._add(b, "email")

    def write(self, path, mode="a"):
        with open(path, mode, newline="") as fh:
            w = csv.writer(fh)
            if mode == "w":
                w.writerow(["key", "by", "channel"])
            for k, ch in sorted(self.rows.items()):
                w.writerow([k, self.by, "/".join(sorted(ch))])


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--csm", required=True)
    ap.add_argument("--sheet", required=True)
    ap.add_argument("--meetings"); ap.add_argument("--notes"); ap.add_argument("--calls"); ap.add_argument("--emails")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    b = OutreachBuilder(a.csm, a.sheet)
    for path, ch in ((a.meetings, "meeting"), (a.notes, "note"), (a.calls, "call")):
        if path:
            b.from_activity(path, ch)
    if a.emails:
        b.from_emails(a.emails)
    b.write(a.out, "w")
