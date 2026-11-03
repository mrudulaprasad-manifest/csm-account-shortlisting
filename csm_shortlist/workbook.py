"""Weekly CSM account-health workbook: one sheet per CSM, grouped into the sections the CSM leads review.

Sections per account: Product usage (critical features only), Business outcomes, Customer engagement,
Issues & satisfaction, Renewal, plus the recommended play. Excluded and retention-first accounts get their own sheets.
"""
import json
from datetime import date, datetime
from pathlib import Path

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

# Features that move outcomes; everything else stays in the recommender's "also on the table" list.
CRITICAL_MANIFEST = ("AGENTS", "AI_PRODUCT_ASSISTANT", "ABANDONED_CART_FLOW", "PRODUCT_ADD_TO_CART", "LEADS",
                     "INTEGRATIONS")
CRITICAL_BIK = ("WA_CAMPAIGNS", "WA_JOURNEYS", "BIK_AI_AGENT", "WIDGETS", "CRM", "EMAIL")
# channel label -> columns where any value > 0 means the channel is live
BIK_CHANNELS = {
    "WhatsApp": ["Overall Health-totalWhatsappMessages"],
    "Email": ["Overall Health-totalEmailMessages"],
    "SMS": ["Overall Health-totalSMSMessages"],
    "RCS": ["Overall Health-totalRCSMessages"],
    "Web push": ["Campaign Metrics-numberOfWebpushMessages"],
    "Instagram": ["Journey Metrics-activeInstagramJourneys", "Ai metrics-aiDirectRevenueInstagram"],
}
NOT_ON_PRODUCT = "Not on BIK"
NOT_ON_MANIFEST = "Not on Manifest"
RENEWAL_SOON_DAYS = 60

SECTION_FILLS = {
    "Account": "1F2937", "Product usage": "1D4ED8", "Business outcomes": "047857", "Customer engagement": "B45309",
    "Issues & satisfaction": "B91C1C", "Renewal": "6D28D9", "Recommended play": "374151",
}
BAND_FILLS = {
    "Account": "F3F4F6", "Product usage": "EFF6FF", "Business outcomes": "ECFDF5", "Customer engagement": "FFFBEB",
    "Issues & satisfaction": "FEF2F2", "Renewal": "F5F3FF", "Recommended play": "F9FAFB",
}
THIN = Side(style="thin", color="D1D5DB")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


class Column:
    def __init__(self, section, title, width, number_format=None, center=False):
        self.section = section
        self.title = title
        self.width = width
        self.number_format = number_format
        self.center = center


COLUMNS = [
    Column("Account", "#", 5, center=True),
    Column("Account", "Account", 24),
    Column("Account", "Segment", 12),
    Column("Account", "MRR (USD)", 11, '"$"#,##0'),
    Column("Account", "MRR source", 14),
    Column("Account", "Products", 14),
    Column("Account", "Priority score", 9, "0.0", center=True),
    Column("Product usage", "Manifest critical features live", 12, center=True),
    Column("Product usage", "Manifest critical features not live", 30),
    Column("Product usage", "BIK critical features live", 12, center=True),
    Column("Product usage", "BIK critical features not live", 30),
    Column("Business outcomes", "Revenue contribution % (BIK)", 13, '0.0"%"', center=True),
    Column("Business outcomes", "Revenue contribution % (Manifest)", 13, '0.0"%"', center=True),
    Column("Business outcomes", "Conversations (Manifest)", 13, "#,##0", center=True),
    Column("Business outcomes", "AI conversations (BIK)", 13, "#,##0", center=True),
    Column("Business outcomes", "Support tickets closed (BIK)", 13, "#,##0", center=True),
    Column("Business outcomes", "Widgets live (BIK)", 10, "#,##0", center=True),
    Column("Business outcomes", "Channels live (BIK)", 22),
    Column("Customer engagement", "Last chat date", 13, "dd-mmm-yyyy", center=True),
    Column("Customer engagement", "Last chat summary", 52),
    Column("Customer engagement", "Last HubSpot touch", 13, "dd-mmm-yyyy", center=True),
    Column("Issues & satisfaction", "Issues raised", 46),
    Column("Renewal", "Renewal date", 13, "dd-mmm-yyyy", center=True),
    Column("Renewal", "Renewal status", 24),
    Column("Recommended play", "Why now", 44),
    Column("Recommended play", "Lead pitch", 40),
    Column("Recommended play", "Also pitch", 34),
    Column("Recommended play", "News", 34),
]


def parse_date(value):
    if not value:
        return None
    return datetime.strptime(value[:10], "%Y-%m-%d").date()


class StoreMetrics:
    """Sep dashboard numbers for one account, summed over its BIK and Manifest stores."""

    def __init__(self, account):
        self.m_stores = account.m_stores
        self.b_stores = account.b_stores

    @staticmethod
    def _sum(stores, column):
        return sum(s.raw.get(column) or 0 for s in stores)

    def bik_revenue_percent(self):
        if not self.b_stores:
            return NOT_ON_PRODUCT
        biggest = max(self.b_stores, key=lambda s: s.messages)
        return biggest.raw.get("Overall Health-bikRevenuePercent") or 0

    def manifest_revenue_percent(self):
        if not self.m_stores:
            return NOT_ON_MANIFEST
        biggest = max(self.m_stores, key=lambda s: s.orders)
        return biggest.raw.get("Revenue-contributionPercentage") or 0

    def manifest_conversations(self):
        return self._sum(self.m_stores, "Support Metrics-totalSessions") if self.m_stores else NOT_ON_MANIFEST

    def bik_ai_conversations(self):
        return self._sum(self.b_stores, "Ai metrics-aiSessions") if self.b_stores else NOT_ON_PRODUCT

    def bik_tickets(self):
        return self._sum(self.b_stores, "crm metrics-ticketsClosed") if self.b_stores else NOT_ON_PRODUCT

    def bik_widgets(self):
        return self._sum(self.b_stores, "Widget Revenue-activeWidgets") if self.b_stores else NOT_ON_PRODUCT

    def bik_channels(self):
        if not self.b_stores:
            return NOT_ON_PRODUCT
        live = [name for name, cols in BIK_CHANNELS.items()
                if any(self._sum(self.b_stores, c) > 0 for c in cols)]
        return f"{len(live)}: {', '.join(live)}" if live else "0"


class CallLog:
    """Last customer call per account, from the Fireflies lookups saved as last_calls_*.json."""

    def __init__(self, paths):
        self.by_account = {}
        for path in paths:
            for row in json.load(open(path)):
                self.by_account[row["account"]] = row

    def get(self, account):
        return self.by_account.get(account)


class WorkbookWriter:
    def __init__(self, week, as_of, manifest_labels, bik_labels, calls, sanitiser):
        self.week = week
        self.as_of = as_of
        self.manifest_labels = manifest_labels
        self.bik_labels = bik_labels
        self.calls = calls
        self.sanitiser = sanitiser

    def write(self, path, top, removed, skipped, flagged, news):
        wb = Workbook()
        self._summary_sheet(wb.active, top, removed, skipped, flagged)
        for csm, accounts in top.items():
            self._csm_sheet(wb.create_sheet(csm.split()[0]), accounts, news)
        self._excluded_sheet(wb.create_sheet("Excluded"), removed, skipped)
        self._retention_sheet(wb.create_sheet("Retention first"), flagged)
        self._notes_sheet(wb.create_sheet("Notes"))
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        wb.save(path)

    def _critical(self, used, keys, labels, on_product):
        if not on_product:
            return "-", "-"
        missing = [labels[k] if k in labels else k for k in keys if k not in used]
        live = [k for k in keys if k in used]
        return f"{len(live)} of {len(keys)}", ", ".join(missing) or "None - all live"

    def _manifest_labels(self, key):
        return self.manifest_labels[key]

    def _usage_cells(self, account):
        manifest_label = {k: self.manifest_labels[k] for k in CRITICAL_MANIFEST}
        bik_label = {k: self.bik_labels[k][0] for k in CRITICAL_BIK}
        m_live, m_missing = self._critical(account.used_m(), CRITICAL_MANIFEST, manifest_label, bool(account.m_stores))
        b_live, b_missing = self._critical(account.used_b(), CRITICAL_BIK, bik_label, bool(account.b_stores))
        return [m_live, m_missing, b_live, b_missing]

    def _issues(self, account, call):
        issues = []
        if call and call.get("issues_raised"):
            issues += call["issues_raised"]
        if account.context:
            issues += [p.text for p in account.context.pains] + list(account.context.risk_flags)
        seen, unique = set(), []
        for i in issues:
            if i.lower() not in seen:
                seen.add(i.lower())
                unique.append(i)
        return "\n".join(f"- {i}" for i in unique) if unique else "None recorded in calls"

    def _last_chat(self, account):
        """Prefers the fresh Fireflies lookup, then the call context saved earlier, else says no call is on record."""
        call = self.calls.get(account.brand)
        if call and call.get("last_call_date"):
            return parse_date(call["last_call_date"]), call.get("summary") or "", call
        ctx = account.context
        if ctx and ctx.meetings:
            summary = ctx.business + (" Open: " + "; ".join(o.text for o in ctx.open_items[:2]) if ctx.open_items else "")
            return parse_date(ctx.last_meeting), summary.strip(), call
        return None, "No recorded customer call", call

    def _renewal(self, account):
        company = self.sanitiser.company(account.brand, account.csm)
        if not company:
            return None, "Not found in HubSpot", None
        renewal = parse_date(company.get("renewal_date") or company.get("bik_renewal_date"))
        touched = parse_date(company.get("notes_last_contacted"))
        if not renewal:
            return None, "Not set in HubSpot", touched
        days = (renewal - self.as_of).days
        if days < 0:
            return renewal, f"Date passed {-days} days ago - confirm in HubSpot", touched
        if days <= RENEWAL_SOON_DAYS:
            return renewal, f"Renews in {days} days", touched
        return renewal, f"Renews in {days} days", touched

    def _row(self, rank, account, news):
        metrics = StoreMetrics(account)
        chat_date, chat_summary, call = self._last_chat(account)
        renewal, status, touched = self._renewal(account)
        picks = account.picks()
        lead = f"{picks[0].spec.name}\nOpen with: \"{picks[0].spec.ask}\"" if picks else "Nothing measurable is missing"
        others = "\n".join(f"{n}. {p.spec.name}" for n, p in enumerate(picks[1:3], 2))
        return [rank, account.brand, account.bucket, account.mrr, account.mrr_source, account.mix, account.score(),
                *self._usage_cells(account),
                metrics.bik_revenue_percent(), metrics.manifest_revenue_percent(), metrics.manifest_conversations(),
                metrics.bik_ai_conversations(), metrics.bik_tickets(), metrics.bik_widgets(), metrics.bik_channels(),
                chat_date, chat_summary, touched,
                self._issues(account, call),
                renewal, status,
                account.reasoning(news.get(account.brand, "")), lead, others, news.get(account.brand, "")]

    def _csm_sheet(self, ws, accounts, news):
        rows = [self._row(i, a, news) for i, a in enumerate(accounts, 1)]
        self._grid(ws, rows)
        renewal_col = next(i for i, c in enumerate(COLUMNS, 1) if c.title == "Renewal status")
        for r in range(3, 3 + len(rows)):
            cell = ws.cell(r, renewal_col)
            if "passed" in str(cell.value) or "Not " in str(cell.value):
                cell.font = Font(color="B91C1C", bold=True)
            elif "Renews in" in str(cell.value) and int(str(cell.value).split()[2]) <= RENEWAL_SOON_DAYS:
                cell.font = Font(color="B45309", bold=True)

    def _grid(self, ws, rows):
        sections = []
        for i, col in enumerate(COLUMNS, 1):
            if not sections or sections[-1][0] != col.section:
                sections.append([col.section, i, i])
            sections[-1][2] = i
            head = ws.cell(2, i, col.title)
            head.font = Font(bold=True, color="FFFFFF")
            head.fill = PatternFill("solid", fgColor=SECTION_FILLS[col.section])
            head.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
            head.border = BORDER
            ws.column_dimensions[get_column_letter(i)].width = col.width
        for name, start, end in sections:
            ws.merge_cells(start_row=1, start_column=start, end_row=1, end_column=end)
            band = ws.cell(1, start, name)
            band.font = Font(bold=True, size=12, color="FFFFFF")
            band.fill = PatternFill("solid", fgColor=SECTION_FILLS[name])
            band.alignment = Alignment(horizontal="center", vertical="center")
        for r, row in enumerate(rows, 3):
            for c, value in enumerate(row, 1):
                col = COLUMNS[c - 1]
                cell = ws.cell(r, c, value)
                cell.fill = PatternFill("solid", fgColor=BAND_FILLS[col.section])
                cell.border = BORDER
                cell.alignment = Alignment(wrap_text=True, vertical="top",
                                           horizontal="center" if col.center else "left")
                if col.number_format and not isinstance(value, str):
                    cell.number_format = col.number_format
            ws.row_dimensions[r].height = self._row_height(row)
        ws.row_dimensions[1].height = 22
        ws.row_dimensions[2].height = 44
        ws.freeze_panes = "C3"
        ws.sheet_view.zoomScale = 90

    @staticmethod
    def _row_height(row):
        """Wrapped text does not auto-size in Excel for merged-header sheets, so estimate the tallest cell."""
        lines = 1
        for col, value in zip(COLUMNS, row):
            if isinstance(value, str):
                lines = max(lines, sum(-(-max(len(part), 1) // int(col.width * 1.05)) for part in value.split("\n")))
        return min(max(lines * 15, 60), 400)

    def _table(self, ws, header, rows, widths, start_row=1):
        for i, h in enumerate(header, 1):
            cell = ws.cell(start_row, i, h)
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor=SECTION_FILLS["Account"])
            cell.border = BORDER
            cell.alignment = Alignment(wrap_text=True, vertical="center")
            ws.column_dimensions[get_column_letter(i)].width = widths[i - 1]
        for r, row in enumerate(rows, start_row + 1):
            for c, value in enumerate(row, 1):
                cell = ws.cell(r, c, value)
                cell.border = BORDER
                cell.alignment = Alignment(wrap_text=True, vertical="top")
                if isinstance(value, float):
                    cell.number_format = '"$"#,##0'
        ws.freeze_panes = ws.cell(start_row + 1, 1)

    def _excluded_sheet(self, ws, removed, skipped):
        rows = [[a.csm, a.brand, a.mrr, r.reason, r.source] for a, r in sorted(removed, key=lambda x: (x[0].csm, -x[0].mrr))]
        rows += [[a.csm, a.brand, a.mrr, f"Already touched this month by {h['by']} ({h['channel']})", "HubSpot activity"]
                 for a, h in sorted(skipped, key=lambda x: (x[0].csm, -x[0].mrr))]
        self._table(ws, ["CSM", "Account", "MRR (USD)", "Why it is not on the list", "Source"], rows, [20, 30, 12, 56, 18])

    def _retention_sheet(self, ws, flagged):
        rows = [[a.csm, a.brand, a.mrr, "\n".join(f"- {f}" for f in a.context.risk_flags), a.context.last_meeting]
                for a in sorted(flagged, key=lambda x: (x.csm, -x.mrr))]
        self._table(ws, ["CSM", "Account", "MRR (USD)", "Risk signals", "Last call"], rows, [20, 28, 12, 90, 12])

    def _summary_sheet(self, ws, top, removed, skipped, flagged):
        ws.title = "Summary"
        ws["A1"] = f"CSM account health - {self.week}"
        ws["A1"].font = Font(bold=True, size=16)
        ws["A2"] = f"As of {self.as_of:%d %b %Y}. Usage is September 2026 from the Manifest and BIK GSM dashboards."
        header = ["CSM", "On this week's list", "Removed: churned / not a customer / already in upsell",
                  "Skipped: already touched this month", "Retention first"]
        rows = []
        for csm, accounts in top.items():
            rows.append([csm, len(accounts), sum(1 for a, _ in removed if a.csm == csm),
                         sum(1 for a, _ in skipped if a.csm == csm), sum(1 for a in flagged if a.csm == csm)])
        self._table(ws, header, rows, [24, 18, 30, 24, 16], start_row=4)
        ws.row_dimensions[4].height = 48

    def _notes_sheet(self, ws):
        lines = [
            ("MRR", "The CSM sheet (August column) is the standard. Where the sheet has zero, MRR is the closed-won deal amount in "
                   "the India Commercial + Mid-market or US pipeline on the company linked to the Service deal, converted to USD "
                   "(HubSpot 'amount in company currency'). The MRR source column says which was used."),
            ("Account list", "Only HubSpot companies at lifecycle stage Customer. Removed when the company is Churned / Closed Lost / "
                             "Lead, the Service deal is in Churned or Churn Notice, or the Service deal is already in an upsell stage "
                             "(Accounts for this week to Escalated). Each removal is listed on the Excluded sheet."),
            ("Critical features - Manifest", "AI Agents, AI Product Assistant, Abandoned Checkout Recovery Nudge, Add to Cart Popup Nudge, "
                                              "Lead Generation, Integrations. 'Live' means sessions, agents, leads or integrations in September."),
            ("Critical features - BIK", "WhatsApp Campaigns, WhatsApp Journeys, BIK AI Agent, Lead/Offer Widgets, CRM / Helpdesk Inbox, "
                                        "Email Campaigns/Journeys. 'Live' means any activity in September."),
            ("Revenue contribution %", "Share of the store's total revenue attributed to the product in September. Brands with several "
                                       "stores show the biggest store."),
            ("Conversations (Manifest)", "Sessions in which a shopper chatted with the Manifest assistant, September."),
            ("AI conversations (BIK)", "BIK AI sessions in September."),
            ("Support tickets closed (BIK)", "Helpdesk tickets closed in September (the dashboard does not carry tickets opened)."),
            ("Widgets / channels (BIK)", "Active widgets; channels with messages sent in September (WhatsApp, Email, SMS, RCS, Web push, "
                                         "Instagram)."),
            ("Last chat", "Newest customer call in Fireflies and its summary. Last HubSpot touch is the last logged activity of any kind."),
            ("Issues raised", "Problems the customer raised on calls plus risk signals from billing and support emails."),
            ("Renewal", "HubSpot company 'Renewal date', falling back to 'Subscription Renewal Dates'. Dates already in the past are "
                        "usually stale records."),
        ]
        self._table(ws, ["Topic", "Definition"], [list(l) for l in lines], [30, 120])
