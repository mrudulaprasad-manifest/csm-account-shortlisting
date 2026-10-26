"""Render reports/<week>/shortlist.csv to a PDF via headless Chromium."""
import csv
import html
import subprocess
import sys
from pathlib import Path


class PdfReport:
    CSS = """body{font-family:Helvetica,Arial,sans-serif;font-size:10.5px;color:#1a1a1a;margin:0}
h1{font-size:20px;margin:0 0 4px}h2{font-size:15px;margin:18px 0 6px;padding-bottom:3px;border-bottom:2px solid #444}
.card{border:1px solid #ccc;border-radius:5px;padding:7px 10px;margin:7px 0;page-break-inside:avoid}
.card h3{font-size:12px;margin:0 0 4px}.meta{color:#555;margin-bottom:4px}
.card p{margin:2px 0;line-height:1.35}b{color:#222}"""

    CHROME = "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"

    def __init__(self, week, csv_path):
        self.week = week
        self.rows = list(csv.DictReader(open(csv_path)))

    def html(self):
        out = [f"<html><head><meta charset='utf-8'><style>{self.CSS}</style></head><body>",
               f"<h1>CSM upsell shortlist, {html.escape(self.week)}</h1>"]
        csm = None
        for r in self.rows:
            if r["csm"] != csm:
                csm = r["csm"]
                out.append(f"<h2>{html.escape(csm)}</h2>")
            man = r["manifest_modules_used_of_17"]
            bik = r["bik_modules_used_of_11"]
            meta = (f"{r['product_mix']} | {r['bucket']} | MRR ${float(r['mrr_aug']):,.0f}"
                    + (f" | Manifest modules {man}/17" if man != "n/a" else "")
                    + (f" | BIK modules {bik}/11" if bik != "n/a" else ""))
            pitch = "".join(f"<li>{html.escape(p.strip())}</li>" for p in r["what_to_pitch"].split(" | "))
            news = f"<p><b>News:</b> {html.escape(r['news'])}</p>" if r["news"] else ""
            out.append(
                f"<div class='card'><h3>{r['rank']}. {html.escape(r['account'])} (score {r['score']})</h3>"
                f"<div class='meta'>{html.escape(meta)}</div>"
                f"<p><b>Play:</b> {html.escape(r['play'])}</p>"
                f"<p><b>Why now:</b> {html.escape(r['reasoning'])}</p>"
                f"<p><b>Missing features:</b> {html.escape(r['missing_features'])}</p>"
                f"<p><b>What to pitch:</b></p><ul style='margin:2px 0 2px 16px;padding:0'>{pitch}</ul>{news}</div>")
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
