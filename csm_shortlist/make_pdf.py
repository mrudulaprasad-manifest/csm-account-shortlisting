"""Render the weekly account-health workbook to a PDF via headless Chromium, one card per account."""
import html
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from openpyxl import load_workbook

from workbook import BAND_FILLS, COLUMNS, SECTION_FILLS

CSM_SHEETS = ("Luv", "Nishant", "Rahul", "Prial")
HEADLINE = ("#", "Account", "Segment", "MRR (USD)", "MRR source", "Products", "Priority score")


class PdfReport:
    CSS = """@page{size:A4 landscape;margin:10mm}
body{font-family:Helvetica,Arial,sans-serif;font-size:10px;color:#1a1a1a;margin:0}
h1{font-size:20px;margin:0 0 2px}.sub{color:#555;margin-bottom:6px}
h2{font-size:15px;margin:0 0 8px;padding-bottom:3px;border-bottom:2px solid #444;page-break-before:always}
h2.first{page-break-before:avoid}
.card{border:1px solid #bbb;border-radius:5px;margin:0 0 9px;page-break-inside:avoid;overflow:hidden}
.head{background:#1F2937;color:#fff;padding:4px 9px;font-size:12px;font-weight:bold}
.head span{font-weight:normal;font-size:10px;margin-left:10px;color:#d1d5db}
.grid{display:grid;grid-template-columns:repeat(3,1fr)}
.sec{padding:4px 8px;border-top:1px solid #ddd;border-right:1px solid #ddd;line-height:1.35}
.sec h4{margin:0 0 3px;font-size:9.5px;text-transform:uppercase;letter-spacing:.4px}
.sec p{margin:1px 0;white-space:pre-line}.k{color:#555}
table{border-collapse:collapse;width:100%;margin-bottom:10px}
td,th{border:1px solid #ccc;padding:3px 6px;text-align:left;vertical-align:top;font-size:9.5px}
th{background:#1F2937;color:#fff}"""

    CHROME = "/opt/pw-browsers/chromium"

    def __init__(self, week, xlsx_path):
        self.week = week
        self.wb = load_workbook(xlsx_path, data_only=True)

    @staticmethod
    def _fmt(column, value):
        if value is None or value == "":
            return "-"
        if isinstance(value, datetime):
            return value.strftime("%d %b %Y")
        if isinstance(value, (int, float)) and column.title == "MRR (USD)":
            return f"${value:,.0f}"
        if isinstance(value, (int, float)) and "%" in column.title:
            return f"{value:.1f}%"
        if isinstance(value, (int, float)):
            return f"{value:,.1f}".rstrip("0").rstrip(".") if isinstance(value, float) else f"{value:,}"
        return str(value)

    def _card(self, row):
        values = {c.title: self._fmt(c, v) for c, v in zip(COLUMNS, row)}
        sections = {}
        for column, value in zip(COLUMNS, row):
            if column.title not in HEADLINE:
                sections.setdefault(column.section, []).append((column.title, self._fmt(column, value)))
        head = (f"{html.escape(values['#'])}. {html.escape(values['Account'])}"
                f"<span>{html.escape(values['Products'])} | {html.escape(values['Segment'])} | MRR {values['MRR (USD)']}"
                f" | score {html.escape(values['Priority score'])}</span>")
        panels = []
        for name, items in sections.items():
            body = "".join(f"<p><span class='k'>{html.escape(t)}:</span> {html.escape(v)}</p>" for t, v in items)
            panels.append(f"<div class='sec' style='background:#{BAND_FILLS[name]}'>"
                          f"<h4 style='color:#{SECTION_FILLS[name]}'>{html.escape(name)}</h4>{body}</div>")
        return f"<div class='card'><div class='head'>{head}</div><div class='grid'>{''.join(panels)}</div></div>"

    def _table(self, title, sheet):
        rows = list(self.wb[sheet].iter_rows(values_only=True))
        head = "".join(f"<th>{html.escape(str(h))}</th>" for h in rows[0])
        body = "".join("<tr>" + "".join(f"<td>{html.escape(self._cell(v))}</td>" for v in r) + "</tr>"
                       for r in rows[1:] if any(r))
        return f"<h2>{html.escape(title)}</h2><table><tr>{head}</tr>{body}</table>"

    @staticmethod
    def _cell(v):
        if isinstance(v, float):
            return f"${v:,.0f}"
        return "" if v is None else str(v)

    def html(self):
        out = [f"<html><head><meta charset='utf-8'><style>{self.CSS}</style></head><body>",
               f"<h1>CSM account health, {html.escape(self.week)}</h1>",
               "<div class='sub'>Usage is September 2026 from the Manifest and BIK dashboards. "
               "Definitions are in the Notes section at the end.</div>"]
        for i, name in enumerate(CSM_SHEETS):
            rows = list(self.wb[name].iter_rows(min_row=3, values_only=True))
            out.append(f"<h2 class='{'first' if i == 0 else ''}'>{name}</h2>")
            out += [self._card(r) for r in rows if r[1]]
        out.append(self._table("Retention first", "Retention first"))
        out.append(self._table("Notes", "Notes"))
        out.append("</body></html>")
        return "\n".join(out)

    def write(self, pdf_path):
        page = Path(pdf_path).with_suffix(".html")
        page.write_text(self.html())
        subprocess.run([self.CHROME, "--headless", "--no-sandbox", "--disable-gpu", "--no-pdf-header-footer",
                        f"--print-to-pdf={pdf_path}", page.resolve().as_uri()], check=True, capture_output=True)
        page.unlink()


if __name__ == "__main__":
    PdfReport(sys.argv[3] if len(sys.argv) > 3 else "", sys.argv[1]).write(sys.argv[2])
