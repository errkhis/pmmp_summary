import html
import json
import re
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, replace
from datetime import date, timedelta
from typing import Optional
from urllib.parse import parse_qs, urljoin, urlparse

import requests as http
from bs4 import BeautifulSoup

from scraper import HEADERS, _parse_price_fr


BASE_URL = "https://www.marchespublics.gov.ma"
SEARCH_URL = f"{BASE_URL}/index.php?page=entreprise.EntrepriseAdvancedSearch&searchAnnCons"
PROCEDURE_TYPE_SELECT = "ctl0$CONTENU_PAGE$AdvancedSearch$procedureType"
PUBLISHED_DATE_START = "ctl0$CONTENU_PAGE$AdvancedSearch$dateMiseEnLigneCalculeStart"
PUBLISHED_DATE_END = "ctl0$CONTENU_PAGE$AdvancedSearch$dateMiseEnLigneCalculeEnd"
SEARCH_BUTTON = "ctl0$CONTENU_PAGE$AdvancedSearch$lancerRecherche"
PAGE_SIZE_SELECT = "ctl0$CONTENU_PAGE$resultSearch$listePageSizeTop"
SIMPLIFIED_OPEN_TENDER_LABEL = "Appel d'offres ouvert simplifié"
SIMPLIFIED_OPEN_TENDER_VALUE = "50"


@dataclass(frozen=True)
class ProcurementSummaryItem:
    reference: str
    title: str
    category: str
    estimated_price: Optional[float]
    caution_amount: Optional[float]
    has_documents: bool
    location: str
    due_date: str
    published_date: str
    consultation_url: str


def fetch_daily_procurements(
    target_date: date,
    browser_api_base_url: Optional[str] = None,
) -> list[ProcurementSummaryItem]:
    target = target_date.strftime("%d/%m/%Y")
    end_date = (target_date + timedelta(days=1)).strftime("%d/%m/%Y")
    items = _fetch_listing_items_via_browser_api(target_date, browser_api_base_url)
    if items is None:
        html = _fetch_listing_html(target, end_date)
        items = _parse_listing_items(html, target)

    return _with_detail_data(items)


def _fetch_listing_items_via_browser_api(
    target_date: date,
    browser_api_base_url: Optional[str],
) -> Optional[list[ProcurementSummaryItem]]:
    base_url = (browser_api_base_url or "").strip().rstrip("/") or os.environ.get("APP_BASE_URL", "").strip().rstrip("/")
    if not base_url:
        return None

    secret = os.environ.get("CRON_SECRET", "").strip()
    if not secret:
        raise RuntimeError("daily_summary_browser_api_requires_cron_secret")

    response = http.get(
        f"{base_url}/api/daily-summary-browser",
        params={"secret": secret, "date": target_date.isoformat()},
        timeout=120,
    )
    response.raise_for_status()
    payload = response.json()
    if not payload.get("ok"):
        raise RuntimeError(payload.get("error") or "daily_summary_browser_api_failed")

    return [
        ProcurementSummaryItem(
            reference=str(item.get("reference") or "").strip(),
            title=str(item.get("title") or "").strip(),
            category=str(item.get("category") or "—").strip() or "—",
            estimated_price=None,
            caution_amount=None,
            has_documents=False,
            location=str(item.get("location") or "—").strip() or "—",
            due_date=str(item.get("due_date") or "—").strip() or "—",
            published_date=str(item.get("published_date") or "").strip(),
            consultation_url=str(item.get("consultation_url") or "").strip(),
        )
        for item in payload.get("items") or []
        if str(item.get("consultation_url") or "").strip()
    ]
def _fetch_listing_html(published_date: str, published_end_date: str) -> str:
    raise RuntimeError(
        "daily_summary_browser_required: APP_BASE_URL is required so the summary endpoint can call /api/daily-summary-browser"
    )


def build_daily_summary_message(
    items: list[ProcurementSummaryItem],
    target_date: date,
) -> str:
    date_label = target_date.strftime("%d/%m/%Y")
    if not items:
        return (
            "📋 <b>Résumé quotidien - Appels d'offres ouverts simplifiés</b>\n\n"
            f"Publié le: <b>{date_label}</b>\n"
            "Total: <b>0 consultation</b>\n\n"
            "Aucune consultation publiée hier."
        )

    return (
        "📋 <b>Résumé quotidien - Appels d'offres ouverts simplifiés</b>\n\n"
        f"Publié le: <b>{date_label}</b>\n"
        f"Total: <b>{len(items)} consultations</b>\n"
        "Le détail complet est joint en fichier HTML."
    )


def build_daily_summary_html_document(
    items: list[ProcurementSummaryItem],
    target_date: date,
) -> str:
    date_label = target_date.strftime("%d/%m/%Y")
    rows = []
    cards = []
    for index, item in enumerate(items, start=1):
        row_class = "row-fournitures" if _norm(item.category).startswith("fourniture") else ""
        title = _html(item.title)
        category = _html(item.category)
        estimated_price = _html(_fmt_price(item.estimated_price))
        caution_amount = _html(_fmt_price(item.caution_amount))
        documents = _html(_yes_no(item.has_documents))
        location = _html(item.location)
        due_date = _html(item.due_date)
        consultation_url = _html(item.consultation_url)
        reference = _html(item.reference or "Sans reference")
        rows.append(
            f"<tr class=\"{row_class}\">"
            f"<td data-label=\"#\">{index}</td>"
            f"<td data-label=\"OBJET\"><strong>{title}</strong><span>{reference}</span></td>"
            f"<td data-label=\"TYPE\"><span class=\"type-pill\">{category}</span></td>"
            f"<td data-label=\"ESTIMATION\">{estimated_price}</td>"
            f"<td data-label=\"CAUTION\">{caution_amount}</td>"
            f"<td data-label=\"DOCUMENTS\"><span class=\"doc-pill\">{documents}</span></td>"
            f"<td data-label=\"LIEU\">{location}</td>"
            f"<td data-label=\"DATE LIMITE\">{due_date}</td>"
            f"<td data-label=\"LIEN\"><a href=\"{consultation_url}\">Ouvrir</a></td>"
            "</tr>"
        )
        cards.append(
            f"<article class=\"consultation-card {row_class}\">"
            f"<div class=\"card-top\">"
            f"<span class=\"card-index\">#{index}</span>"
            f"<span class=\"card-badge\">{'Type fournitures' if row_class else 'AOS'}</span>"
            "</div>"
            f"<h2>{title}</h2>"
            f"<p class=\"card-ref\">Ref: {reference}</p>"
            f"<div class=\"card-type\">{category}</div>"
            f"<div class=\"card-grid\">"
            f"<div><span>Estimation</span><strong>{estimated_price}</strong></div>"
            f"<div><span>Caution</span><strong>{caution_amount}</strong></div>"
            f"<div><span>Documents</span><strong>{documents}</strong></div>"
            f"<div><span>Lieu</span><strong>{location}</strong></div>"
            f"<div class=\"card-wide\"><span>Date limite</span><strong>{due_date}</strong></div>"
            "</div>"
            f"<a class=\"card-link\" href=\"{consultation_url}\">Voir la consultation</a>"
            "</article>"
        )

    table_rows = "\n".join(rows) or (
        "<tr><td colspan=\"9\" class=\"empty-table\">Aucune consultation publiee pour cette date.</td></tr>"
    )
    card_markup = "\n".join(cards) or (
        "<section class=\"empty-state\">"
        "<div class=\"empty-icon\">0</div>"
        "<h2>Aucune consultation publiee</h2>"
        "<p>La journee selectionnee ne contient aucun appel d'offres ouvert simplifie a afficher.</p>"
        "</section>"
    )
    return f"""<!doctype html>
<html lang="fr">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Resume AOS {date_label}</title>
  <style>
    :root {{
      color-scheme: light;
      --bg: #f3efe6;
      --bg-strong: #e5dcc8;
      --paper: rgba(255, 251, 244, 0.94);
      --paper-strong: #fffdfa;
      --ink: #182126;
      --muted: #64707a;
      --line: rgba(24, 33, 38, 0.11);
      --accent: #0f766e;
      --accent-2: #d97706;
      --accent-soft: rgba(15, 118, 110, 0.11);
      --warm-soft: rgba(217, 119, 6, 0.12);
      --success-soft: rgba(22, 163, 74, 0.12);
      --shadow: 0 24px 60px rgba(51, 39, 16, 0.12);
      --fournitures: rgba(255, 233, 233, 0.9);
      --fournitures-border: #c2410c;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      padding: 24px;
      background:
        radial-gradient(circle at top left, rgba(15, 118, 110, 0.16), transparent 28%),
        radial-gradient(circle at top right, rgba(217, 119, 6, 0.16), transparent 24%),
        linear-gradient(180deg, #f8f4ec 0%, var(--bg) 52%, var(--bg-strong) 100%);
      color: var(--ink);
      font: 15px/1.6 "Segoe UI", Arial, sans-serif;
    }}
    a {{
      color: inherit;
    }}
    .page {{
      max-width: 1320px;
      margin: 0 auto;
    }}
    .topbar {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      margin-bottom: 18px;
      padding: 0 4px;
    }}
    .brand {{
      display: inline-flex;
      align-items: center;
      gap: 10px;
      color: var(--muted);
      font-size: 13px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }}
    .brand-mark {{
      width: 38px;
      height: 38px;
      display: inline-grid;
      place-items: center;
      border-radius: 12px;
      background: linear-gradient(135deg, var(--accent), #155e75);
      color: #fff;
      font-weight: 700;
      box-shadow: 0 12px 24px rgba(15, 118, 110, 0.28);
    }}
    .brand-name strong {{
      display: block;
      color: var(--ink);
      font-size: 14px;
      letter-spacing: 0.04em;
    }}
    .status-pill {{
      padding: 10px 14px;
      border: 1px solid rgba(15, 118, 110, 0.18);
      border-radius: 999px;
      background: rgba(255, 255, 255, 0.72);
      color: var(--accent);
      font-size: 12px;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }}
    .sheet {{
      background: var(--paper);
      border: 1px solid rgba(255, 255, 255, 0.5);
      border-radius: 28px;
      overflow: hidden;
      box-shadow: var(--shadow);
      backdrop-filter: blur(12px);
    }}
    header {{
      padding: 36px 36px 24px;
      background:
        radial-gradient(circle at 85% 10%, rgba(217, 119, 6, 0.2), transparent 20%),
        linear-gradient(135deg, rgba(15, 118, 110, 0.14), rgba(255, 255, 255, 0.78));
      border-bottom: 1px solid var(--line);
    }}
    .hero {{
      display: grid;
      grid-template-columns: minmax(0, 1.3fr) minmax(260px, 0.7fr);
      gap: 24px;
      align-items: end;
    }}
    .eyebrow {{
      display: inline-flex;
      align-items: center;
      gap: 8px;
      margin-bottom: 14px;
      padding: 8px 12px;
      border-radius: 999px;
      background: rgba(255, 255, 255, 0.78);
      border: 1px solid rgba(15, 118, 110, 0.16);
      color: var(--accent);
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }}
    h1 {{
      margin: 0;
      font-size: 42px;
      line-height: 1.05;
      letter-spacing: -0.03em;
      max-width: 14ch;
    }}
    .lead {{
      max-width: 58ch;
      margin: 14px 0 0;
      color: var(--muted);
      font-size: 16px;
    }}
    .hero-side {{
      display: grid;
      gap: 12px;
    }}
    .spotlight {{
      padding: 18px 18px 16px;
      border-radius: 22px;
      background: rgba(255, 255, 255, 0.76);
      border: 1px solid rgba(24, 33, 38, 0.07);
    }}
    .spotlight span {{
      display: block;
      color: var(--muted);
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }}
    .spotlight strong {{
      display: block;
      margin-top: 8px;
      font-size: 24px;
      line-height: 1.15;
    }}
    .hero-note {{
      color: var(--muted);
      font-size: 13px;
    }}
    .summary-strip {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 14px;
      padding: 20px 36px 0;
    }}
    .metric {{
      padding: 18px 18px 16px;
      border-radius: 20px;
      background: var(--paper-strong);
      border: 1px solid var(--line);
      box-shadow: 0 10px 24px rgba(31, 41, 55, 0.04);
    }}
    .metric span {{
      display: block;
      color: var(--muted);
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }}
    .metric strong {{
      display: block;
      margin-top: 8px;
      font-size: 26px;
      line-height: 1.15;
    }}
    .metric em {{
      display: block;
      margin-top: 6px;
      color: var(--muted);
      font-style: normal;
      font-size: 13px;
    }}
    .content {{
      padding: 26px 36px 36px;
    }}
    .section-head {{
      display: flex;
      align-items: end;
      justify-content: space-between;
      gap: 16px;
      margin-bottom: 18px;
    }}
    .section-head h2 {{
      margin: 0;
      font-size: 22px;
      letter-spacing: -0.02em;
    }}
    .section-head p {{
      margin: 0;
      color: var(--muted);
      font-size: 14px;
    }}
    .table-wrap {{
      overflow: auto;
      border: 1px solid var(--line);
      border-radius: 24px;
      background: rgba(255, 255, 255, 0.82);
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.65);
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
    }}
    th, td {{
      padding: 16px 18px;
      border-bottom: 1px solid var(--line);
      vertical-align: top;
      text-align: left;
    }}
    th {{
      position: sticky;
      top: 0;
      background: rgba(248, 244, 236, 0.94);
      z-index: 1;
      font-size: 11px;
      letter-spacing: 0.08em;
      text-transform: uppercase;
      color: var(--muted);
    }}
    td strong {{
      display: block;
      font-size: 15px;
      line-height: 1.45;
    }}
    td span {{
      display: block;
      margin-top: 6px;
      color: var(--muted);
      font-size: 12px;
    }}
    tbody tr {{
      background: rgba(255, 255, 255, 0.78);
    }}
    tbody tr:hover {{
      background: rgba(255, 255, 255, 0.95);
    }}
    tbody tr.row-fournitures {{
      box-shadow: inset 6px 0 0 var(--fournitures-border);
    }}
    tbody tr.row-fournitures td {{
      background: var(--fournitures);
    }}
    td:nth-child(1), td:nth-child(4), td:nth-child(5), td:nth-child(7) {{
      white-space: nowrap;
    }}
    td a, .card-link {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 42px;
      padding: 0 16px;
      border-radius: 999px;
      background: var(--ink);
      color: #fff;
      text-decoration: none;
      font-weight: 700;
    }}
    .type-pill, .doc-pill, .card-badge, .card-type {{
      display: inline-flex;
      align-items: center;
      width: fit-content;
      padding: 8px 12px;
      border-radius: 999px;
      background: var(--accent-soft);
      color: var(--accent);
      font-size: 12px;
      font-weight: 700;
      line-height: 1;
    }}
    .doc-pill {{
      background: var(--success-soft);
      color: #166534;
    }}
    .cards {{
      display: none;
      gap: 14px;
    }}
    .consultation-card {{
      padding: 18px;
      border-radius: 22px;
      background: rgba(255, 255, 255, 0.86);
      border: 1px solid var(--line);
      box-shadow: 0 14px 28px rgba(31, 41, 55, 0.06);
    }}
    .consultation-card.row-fournitures {{
      background: var(--fournitures);
      border-color: rgba(194, 65, 12, 0.24);
      box-shadow: inset 5px 0 0 var(--fournitures-border);
    }}
    .card-top {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      margin-bottom: 10px;
    }}
    .card-index {{
      color: var(--muted);
      font-size: 13px;
      font-weight: 700;
    }}
    .card-badge {{
      background: var(--warm-soft);
      color: #b45309;
    }}
    .consultation-card h2 {{
      margin: 0;
      font-size: 20px;
      line-height: 1.25;
      letter-spacing: -0.02em;
    }}
    .card-ref {{
      margin: 8px 0 0;
      color: var(--muted);
      font-size: 13px;
    }}
    .card-type {{
      margin-top: 14px;
    }}
    .card-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
      margin-top: 16px;
    }}
    .card-grid div {{
      padding: 12px;
      border-radius: 16px;
      background: rgba(248, 244, 236, 0.9);
      border: 1px solid rgba(24, 33, 38, 0.06);
    }}
    .card-grid span {{
      display: block;
      color: var(--muted);
      font-size: 11px;
      font-weight: 700;
      letter-spacing: 0.06em;
      text-transform: uppercase;
    }}
    .card-grid strong {{
      display: block;
      margin-top: 6px;
      font-size: 15px;
      line-height: 1.35;
    }}
    .card-wide {{
      grid-column: 1 / -1;
    }}
    .card-link {{
      width: 100%;
      margin-top: 16px;
    }}
    .empty-state {{
      padding: 34px 20px;
      text-align: center;
      border: 1px dashed rgba(24, 33, 38, 0.16);
      border-radius: 24px;
      background: rgba(255, 255, 255, 0.7);
    }}
    .empty-icon {{
      width: 70px;
      height: 70px;
      margin: 0 auto 16px;
      display: grid;
      place-items: center;
      border-radius: 20px;
      background: linear-gradient(135deg, var(--accent-soft), rgba(217, 119, 6, 0.16));
      color: var(--accent);
      font-size: 28px;
      font-weight: 800;
    }}
    .empty-state h2 {{
      margin: 0;
      font-size: 24px;
    }}
    .empty-state p, .empty-table {{
      color: var(--muted);
    }}
    footer {{
      padding: 0 36px 30px;
      color: var(--muted);
      font-size: 13px;
    }}
    @media (max-width: 980px) {{
      .hero,
      .summary-strip {{
        grid-template-columns: 1fr;
      }}
      h1 {{
        font-size: 34px;
      }}
    }}
    @media (max-width: 720px) {{
      body {{
        padding: 14px;
      }}
      .topbar {{
        align-items: flex-start;
        flex-direction: column;
      }}
      header {{
        padding: 24px 18px 18px;
      }}
      .summary-strip,
      .content,
      footer {{
        padding-left: 18px;
        padding-right: 18px;
      }}
      h1 {{
        font-size: 28px;
      }}
      .lead {{
        font-size: 15px;
      }}
      .metric strong,
      .spotlight strong {{
        font-size: 22px;
      }}
      .table-wrap {{
        display: none;
      }}
      .cards {{
        display: grid;
      }}
      .section-head {{
        align-items: flex-start;
        flex-direction: column;
      }}
      .consultation-card {{
        padding: 16px;
      }}
      .consultation-card h2 {{
        font-size: 18px;
      }}
      .card-grid {{
        grid-template-columns: 1fr;
      }}
      .card-wide {{
        grid-column: auto;
      }}
    }}
  </style>
</head>
<body>
  <div class="page">
    <div class="topbar">
      <div class="brand">
        <span class="brand-mark">PB</span>
        <span class="brand-name"><strong>PMMP Daily Summary Bot</strong>Rapport HTML premium</span>
      </div>
      <div class="status-pill">Rapport du {html.escape(date_label)}</div>
    </div>
    <div class="sheet">
      <header>
        <div class="hero">
          <div>
            <div class="eyebrow">Appels d'offres ouverts simplifies</div>
            <h1>Lecture rapide, claire et mobile pour vos consultations.</h1>
            <p class="lead">Ce document genere par <strong>PMMP Daily Summary Bot</strong> met les consultations du jour en avant avec une hierarchie visuelle plus nette, pour faciliter le tri, la lecture et l'action.</p>
          </div>
          <div class="hero-side">
            <div class="spotlight">
              <span>Date filtree</span>
              <strong>{html.escape(date_label)}</strong>
            </div>
            <div class="hero-note">Ouverture confortable sur telephone, partage simple en HTML et lecture plus motivante pour les utilisateurs finaux.</div>
          </div>
        </div>
      </header>
      <section class="summary-strip">
        <div class="metric">
          <span>Total consultations</span>
          <strong>{len(items)}</strong>
          <em>Resume quotidien disponible en un coup d'oeil</em>
        </div>
        <div class="metric">
          <span>Format</span>
          <strong>HTML premium</strong>
          <em>Structure optimisee pour bureau et smartphone</em>
        </div>
        <div class="metric">
          <span>Assistant</span>
          <strong>PMMP Daily Summary Bot</strong>
          <em>Rapport prepare pour une lecture plus fluide</em>
        </div>
      </section>
      <section class="content">
        <div class="section-head">
          <div>
            <h2>Consultations publiees</h2>
            <p>Les cartes s'affichent automatiquement sur mobile. Le tableau reste disponible sur ecran large pour comparer rapidement plusieurs lignes.</p>
          </div>
        </div>
        <div class="cards">
          {card_markup}
        </div>
        <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>#</th>
                <th>Objet</th>
                <th>Type</th>
                <th>Estimation</th>
                <th>Caution</th>
                <th>Documents</th>
                <th>Lieu</th>
                <th>Date limite</th>
                <th>Lien</th>
              </tr>
            </thead>
            <tbody>
              {table_rows}
            </tbody>
          </table>
        </div>
      </section>
      <footer>
        Document genere par <strong>PMMP Daily Summary Bot</strong>. Les donnees et liens restent inchanges; seule la presentation HTML a ete amelioree pour la lisibilite.
      </footer>
    </div>
  </div>
</body>
</html>
"""


def _parse_listing_items(html: str, published_date: str) -> list[ProcurementSummaryItem]:
    soup = BeautifulSoup(html, "lxml")
    table = soup.find("table", class_="table-results")
    if not table:
        return []

    items = []
    for row in table.find_all("tr")[2:]:
        item = _parse_listing_row(row, published_date)
        if item:
            items.append(item)
    return items


def _parse_listing_row(row, published_date: str) -> Optional[ProcurementSummaryItem]:
    cells = row.find_all("td")
    if len(cells) < 6:
        return None

    meta_text = _clean(cells[1].get_text(" ", strip=True))
    procedure = meta_text.split(" ... ", 1)[0].strip()
    meta_parts = [part.strip() for part in meta_text.split(" ... ") if part.strip()]
    category = meta_parts[1] if len(meta_parts) > 1 else "—"
    dates = re.findall(r"\d{2}/\d{2}/\d{4}", meta_text)
    row_published_date = dates[-1] if dates else ""
    if row_published_date != published_date:
        return None
    if procedure and procedure != "AOS":
        return None

    consultation_url = _detail_url(row)
    if not consultation_url:
        return None

    detail_text = _clean(cells[2].get_text(" ", strip=True))
    reference = _reference_from_url(consultation_url) or _reference_from_text(detail_text)
    title = _title_from_text(detail_text)
    location = _location_from_text(cells[3].get_text(" ", strip=True))
    due_date = _due_date_from_text(cells[4].get_text(" ", strip=True))

    return ProcurementSummaryItem(
        reference=reference,
        title=title,
        category=category,
        estimated_price=None,
        caution_amount=None,
        has_documents=False,
        location=location,
        due_date=due_date,
        published_date=row_published_date,
        consultation_url=consultation_url,
    )

def _detail_url(row) -> str:
    for link in row.find_all("a", href=True):
        href = link["href"]
        if "EntrepriseDetailConsultation" in href:
            return urljoin(BASE_URL, href)
    return ""


def _reference_from_url(url: str) -> str:
    query = parse_qs(urlparse(url).query)
    return (query.get("refConsultation") or [""])[0]


def _reference_from_text(text: str) -> str:
    return text.split(" - ", 1)[0].strip()


def _title_from_text(text: str) -> str:
    match = re.search(r"Objet\s*:\s*(.*)", text, re.I)
    if not match:
        return text.strip()
    return match.group(1).strip()


def _location_from_text(text: str) -> str:
    value = _clean(text).strip("- ")
    if " ... " in value:
        value = value.split(" ... ")[-1].strip("- ")
    return value or "—"


def _due_date_from_text(text: str) -> str:
    match = re.search(r"\d{2}/\d{2}/\d{4}(?:\s+\d{2}:\d{2})?", text)
    return match.group(0) if match else "—"


def _with_detail_data(
    items: list[ProcurementSummaryItem],
) -> list[ProcurementSummaryItem]:
    if not items:
        return []

    enriched: list[Optional[ProcurementSummaryItem]] = [None for _ in items]
    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {
            executor.submit(_fetch_detail_data, item): index
            for index, item in enumerate(items)
        }
        for future in as_completed(futures):
            index = futures[future]
            try:
                enriched[index] = future.result()
            except Exception:
                enriched[index] = None
    return [item for item in enriched if item is not None]


def _fetch_detail_data(item: ProcurementSummaryItem) -> Optional[ProcurementSummaryItem]:
    response = http.get(item.consultation_url, headers=HEADERS, timeout=25)
    response.raise_for_status()
    if not _is_simplified_open_tender_detail(response.text):
        return None
    return replace(
        item,
        estimated_price=_extract_estimated_price(response.text),
        caution_amount=_extract_caution_amount(response.text),
        has_documents=_extract_has_documents(response.text),
    )


def _is_simplified_open_tender_detail(html: str) -> bool:
    text = _clean(BeautifulSoup(html, "lxml").get_text(" ", strip=True)).lower()
    return "procédure : appel d'offres ouvert simplifié" in text or (
        "procedure : appel d'offres ouvert simplifie" in _norm(text)
    )


def _extract_estimated_price(html: str) -> Optional[float]:
    text = _clean(BeautifulSoup(html, "lxml").get_text(" ", strip=True))
    match = re.search(
        r"Estimation\s*\([^)]*\)\s*\*?\s*:\s*([0-9][0-9\s.,]*)",
        text,
        re.I,
    )
    if not match:
        return None
    return _parse_price_fr(match.group(1))


def _extract_caution_amount(html: str) -> Optional[float]:
    text = _clean(BeautifulSoup(html, "lxml").get_text(" ", strip=True))
    match = re.search(
        r"Caution\s+provisoire\s*:\s*([0-9][0-9\s.,]*)",
        text,
        re.I,
    )
    if not match:
        return None
    return _parse_price_fr(match.group(1))


def _extract_has_documents(html: str) -> bool:
    text = _clean(BeautifulSoup(html, "lxml").get_text(" ", strip=True))
    match = re.search(
        r"Prospectus,\s*notices\s+ou\s+autres\s+documents\s*:\s*(.+?)\s+(?:Réunion|Visites des lieux|Variante)\s*:",
        text,
        re.I,
    )
    if not match:
        return False
    value = match.group(1).strip()
    return value not in {"-", "—", ""}


def _fmt_price(value: Optional[float]) -> str:
    return "—" if value is None else f"{value:,.2f} Dhs TTC"


def _yes_no(value: bool) -> str:
    return "Oui" if value else "Non"


def _clean(value: str) -> str:
    return " ".join(value.replace("\xa0", " ").split())


def _norm(value: str) -> str:
    return (
        value.replace("é", "e")
        .replace("è", "e")
        .replace("ê", "e")
        .replace("à", "a")
        .replace("â", "a")
    )


def _shorten(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."


def _esc(value) -> str:
    return str(value).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _html(value) -> str:
    return html.escape(str(value), quote=True)
