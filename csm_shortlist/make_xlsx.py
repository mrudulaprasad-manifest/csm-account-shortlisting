"""Write the weekly shortlist as an Excel workbook styled like the CSM Account Health template.

Yellow cells are inputs pulled from HubSpot, the GSM dashboards or call notes; grey cells are computed by
the shortlist scoring. The Scoring tab explains the ranking so a CSM can challenge a pick.
"""
import argparse
import csv
from datetime import date
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

NAVY, SLATE, INK = "253858", "64748B", "243247"
INPUT_FILL, COMPUTED_FILL = "FFF8DF", "EEF2F6"

# (header, csv field, kind, width, number format)
SHORTLIST_COLUMNS = [
    ("CSM", "csm", "input", 16, None),
    ("Rank", "rank", "computed", 7, "0"),
    ("Account", "account", "input", 28, None),
    ("Revenue bucket", "bucket", "input", 20, None),
    ("MRR (USD)", "mrr", "input", 12, "#,##0"),
    ("Priority score", "score", "computed", 10, "0.0"),
    ("Product mix", "product_mix", "input", 16, None),
    ("Manifest modules used (of 17)", "manifest_modules_used_of_17", "input", 16, "0"),
    ("BIK modules used (of 11)", "bik_modules_used_of_11", "input", 16, "0"),
    ("Manifest orders Oct", "manifest_orders_oct26", "input", 14, "#,##0"),
    ("Why now", "reasoning", "computed", 60, None),
    ("Where they stand", "play", "computed", 50, None),
    ("Lead with #1", "rec1_feature", "computed", 28, None),
    ("Why it fits #1", "rec1_pitch", "computed", 50, None),
    ("Evidence #1", "rec1_evidence", "computed", 50, None),
    ("They told us #1", "rec1_heard", "input", 40, None),
    ("Opening question #1", "rec1_ask", "computed", 50, None),
    ("Lead with #2", "rec2_feature", "computed", 28, None),
    ("Why it fits #2", "rec2_pitch", "computed", 50, None),
    ("Lead with #3", "rec3_feature", "computed", 28, None),
    ("Why it fits #3", "rec3_pitch", "computed", 50, None),
    ("Also consider", "also_consider", "computed", 40, None),
    ("Pain points heard", "heard_pains", "input", 50, None),
    ("Open items to close first", "open_items", "input", 50, None),
    ("Risk flags", "risk_flags", "input", 40, None),
    ("Last call", "last_call", "input", 12, None),
]
RETENTION_COLUMNS = [
    ("CSM", "csm", "input", 16, None),
    ("Account", "account", "input", 28, None),
    ("MRR (USD)", "mrr", "input", 12, "#,##0"),
    ("Why held back from upsell", "risk_flags", "input", 90, None),
    ("Last call", "last_meeting", "input", 12, None),
]
SCORING_ROWS = [
    ("Account scope", "Companies on a non-churned Service BIK or Service Manifest deal owned by the CSM.", ""),
    ("MRR source", "Amount in company currency (USD) of the company's most recent closed-won deal in the India "
                   "Commercial + Midmarket or US pipeline.", "Only the latest won deal counts; older ones are earlier plans. Optional: blank when no won deal exists."),
    ("Usage source", "Oct 2026 GSM dashboards (Manifest and BIK). A feature is in use when its sessions or volume are above 0.", ""),
    ("Size weight", 0.30, "log of MRR, capped at $2,000."),
    ("Feature fit weight", 0.25, "How strongly the best-fit unused feature matches peers and what the customer said."),
    ("Brand size weight", 0.15, "Manifest orders, or BIK messages sent when the store is BIK only."),
    ("Engagement weight", 0.10, "Share of up to 12 features already in use."),
    ("Revenue bucket weight", 0.10, "Enterprise/key 1.0, mid-market 0.7, SMB 0.4."),
    ("Feature gap weight", 0.05, "Share of measurable features not yet in use."),
    ("Momentum weight", 0.05, "Only counts when MRR grew by more than 10%; not available this run, so it is 0."),
    ("Risk penalty", 4, "Points removed when call notes carry a watch flag."),
    ("Held back", "Accounts with churn, discontinuation or suspension signals go to Retention first, not the upsell list.", ""),
    ("Already contacted", "Accounts the owning CSM already called, met, noted or emailed this month, or has an open upsell deal on.", "See excluded_outreach_mtd.csv."),
    ("MRR is optional", "Accounts with no closed-won deal in those two pipelines stay in the list with a blank MRR.",
     "Size and bucket (40% of the weight) are left out and the other factors are rescaled to 100. See accounts_missing_mrr.csv."),
]


class Sheet:
    """One styled table in the template's layout: title, subtitle, header on row 6, data below."""

    HEADER_ROW = 6

    def __init__(self, ws, title, subtitle, columns):
        self.ws = ws
        self.columns = columns
        ws["A2"] = title
        ws["A2"].font = Font(bold=True, size=15, color=NAVY)
        ws["A3"] = subtitle
        ws["A3"].font = Font(size=10, color=SLATE)
        for i, (header, _, _, width, _) in enumerate(columns, 1):
            cell = ws.cell(self.HEADER_ROW, i, header)
            cell.font = Font(bold=True, size=10, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor=NAVY)
            cell.alignment = Alignment(wrap_text=True, vertical="center")
            ws.column_dimensions[get_column_letter(i)].width = width
        ws.row_dimensions[self.HEADER_ROW].height = 32

    def add_rows(self, rows):
        for r, row in enumerate(rows, self.HEADER_ROW + 1):
            for c, (_, field, kind, _, fmt) in enumerate(self.columns, 1):
                cell = self.ws.cell(r, c, self._value(row.get(field, ""), fmt))
                cell.font = Font(size=10, color=INK)
                cell.fill = PatternFill("solid", fgColor=INPUT_FILL if kind == "input" else COMPUTED_FILL)
                cell.alignment = Alignment(wrap_text=True, vertical="top")
                if fmt:
                    cell.number_format = fmt
        self.ws.freeze_panes = f"D{self.HEADER_ROW + 1}"
        self.ws.auto_filter.ref = f"A{self.HEADER_ROW}:{get_column_letter(len(self.columns))}{self.HEADER_ROW + len(rows)}"

    @staticmethod
    def _value(raw, fmt):
        if raw in ("", "n/a") or fmt is None:
            return raw
        try:
            number = float(raw)
        except ValueError:
            return raw
        return int(number) if fmt in ("0", "#,##0") and number == int(number) else number


class ShortlistWorkbook:
    def __init__(self, week, out_dir):
        self.week = week
        self.dir = Path(out_dir)

    def build(self, out_path):
        wb = Workbook()
        shortlist = list(csv.DictReader(open(self.dir / "shortlist.csv")))
        retention = list(csv.DictReader(open(self.dir / "retention_first.csv")))
        subtitle = (f"{self.week}, usage month Oct 2026, built {date.today():%b %d, %Y}. "
                    "Yellow cells = inputs from HubSpot, GSM and call notes. Grey cells = computed. Rules: Scoring tab.")
        Sheet(wb.active, f"CSM upsell shortlist, {self.week}", subtitle, SHORTLIST_COLUMNS).add_rows(shortlist)
        wb.active.title = "Shortlist"
        Sheet(wb.create_sheet("Retention first"), "Retention first",
              "Accounts with churn or suspension signals. Fix these before pitching anything new.",
              RETENTION_COLUMNS).add_rows(retention)
        self._scoring(wb.create_sheet("Scoring"))
        wb.save(out_path)
        return len(shortlist), len(retention)

    @staticmethod
    def _scoring(ws):
        ws["A2"] = "Shortlist scoring"
        ws["A2"].font = Font(bold=True, size=15, color=NAVY)
        ws["A3"] = "How accounts are picked and ranked. Weights add to 1.0 before the risk penalty."
        ws["A3"].font = Font(size=10, color=SLATE)
        for i, (h, w) in enumerate((("Setting", 28), ("Value", 70), ("Meaning", 70)), 1):
            cell = ws.cell(5, i, h)
            cell.font = Font(bold=True, size=10, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor=NAVY)
            ws.column_dimensions[get_column_letter(i)].width = w
        for r, row in enumerate(SCORING_ROWS, 6):
            for c, v in enumerate(row, 1):
                cell = ws.cell(r, c, v)
                cell.font = Font(size=10, color=INK)
                cell.fill = PatternFill("solid", fgColor=INPUT_FILL if c == 2 else COMPUTED_FILL)
                cell.alignment = Alignment(wrap_text=True, vertical="top")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--week", required=True)
    ap.add_argument("--dir", required=True, help="folder holding shortlist.csv and retention_first.csv")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    print(ShortlistWorkbook(a.week, a.dir).build(a.out))
