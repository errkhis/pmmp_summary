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
    total_count = len(items)
    with_documents = sum(1 for item in items if item.has_documents)
    no_estimation = sum(1 for item in items if item.estimated_price is None)
    category_options = sorted({item.category.strip() or "—" for item in items}, key=lambda value: _norm(value))
    rows = []
    cards = []
    for index, item in enumerate(items, start=1):
        title = _html(item.title)
        category = _html(item.category)
        estimated_price = _html(_fmt_price(item.estimated_price))
        caution_amount = _html(_fmt_price(item.caution_amount))
        documents = _html(_yes_no(item.has_documents))
        location = _html(item.location)
        due_date = _html(item.due_date)
        consultation_url = _html(item.consultation_url)
        reference = _html(item.reference or "Sans reference")
        search_text = _html(f"{item.title} {item.location}".lower())
        type_filter = _html((item.category or "—").strip() or "—")
        docs_filter = "yes" if item.has_documents else "no"
        rows.append(
            f"<tr data-search=\"{search_text}\" data-type=\"{type_filter}\" data-documents=\"{docs_filter}\">"
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
            f"<article class=\"consultation-card\" data-search=\"{search_text}\" data-type=\"{type_filter}\" data-documents=\"{docs_filter}\">"
            f"<div class=\"card-top\">"
            f"<span class=\"card-index\">Consultation {index}</span>"
            f"<span class=\"card-badge\">{category}</span>"
            "</div>"
            f"<h2>{title}</h2>"
            f"<p class=\"card-ref\">Ref: {reference}</p>"
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
        "<h2>Aucune consultation publiee</h2>"
        "<p>Aucun appel d'offres ouvert simplifie n'a ete publie pour cette date.</p>"
        "</section>"
    )
    type_option_markup = "\n".join(
        f'<option value="{_html(category)}">{_html(category)}</option>'
        for category in category_options
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
      --bg: #edf4ef;
      --panel: #fbfdfb;
      --panel-alt: #f1f7f2;
      --ink: #15241c;
      --muted: #5f7267;
      --line: #d4e1d8;
      --accent: #1f6a4d;
      --accent-soft: #dff0e6;
      --success-soft: #e6f4ec;
      --shadow: 0 22px 48px rgba(21, 36, 28, 0.10);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      padding: 24px;
      background:
        radial-gradient(circle at top left, rgba(31, 106, 77, 0.10), transparent 30%),
        linear-gradient(180deg, #f8fcf8 0%, var(--bg) 100%);
      color: var(--ink);
      font: 15px/1.6 Arial, sans-serif;
    }}
    a {{
      color: var(--accent);
    }}
    .page {{
      max-width: 1200px;
      margin: 0 auto;
    }}
    .report-meta {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      margin-bottom: 16px;
      color: var(--muted);
      font-size: 13px;
    }}
    .report-tag {{
      display: inline-flex;
      align-items: center;
      padding: 8px 12px;
      border: 1px solid rgba(31, 106, 77, 0.22);
      border-radius: 999px;
      background: var(--panel);
      color: var(--accent);
      font-size: 12px;
      font-weight: 700;
      letter-spacing: 0.08em;
      text-transform: uppercase;
    }}
    .sheet {{
      overflow: hidden;
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 20px;
      box-shadow: var(--shadow);
    }}
    header {{
      padding: 32px;
      background:
        linear-gradient(135deg, rgba(223, 240, 230, 0.88), rgba(251, 253, 251, 0.96));
    }}
    .hero {{
      display: grid;
      grid-template-columns: 1fr;
      gap: 0;
      align-items: start;
    }}
    h1 {{
      margin: 0;
      font-size: 34px;
      line-height: 1.15;
      letter-spacing: -0.02em;
    }}
    .lead {{
      margin: 12px 0 0;
      color: var(--muted);
      font-size: 16px;
    }}
    .summary-strip {{
      display: grid;
      grid-template-columns: repeat(3, minmax(0, 1fr));
      gap: 18px;
      padding: 24px 32px 32px;
    }}
    .metric {{
      padding: 20px;
      border-radius: 16px;
      background: var(--panel-alt);
      border: 1px solid var(--line);
      box-shadow: inset 0 1px 0 rgba(255, 255, 255, 0.65);
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
      font-size: 24px;
      line-height: 1.15;
    }}
    .metric em {{
      display: block;
      margin-top: 8px;
      color: var(--muted);
      font-style: normal;
      font-size: 13px;
    }}
    .content {{
      margin: 0 32px 32px;
      padding: 28px 0 0;
    }}
    .controls {{
      display: grid;
      grid-template-columns: minmax(0, 1.4fr) repeat(3, minmax(180px, 0.6fr)) auto;
      gap: 12px;
      margin: 0 0 20px;
    }}
    .control-field {{
      width: 100%;
      min-height: 46px;
      padding: 0 14px;
      border: 1px solid var(--line);
      border-radius: 12px;
      background: var(--panel);
      color: var(--ink);
      font: inherit;
    }}
    .control-field:focus {{
      outline: 2px solid rgba(31, 106, 77, 0.18);
      border-color: var(--accent);
    }}
    .control-button {{
      min-height: 46px;
      padding: 0 18px;
      border: 1px solid var(--line);
      border-radius: 12px;
      background: var(--panel-alt);
      color: var(--ink);
      font: inherit;
      font-weight: 700;
      cursor: pointer;
    }}
    .control-button:hover {{
      background: #e5f0e8;
    }}
    .results-note {{
      margin: 0 0 24px;
      color: var(--muted);
      font-size: 14px;
    }}
    .section-head {{
      display: flex;
      align-items: end;
      justify-content: space-between;
      gap: 16px;
      margin-bottom: 18px;
      padding-top: 10px;
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
      border-radius: 16px;
      background: var(--panel);
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
      background: var(--panel-alt);
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
      background: var(--panel);
    }}
    tbody tr:hover {{
      background: #f4faf6;
    }}
    td:nth-child(1), td:nth-child(4), td:nth-child(5), td:nth-child(6), td:nth-child(8) {{
      white-space: nowrap;
    }}
    td a, .card-link {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-height: 42px;
      padding: 0 16px;
      border-radius: 999px;
      background: linear-gradient(135deg, #2b7a5a, #1b5a40);
      color: #fff;
      text-decoration: none;
      font-weight: 700;
      box-shadow: 0 10px 20px rgba(27, 90, 64, 0.18);
    }}
    td a:hover, .card-link:hover {{
      background: linear-gradient(135deg, #338665, #236649);
    }}
    .type-pill, .doc-pill, .card-badge {{
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
      gap: 18px;
      margin-top: 16px;
    }}
    .table-wrap {{
      margin-top: 16px;
    }}
    .consultation-card {{
      padding: 22px;
      border-radius: 16px;
      background: var(--panel);
      border: 1px solid var(--line);
      box-shadow: 0 12px 24px rgba(21, 36, 28, 0.05);
    }}
    .card-top {{
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 10px;
      margin-bottom: 14px;
    }}
    .card-index {{
      color: var(--muted);
      font-size: 13px;
      font-weight: 700;
    }}
    .card-badge {{
      background: var(--accent-soft);
      color: var(--accent);
    }}
    .consultation-card h2 {{
      margin: 0;
      font-size: 19px;
      line-height: 1.25;
      letter-spacing: -0.02em;
    }}
    .card-ref {{
      margin: 10px 0 0;
      color: var(--muted);
      font-size: 13px;
    }}
    .card-grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 14px;
      margin-top: 18px;
    }}
    .card-grid div {{
      padding: 14px;
      border-radius: 12px;
      background: var(--panel-alt);
      border: 1px solid var(--line);
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
      margin-top: 8px;
      font-size: 15px;
      line-height: 1.35;
    }}
    .card-wide {{
      grid-column: 1 / -1;
    }}
    .card-link {{
      width: 100%;
      margin-top: 20px;
    }}
    .empty-state {{
      padding: 40px 20px;
      text-align: center;
      border: 1px dashed var(--line);
      border-radius: 16px;
      background: var(--panel-alt);
    }}
    .empty-state h2 {{
      margin: 0;
      font-size: 24px;
    }}
    .empty-state p, .empty-table {{
      color: var(--muted);
    }}
    .hidden-item {{
      display: none !important;
    }}
    footer {{
      padding: 0 32px 28px;
      color: var(--muted);
      font-size: 13px;
      border-top: 1px solid var(--line);
      margin-top: 24px;
      padding-top: 18px;
    }}
    @media (max-width: 980px) {{
      .hero,
      .summary-strip {{
        grid-template-columns: 1fr;
      }}
      h1 {{
        font-size: 30px;
      }}
    }}
    @media (max-width: 720px) {{
      body {{
        padding: 12px;
      }}
      header {{
        padding: 22px 18px;
      }}
      .report-meta {{
        flex-direction: column;
        align-items: flex-start;
      }}
      .summary-strip,
      footer {{
        padding-left: 18px;
        padding-right: 18px;
      }}
      .summary-strip {{
        padding-top: 16px;
      }}
      .content {{
        margin: 0 18px 24px;
        padding-top: 0;
      }}
      .controls {{
        grid-template-columns: 1fr;
      }}
      h1 {{
        font-size: 26px;
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
        padding: 18px;
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
    <div class="report-meta">
      <div>PMMP Daily Summary Bot</div>
      <div class="report-tag">Rapport du {html.escape(date_label)}</div>
    </div>
    <div class="sheet">
      <header>
        <div class="hero">
          <div>
            <h1>Resume quotidien des appels d'offres ouverts simplifies</h1>
            <p class="lead">Synthese des consultations publiees le {html.escape(date_label)} avec les informations utiles pour verifier rapidement chaque dossier.</p>
          </div>
        </div>
      </header>
      <section class="summary-strip">
        <div class="metric">
          <span>Total consultations</span>
          <strong>{total_count}</strong>
          <em>Nombre d'avis retenus dans ce rapport</em>
        </div>
        <div class="metric">
          <span>Avec documents</span>
          <strong>{with_documents}</strong>
          <em>Dossiers avec documents detectes</em>
        </div>
        <div class="metric">
          <span>Sans estimation</span>
          <strong>{no_estimation}</strong>
          <em>Consultations sans montant estime</em>
        </div>
      </section>
      <section class="content">
        <div class="section-head">
          <div>
            <h2>Consultations publiees</h2>
            <p>Recherche par objet ou ville, puis tri par date limite sans modifier les donnees du rapport.</p>
          </div>
        </div>
        <div class="controls">
          <input id="searchInput" class="control-field" type="search" placeholder="Rechercher par objet ou ville" aria-label="Rechercher par objet ou ville">
          <select id="typeSelect" class="control-field" aria-label="Filtrer par type">
            <option value="">Tous les types</option>
            {type_option_markup}
          </select>
          <select id="documentsSelect" class="control-field" aria-label="Filtrer par disponibilite des documents">
            <option value="">Documents: tous</option>
            <option value="yes">Avec documents</option>
            <option value="no">Sans documents</option>
          </select>
          <select id="sortSelect" class="control-field" aria-label="Trier par date limite">
            <option value="asc">Date limite croissante</option>
            <option value="desc">Date limite decroissante</option>
          </select>
          <button id="resetFiltersButton" class="control-button" type="button">Reinitialiser</button>
        </div>
        <p id="resultsNote" class="results-note">Affichage de {total_count} consultation(s).</p>
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
        Document genere par <strong>PMMP Daily Summary Bot</strong>. Verifiez les details et les pieces sur le lien officiel avant toute decision.
      </footer>
    </div>
  </div>
  <script>
    (function () {{
      const searchInput = document.getElementById("searchInput");
      const typeSelect = document.getElementById("typeSelect");
      const documentsSelect = document.getElementById("documentsSelect");
      const sortSelect = document.getElementById("sortSelect");
      const resetFiltersButton = document.getElementById("resetFiltersButton");
      const resultsNote = document.getElementById("resultsNote");
      const tableBody = document.querySelector(".table-wrap tbody");
      const cardsContainer = document.querySelector(".cards");
      const emptyState = cardsContainer ? cardsContainer.querySelector(".empty-state") : null;

      if (!searchInput || !typeSelect || !documentsSelect || !sortSelect || !resetFiltersButton || !resultsNote || !tableBody || !cardsContainer) {{
        return;
      }}

      const tableRows = Array.from(tableBody.querySelectorAll("tr[data-search]"));
      const cards = Array.from(cardsContainer.querySelectorAll(".consultation-card[data-search]"));

      function matchesQuery(value, query) {{
        return value.indexOf(query) !== -1;
      }}

      function parseDueDate(text) {{
        const match = String(text || "").match(/(\\d{{2}})\\/(\\d{{2}})\\/(\\d{{4}})(?:\\s+(\\d{{2}}):(\\d{{2}}))?/);
        if (!match) {{
          return Number.POSITIVE_INFINITY;
        }}

        const day = Number(match[1]);
        const month = Number(match[2]) - 1;
        const year = Number(match[3]);
        const hour = Number(match[4] || "23");
        const minute = Number(match[5] || "59");
        return Date.UTC(year, month, day, hour, minute);
      }}

      function dueDateText(node) {{
        if (node.matches("tr")) {{
          const cell = node.querySelector('td[data-label="DATE LIMITE"]');
          return cell ? cell.textContent : "";
        }}

        const blocks = Array.from(node.querySelectorAll(".card-grid div"));
        for (const block of blocks) {{
          const label = block.querySelector("span");
          if (label && label.textContent.trim().toLowerCase() === "date limite") {{
            const value = block.querySelector("strong");
            return value ? value.textContent : "";
          }}
        }}
        return "";
      }}

      function syncVisibility(query, typeValue, documentsValue) {{
        let visibleCount = 0;

        tableRows.forEach((row) => {{
          const matchesType = !typeValue || (row.dataset.type || "") === typeValue;
          const matchesDocuments = !documentsValue || (row.dataset.documents || "") === documentsValue;
          const visible = matchesQuery(row.dataset.search || "", query) && matchesType && matchesDocuments;
          row.classList.toggle("hidden-item", !visible);
          if (visible) visibleCount += 1;
        }});

        cards.forEach((card) => {{
          const matchesType = !typeValue || (card.dataset.type || "") === typeValue;
          const matchesDocuments = !documentsValue || (card.dataset.documents || "") === documentsValue;
          const visible = matchesQuery(card.dataset.search || "", query) && matchesType && matchesDocuments;
          card.classList.toggle("hidden-item", !visible);
        }});

        if (emptyState) {{
          emptyState.classList.toggle("hidden-item", visibleCount !== 0);
        }}

        resultsNote.textContent = "Affichage de " + visibleCount + " consultation(s).";
      }}

      function sortNodes(order) {{
        const factor = order === "desc" ? -1 : 1;
        const compare = (left, right) => {{
          const a = parseDueDate(dueDateText(left));
          const b = parseDueDate(dueDateText(right));
          return a < b ? -1 * factor : a > b ? 1 * factor : 0;
        }};

        tableRows.sort(compare).forEach((row) => tableBody.appendChild(row));
        cards.sort(compare).forEach((card) => cardsContainer.appendChild(card));
        if (emptyState) {{
          cardsContainer.appendChild(emptyState);
        }}
      }}

      function refresh() {{
        const query = (searchInput.value || "").trim().toLowerCase();
        const typeValue = typeSelect.value;
        const documentsValue = documentsSelect.value;
        sortNodes(sortSelect.value);
        syncVisibility(query, typeValue, documentsValue);
      }}

      searchInput.addEventListener("input", refresh);
      typeSelect.addEventListener("change", refresh);
      documentsSelect.addEventListener("change", refresh);
      sortSelect.addEventListener("change", refresh);
      resetFiltersButton.addEventListener("click", () => {{
        searchInput.value = "";
        typeSelect.value = "";
        documentsSelect.value = "";
        sortSelect.value = "asc";
        refresh();
      }});
      refresh();
    }})();
  </script>
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
