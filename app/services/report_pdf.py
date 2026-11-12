"""
Mise en page PDF du rapport mensuel d'une parcelle (report_service), avec
ReportLab. Ce module ne calcule rien : il présente le rapport.

Les polices standard du PDF ne contiennent pas certains caractères (indice
« ₀ » de ET₀, émojis) : ils sont remplacés par leur équivalent lisible.
"""

from __future__ import annotations

import io
from datetime import date

from reportlab.lib import colors
from reportlab.lib.enums import TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import KeepTogether, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from xml.sax.saxutils import escape

# Police Vera, fournie avec ReportLab : plus complète que les polices
# standard du PDF (elle affiche par exemple le « ² » de MJ/m²)
pdfmetrics.registerFont(TTFont("Vera", "Vera.ttf"))
pdfmetrics.registerFont(TTFont("Vera-Bold", "VeraBd.ttf"))
FONT, FONT_BOLD = "Vera", "Vera-Bold"

#: Couleurs d'AWAM
GREEN_DARK = colors.HexColor("#1b4332")
GREEN_TEXT = colors.HexColor("#27500A")
GREEN_LIGHT = colors.HexColor("#EAF3DE")
GREEN_LINE = colors.HexColor("#C0DD97")
GREY = colors.HexColor("#6b7280")
LINE = colors.HexColor("#e5e7eb")

MONTHS = ["janvier", "février", "mars", "avril", "mai", "juin", "juillet", "août",
          "septembre", "octobre", "novembre", "décembre"]

#: Paramètres du climat : libellés, unités et décimales de l'onglet Paramètres
#: (recopiés de src/components/Conditions/parameterInfo.ts)
PARAMETERS = [
    ("temperature_max_c", "Température max.", "°C", 1),
    ("temperature_min_c", "Température min.", "°C", 1),
    ("temperature_mean_c", "Température moy.", "°C", 1),
    ("precipitation_mm", "Pluie", "mm", 0),
    ("humidity_mean_pct", "Humidité de l'air", "%", 0),
    ("wind_speed_mean_kmh", "Vent moyen", "km/h", 0),
    ("radiation_mj_m2", "Rayonnement", "MJ/m²", 1),
    ("et0_mm", "ET0", "mm", 0),
    ("dry_days", "Jours sans pluie", "jours", 0),
]

INDICES = [("NDVI", "Vigueur végétative"), ("NDMI", "Teneur en eau foliaire"), ("NDRE", "Activité chlorophyllienne")]

_REPLACE = {"₀": "0", "≥": ">=", "≤": "<=", "↗": "hausse", "↘": "baisse", "\u202f": " "}


def _clean(text) -> str:
    """Texte sûr pour le PDF : caractères remplacés, émojis retirés, échappé."""
    s = "" if text is None else str(text)
    for a, b in _REPLACE.items():
        s = s.replace(a, b)
    s = "".join(ch for ch in s if ord(ch) < 0x2190 or ch in "€")
    return escape(s)


def _num(v, decimals: int = 1) -> str:
    if v is None:
        return "—"
    s = f"{v:,.{decimals}f}".replace(",", " ").replace(".", ",")
    return s


def _day(d) -> str:
    if d is None:
        return "—"
    if isinstance(d, str):
        d = date.fromisoformat(d[:10])
    return d.strftime("%d/%m/%Y")


def _signed(v, decimals: int, unit: str | None) -> str:
    if v is None:
        return "—"
    sign = "+" if v > 0 else "−" if v < 0 else ""
    return f"{sign}{_num(abs(v), decimals)}{' ' + unit if unit else ''}"


class _Styles:
    def __init__(self):
        base = getSampleStyleSheet()
        self.title = ParagraphStyle("t", parent=base["Title"], fontName=FONT_BOLD, fontSize=15,
                                    textColor=GREEN_DARK, alignment=0, spaceAfter=2)
        self.sub = ParagraphStyle("s", parent=base["Normal"], fontName=FONT, fontSize=9, textColor=GREY, leading=12)
        self.right = ParagraphStyle("r", parent=self.sub, alignment=TA_RIGHT)
        self.section = ParagraphStyle("h", parent=base["Normal"], fontName=FONT_BOLD, fontSize=9.5,
                                      textColor=GREEN_TEXT, spaceBefore=10, spaceAfter=4)
        self.cell = ParagraphStyle("c", parent=base["Normal"], fontName=FONT, fontSize=8, leading=10.5)
        self.head = ParagraphStyle("hd", parent=self.cell, fontName=FONT_BOLD, textColor=GREEN_TEXT)
        self.note = ParagraphStyle("n", parent=base["Normal"], fontName=FONT, fontSize=7.8, textColor=GREY, leading=10.5)


def _table(rows: list[list], widths: list[float], st: _Styles) -> Table:
    data = [[Paragraph(_clean(c) if not isinstance(c, Paragraph) else c.text, st.head if i == 0 else st.cell)
             if not isinstance(c, Paragraph) else c for c in row] for i, row in enumerate(rows)]
    t = Table(data, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), GREEN_LIGHT),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, GREEN_LINE),
        ("LINEBELOW", (0, 1), (-1, -1), 0.3, LINE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return t


def _section(title: str, body: list, st: _Styles) -> KeepTogether:
    return KeepTogether([Paragraph(_clean(title), st.section), *body])


def build_pdf(report: dict) -> bytes:
    """Le PDF du rapport, en octets."""
    st = _Styles()
    buf = io.BytesIO()
    year, month = (int(x) for x in report["month"].split("-"))
    month_label = f"{MONTHS[month - 1]} {year}"
    doc = SimpleDocTemplate(
        buf, pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm, topMargin=14 * mm, bottomMargin=14 * mm,
        title=f"Rapport mensuel {report['parcel_name']} {month_label}", author="AWAM",
    )
    width = A4[0] - 32 * mm
    story: list = []

    # ---- En-tête ----------------------------------------------------
    crops = ", ".join(c["label"] for c in report["crops"]) or "aucune culture en cours"
    kind = "Polyculture" if report["polyculture"] else "Culture"
    head = Table(
        [[
            [Paragraph(_clean(f"Rapport mensuel — {report['parcel_name']}"), st.title),
             Paragraph(_clean(f"{report['farm_name']} · {month_label} · {kind} : {crops}"), st.sub)],
            Paragraph(f"AWAM<br/>généré le {_day(report['generated_on'])}", st.right),
        ]],
        colWidths=[width * 0.75, width * 0.25],
    )
    head.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, 0), 1.2, GREEN_DARK), ("VALIGN", (0, 0), (-1, -1), "TOP")]))
    story += [head, Spacer(1, 4)]
    if report["climate"]["partial"] and report.get("weather_until"):
        story.append(Paragraph(
            _clean(f"Mois en cours : les calculs s'arrêtent au {_day(report['weather_until'])}, dernier jour de météo enregistré."),
            st.note))

    # ---- 1. Climat ----------------------------------------------------
    climate = report["climate"]
    n0, n1 = climate["normals_period"]
    rows = [["Paramètre", "Ce mois", f"Normale {n0}-{n1}", "Écart"]]
    for key, label, unit, dec in PARAMETERS:
        p = climate["parameters"].get(key, {})
        rows.append([
            label,
            f"{_num(p.get('value'), dec)} {unit}" if p.get("value") is not None else "—",
            f"{_num(p.get('normal'), dec)} {unit}" if p.get("normal") is not None else "—",
            _signed(p.get("anomaly"), dec, p.get("anomaly_unit") or unit),
        ])
    body = [_table(rows, [width * 0.34, width * 0.22, width * 0.24, width * 0.20], st)]
    body.append(Paragraph(_clean(f"{climate['days']} jour(s) de météo sur {climate['days_in_month']}."), st.note))
    story.append(_section("1 · Climat du mois", body, st))

    # ---- 2. Stades ----------------------------------------------------
    impacts = report["impacts"]
    rows = [["Culture", "Stade", "Période habituelle", "Climat du stade"]]
    for c in impacts["crops"]:
        for s in c["stages"]:
            rows.append([c["label"], s["label"], s.get("months") or f"{_day(s['start'])} au {_day(s['end'])}", s.get("summary") or "—"])
    body = [_table(rows, [width * 0.15, width * 0.16, width * 0.23, width * 0.46], st)] if len(rows) > 1 \
        else [Paragraph("Aucune culture en cours sur cette parcelle.", st.note)]
    body.append(Paragraph("Stades : périodes habituelles du calendrier AWAM.", st.note))
    story.append(_section("2 · Stades des cultures", body, st))

    # ---- 3. Eau -------------------------------------------------------
    rows = [["Culture", "Besoin", "Pluie mesurée", "Arrosages", "Besoin couvert", "État"]]
    weeks: list[str] = []
    for c in impacts["crops"]:
        w = (c["water"] or {}).get("month")
        if not w:
            rows.append([c["label"], "—", "—", "—", "—", "Pas encore de météo ce mois-ci"])
            continue
        if w["problems"]:
            rows.append([c["label"], "—", f"{_num(w['rain_mm'], 1)} mm", "—", "—",
                         "Non calculable : " + " et ".join(w["problems"])])
            continue
        rows.append([
            c["label"], f"{_num(w['need_mm'], 1)} mm", f"{_num(w['rain_mm'], 1)} mm",
            f"{_num(w['irrigation_mm'], 1)} mm ({_num(w['irrigation_l'], 0)} L)",
            f"{_num(w['coverage_pct'], 0)} %",
            "Besoin couvert" if w["covered"] else f"Besoin non couvert : il manque {_num(w['missing_mm'], 1)} mm",
        ])
        for wk in c["water"].get("weeks", []):
            weeks.append(f"{c['label']} : du {_day(wk['start'])} au {_day(wk['end'])}, "
                         f"{_num(wk['received_mm'], 1)} mm reçus pour {_num(wk['need_mm'], 1)} mm de besoin")
    body = [_table(rows, [width * 0.16, width * 0.12, width * 0.14, width * 0.18, width * 0.13, width * 0.27], st)]
    if weeks:
        body.append(Paragraph("Semaines où le besoin n'a pas été couvert :", st.note))
        body += [Paragraph(_clean("· " + w), st.note) for w in weeks]
    body.append(Paragraph("Besoin = ET0 × Kc du stade de chaque jour (FAO-56, référentiel AWAM). "
                          "Pluie mesurée comptée en entier ; arrosages répartis sur toute la parcelle.", st.note))
    story.append(_section("3 · Eau", body, st))

    # ---- 4. Végétation --------------------------------------------------
    veg = report["vegetation"]
    body = []
    if not veg["scenes"]:
        msg = "Aucune acquisition Sentinel-2 exploitable ce mois-ci"
        if veg["excluded_scenes"]:
            msg += f" ({veg['excluded_scenes']} écartée(s) pour couverture nuageuse)"
        body.append(Paragraph(_clean(msg + "."), st.note))
    else:
        rows = [["Acquisition", "NDVI", "NDMI", "NDRE", "Nuages"]]
        for s in veg["scenes"]:
            v = s["values"]
            rows.append([_day(s["date"]), _num(v.get("NDVI"), 2), _num(v.get("NDMI"), 2), _num(v.get("NDRE"), 2),
                         f"{_num(s['cloud_cover'], 0)} %" if s["cloud_cover"] is not None else "—"])
        body.append(_table(rows, [width * 0.24, width * 0.19, width * 0.19, width * 0.19, width * 0.19], st))
        last = veg["scenes"][-1]
        body.append(Spacer(1, 4))
        body.append(Paragraph(_clean(f"Interprétation de l'acquisition du {_day(last['date'])} :"), st.note))
        rows = [["Indicateur", "Valeur", "Référence parcelle", "Référentiel AWAM"]]
        poly = " (polyculture : indices mélangés)" if veg["multi_crop"] else ""
        crop_names = {c["crop_id"]: c["label"] for c in veg["crops"]}
        for key, label in INDICES:
            if last["values"].get(key) is None:
                continue
            b = last["baselines"].get(key) or {}
            if b.get("status") == "ok":
                where = {"below": "en dessous", "within": "dans la plage", "above": "au-dessus"}.get(b.get("verdict"), "—")
                ref_parcel = f"{_num(b['low'], 2)} à {_num(b['high'], 2)} : {where}"
            else:
                ref_parcel = "historique insuffisant"
            refs = []
            for it in last["interpretation"]:
                r = next((x for x in it["readings"] if x["indice"] == key), None)
                if r and r["level"] is not None:
                    lvl = {"low": "sous l'intervalle", "normal": "dans l'intervalle", "high": "au-dessus"}[r["level"]]
                    refs.append(f"{crop_names.get(it['crop_id'], '')} : {_num(r['low'], 2)} à {_num(r['high'], 2)}, {lvl}")
            rows.append([f"{key} · {label}", _num(last["values"][key], 2), ref_parcel,
                         ("; ".join(refs) + poly) if refs else "—"])
        body.append(_table(rows, [width * 0.27, width * 0.10, width * 0.27, width * 0.36], st))
        body.append(Paragraph("Référence parcelle : valeurs de la même parcelle, à la même période, les années "
                              "précédentes. Référentiel AWAM : intervalle attendu pour chaque culture et son stade.", st.note))
    story.append(_section("4 · Végétation", body, st))

    # ---- 5. Alertes -----------------------------------------------------
    alerts = report["alerts"]
    if alerts:
        rows = [["Alerte", "Culture", "Ouverte", "Clôturée", "Raison"]]
        for a in alerts:
            label = "Besoin en eau non couvert" if a["type"] == "Besoin en eau" else f"{a['type']} · {a['severity_label']}"
            rows.append([label, a["crop_label"] or "—", _day(a["opened"]),
                         _day(a["closed"]) if a["closed"] else "En cours", a["resolution_label"] or "—"])
        body = [_table(rows, [width * 0.34, width * 0.18, width * 0.15, width * 0.15, width * 0.18], st)]
    else:
        body = [Paragraph("Aucune alerte pendant ce mois.", st.note)]
    story.append(_section("5 · Alertes du mois", body, st))

    # ---- 6. Arrosages ---------------------------------------------------
    waterings = report["waterings"]
    if waterings:
        rows = [["Date", "Équipement", "Volume", "Culture", "Employés"]]
        for w in waterings:
            rows.append([_day(w["date"]), w["equipment"] or "—",
                         f"{_num(w['volume_l'], 0)} L" if w["volume_l"] is not None else "—",
                         w["crop_label"], ", ".join(w["employees"]) or "—"])
        body = [_table(rows, [width * 0.14, width * 0.22, width * 0.14, width * 0.2, width * 0.3], st)]
        total = sum(w["volume_l"] or 0 for w in waterings)
        body.append(Paragraph(_clean(f"Total : {len(waterings)} arrosage(s), {_num(total, 0)} L."), st.note))
    else:
        body = [Paragraph("Aucun arrosage enregistré pendant ce mois.", st.note)]
    story.append(_section("6 · Arrosages réalisés", body, st))

    story += [Spacer(1, 8), Paragraph(
        "Sources : météo Open-Meteo · images Sentinel-2 · référentiel agronomique AWAM · "
        "arrosages et alertes enregistrés dans AWAM.", st.note)]
    doc.build(story)
    return buf.getvalue()