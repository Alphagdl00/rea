from __future__ import annotations

import csv
import hashlib
import html as html_lib
import json
import re
import sqlite3
import time
import unicodedata
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import pandas as pd
import requests

USER_AGENT = "AstraFinanceRadar/3.1 (+local job-search tool)"
MX_TZ = ZoneInfo("America/Mexico_City")
FINANCE_TITLE_TERMS = (
    "finance", "financial", "fp&a", "fp & a", "financial planning", "controller",
    "controllership", "controlling", "treasury", "tesoreria", "tesorería",
    "contralor", "contraloría", "contraloria", "chief financial", "cfo",
    "accounting", "contabilidad", "finance business partner", "commercial finance",
    "plant finance", "operations finance", "supply chain finance", "corporate finance",
)
FINANCE_FUNCTION_TERMS = ("finance", "accounting", "audit", "treasury", "financial")
CITY_ALIASES = {
    "Guadalajara": ["guadalajara", "zapopan", "tlaquepaque", "tlajomulco", "el salto"],
    "Monterrey": ["monterrey", "san pedro garza garcia", "san pedro garza garcía", "apodaca", "santa catarina", "escobedo"],
    "CDMX": ["ciudad de mexico", "ciudad de méxico", "mexico city", "méxico city", "cdmx", "distrito federal"],
}
MEXICO_REGION_ALIASES = {
    "Aguascalientes": ["aguascalientes"],
    "Baja California": ["baja california", "tijuana", "mexicali", "ensenada"],
    "Baja California Sur": ["baja california sur", "la paz", "los cabos"],
    "Campeche": ["campeche"],
    "Chiapas": ["chiapas", "tuxtla gutierrez", "tuxtla gutiérrez"],
    "Chihuahua": ["chihuahua", "ciudad juarez", "ciudad juárez"],
    "Coahuila": ["coahuila", "saltillo", "torreon", "torreón"],
    "Colima": ["colima", "manzanillo"],
    "Durango": ["durango"],
    "Estado de México": ["estado de mexico", "estado de méxico", "toluca", "naucalpan", "tlalnepantla"],
    "Guanajuato": ["guanajuato", "leon", "león", "silao", "irapuato", "celaya"],
    "Guerrero": ["guerrero", "acapulco"],
    "Hidalgo": ["hidalgo", "pachuca"],
    "Jalisco": ["jalisco", "guadalajara", "zapopan", "tlaquepaque", "tlajomulco", "el salto"],
    "Michoacán": ["michoacan", "michoacán", "morelia"],
    "Morelos": ["morelos", "cuernavaca"],
    "Nayarit": ["nayarit", "tepic"],
    "Nuevo León": ["nuevo leon", "nuevo león", "monterrey", "apodaca", "san pedro garza garcia", "san pedro garza garcía", "santa catarina", "escobedo"],
    "Oaxaca": ["oaxaca"],
    "Puebla": ["puebla"],
    "Querétaro": ["queretaro", "querétaro", "el marques", "el marqués"],
    "Quintana Roo": ["quintana roo", "cancun", "cancún", "playa del carmen"],
    "San Luis Potosí": ["san luis potosi", "san luis potosí"],
    "Sinaloa": ["sinaloa", "culiacan", "culiacán", "mazatlan", "mazatlán"],
    "Sonora": ["sonora", "hermosillo", "nogales"],
    "Tabasco": ["tabasco", "villahermosa"],
    "Tamaulipas": ["tamaulipas", "reynosa", "matamoros", "nuevo laredo"],
    "Tlaxcala": ["tlaxcala"],
    "Veracruz": ["veracruz", "xalapa", "poza rica"],
    "Yucatán": ["yucatan", "yucatán", "merida", "mérida"],
    "Zacatecas": ["zacatecas"],
}
OCC_STATE_SLUGS = {
    "Guadalajara": "jalisco",
    "Monterrey": "nuevo-leon",
    "CDMX": "ciudad-de-mexico",
}
SOURCE_PRIORITY = {"workday": 0, "greenhouse": 0, "lever": 0, "ashby": 0, "smartrecruiters": 0, "occ": 1, "linkedin": 1, "indeed": 2, "glassdoor": 3}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ascii(value: Any) -> str:
    text = str(value or "").strip().lower()
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def clean_html(text: Any) -> str:
    if not text:
        return ""
    s = re.sub(r"<[^>]+>", " ", str(text))
    s = html_lib.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def parse_dt(value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = pd.to_datetime(value, utc=True).to_pydatetime()
        except Exception:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def age_hours(value: Any) -> float | None:
    dt = parse_dt(value)
    if not dt:
        return None
    return max(0.0, (utcnow() - dt).total_seconds() / 3600)


def is_finance_job(row: dict[str, Any]) -> bool:
    title = _ascii(row.get("title"))
    func = _ascii(row.get("job_function"))
    desc = _ascii(row.get("description"))[:6000]
    if any(_ascii(term) in title for term in FINANCE_TITLE_TERMS):
        return True
    if any(term in func for term in FINANCE_FUNCTION_TERMS):
        return True
    anchors = ("financial planning", "fp&a", "treasury", "controllership", "finance business partner")
    return any(_ascii(term) in desc for term in anchors)


def city_bucket(location: Any, requested_city: str | None = None) -> str:
    loc = _ascii(location)
    if requested_city and requested_city in CITY_ALIASES:
        if any(_ascii(a) in loc for a in CITY_ALIASES[requested_city]):
            return requested_city
    for city, aliases in CITY_ALIASES.items():
        if any(_ascii(a) in loc for a in aliases):
            return city
    region_matches: list[tuple[int, str]] = []
    for region, aliases in MEXICO_REGION_ALIASES.items():
        for alias in aliases:
            normalized = _ascii(alias)
            if normalized and normalized in loc:
                region_matches.append((len(normalized), region))
    if region_matches:
        return max(region_matches, key=lambda x: x[0])[1]
    return "Otra México" if is_mexico_location(location) else "Otra"


def is_mexico_location(location: Any) -> bool:
    loc = _ascii(location)
    if not loc:
        return False
    if "new mexico" in loc or "united states" in loc or re.search(r"\busa\b", loc):
        return False
    if any(_ascii(alias) in loc for aliases in MEXICO_REGION_ALIASES.values() for alias in aliases):
        return True
    return bool(re.search(r"\b(mexico|mexico city|cdmx)\b", loc))


def normalize_board_date(value: Any) -> tuple[datetime | None, str, str]:
    """Preserve date-only postings instead of inventing a midnight timestamp."""
    if isinstance(value, date) and not isinstance(value, datetime):
        d = value
    elif isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value.strip()):
        try:
            d = date.fromisoformat(value.strip())
        except ValueError:
            d = None
    else:
        d = None

    if d is not None:
        # Noon local keeps the calendar date stable across UTC conversion.
        dt = datetime(d.year, d.month, d.day, 12, 0, tzinfo=MX_TZ).astimezone(timezone.utc)
        return dt, "posted_date", d.isoformat()

    dt = parse_dt(value)
    return dt, "posted_at", str(value or "")


def local_days_ago(value: Any) -> int | None:
    dt = parse_dt(value)
    if not dt:
        return None
    return (datetime.now(MX_TZ).date() - dt.astimezone(MX_TZ).date()).days


def normalize_company_name(value: Any) -> str:
    s = _ascii(value).replace("&", " and ")
    s = re.sub(r"\b(inc|incorporated|corp|corporation|company|co|llc|ltd|plc|group)\b", " ", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def load_target_companies(path: str | Path | None) -> set[str]:
    if not path:
        return set()
    p = Path(path)
    if not p.exists():
        return set()
    with p.open("r", encoding="utf-8-sig", newline="") as f:
        rows = csv.DictReader(f)
        return {normalize_company_name(r.get("company")) for r in rows if r.get("company")}


def company_is_target(company: Any, targets: set[str]) -> bool:
    n = normalize_company_name(company)
    if not n:
        return False
    if n in targets:
        return True
    return any(len(t) >= 5 and (n in t or t in n) for t in targets)


def classify_seniority(title: Any) -> str:
    t = _ascii(title)
    if any(x in t for x in ("intern", "internship", "trainee", "becario", "practicante")):
        return "IC"
    if any(x in t for x in (
        "chief financial officer", "cfo", "vice president", "vp finance", "vp, finance",
        "head of finance", "finance director", "director of finance", "financial director",
        "director finance", "director, finance", "director financiero", "direccion de administracion y finanzas",
    )):
        return "Executive"
    if any(x in t for x in (
        "senior finance manager", "sr finance manager", "sr. finance manager",
        "senior fp&a manager", "sr fp&a manager", "senior financial planning manager",
        "finance business partner lead", "lead finance business partner",
        "finance lead", "financial controller", "corporate controller",
    )):
        return "Senior"
    if any(x in t for x in (
        "finance manager", "financial manager", "fp&a manager", "financial planning manager",
        "accounting manager", "treasury manager", "commercial finance manager",
        "plant controller", "controller", "contralor", "finance business partner",
        "controlling manager", "head of controlling",
    )):
        return "Manager"
    if any(x in t for x in (
        "analyst", "analista", "specialist", "especialista", "accountant", "contador",
        "coordinator", "coordinador", "assistant", "auxiliar", "clerk", "associate",
    )):
        return "IC"
    if "director" in t or "head " in t:
        return "Executive"
    if "senior manager" in t or "sr manager" in t or "lead " in t:
        return "Senior"
    if "manager" in t:
        return "Manager"
    return "Other"


def astra_score(row: dict[str, Any], targets: set[str]) -> tuple[int, str]:
    seniority = row.get("seniority") or classify_seniority(row.get("title"))
    base = {"Executive": 80, "Senior": 70, "Manager": 60, "Other": 42, "IC": 24}.get(seniority, 40)
    score = base
    reasons = [seniority]

    title = _ascii(row.get("title"))
    if any(x in title for x in ("cfo", "chief financial officer", "vice president", "head of finance")):
        score += 8; reasons.append("liderazgo ejecutivo")
    elif "director" in title:
        score += 6; reasons.append("dirección")
    elif any(x in title for x in ("senior finance manager", "sr finance manager", "plant controller", "corporate controller")):
        score += 5; reasons.append("rol objetivo")

    if company_is_target(row.get("company"), targets):
        score += 8; reasons.append("empresa objetivo")

    sources = _ascii(row.get("sources") or row.get("source"))
    if any(x in sources for x in ("workday", "greenhouse", "lever", "ashby", "smartrecruiters")):
        score += 5; reasons.append("ATS directo")
    if "," in str(row.get("sources") or ""):
        score += 2; reasons.append("multi-fuente")
    if row.get("salary"):
        score += 2; reasons.append("salario publicado")

    if row.get("freshness") in ("HOY (sin hora)", "HOT 0–24h"):
        score += 5; reasons.append("reciente")
    elif row.get("freshness") in ("AYER (sin hora)", "FRESH 24–48h"):
        score += 2

    if any(x in title for x in ("intern", "trainee", "assistant", "auxiliar", "clerk")):
        score -= 18; reasons.append("nivel junior")

    return max(0, min(100, int(score))), " · ".join(reasons)


def salary_text(row: dict[str, Any]) -> str:
    lo = row.get("min_amount")
    hi = row.get("max_amount")
    cur = row.get("currency") or ""
    interval = row.get("interval") or ""
    try:
        if pd.isna(lo): lo = None
        if pd.isna(hi): hi = None
    except Exception:
        pass
    if lo is None and hi is None:
        return ""
    if lo is not None and hi is not None:
        amount = f"{float(lo):,.0f}–{float(hi):,.0f}"
    else:
        amount = f"{float(lo if lo is not None else hi):,.0f}"
    return " ".join(x for x in [cur, amount, str(interval)] if x)


def _parse_salary_amount(value: str) -> float | None:
    raw = re.sub(r"[^0-9.,]", "", value or "")
    if not raw:
        return None
    if "," in raw and "." in raw:
        raw = raw.replace(",", "")
    elif raw.count(",") >= 1:
        parts = raw.split(",")
        raw = "".join(parts) if all(len(p) == 3 for p in parts[1:]) else raw.replace(",", ".")
    elif raw.count(".") == 1 and len(raw.split(".")[-1]) == 3:
        raw = raw.replace(".", "")
    try:
        return float(raw)
    except ValueError:
        return None


def extract_salary_from_text(text: Any, strict: bool = True) -> dict[str, Any] | None:
    plain = clean_html(text)
    if not plain:
        return None

    number = r"(\d{1,3}(?:[ ,.]\d{3})+|\d{4,7})"
    currency = r"(MXN|USD|US\$|MEX\$|\$)"
    interval = r"(mensual(?:es)?|mes|monthly|month|anual(?:es)?|annual|yearly|hora|hourly|hour)"

    labelled = re.compile(
        rf"(?is)(sueldo|salario|salary|compensaci[oó]n|compensation|rango salarial|pay range|base pay)"
        rf"[^\n.;]{{0,90}}?(?:{currency}\s*)?\$?\s*{number}"
        rf"(?:\s*(?:-|–|—|a|to)\s*(?:{currency}\s*)?\$?\s*{number})?"
        rf"[^\n.;]{{0,35}}?(?:{interval})?"
    )
    direct = re.compile(
        rf"(?is)(?:{currency}\s*)?\$?\s*{number}\s*(?:-|–|—|a|to)\s*"
        rf"(?:{currency}\s*)?\$?\s*{number}[^\n.;]{{0,35}}?(?:{interval})"
    )

    match = labelled.search(plain) or direct.search(plain)
    if not match and not strict:
        single = re.compile(
            rf"(?is)(?:{currency}\s*)?\$?\s*{number}[^\n.;]{{0,25}}?(?:{interval})"
        )
        match = single.search(plain)
    if not match:
        return None

    raw = match.group(0).strip()
    nums = re.findall(number, raw)
    amounts = [_parse_salary_amount(x) for x in nums[:2]]
    amounts = [x for x in amounts if x is not None]
    if not amounts:
        return None

    cur = "MXN"
    if re.search(r"(?i)USD|US\$", raw):
        cur = "USD"
    elif re.search(r"(?i)MXN|MEX\$", raw):
        cur = "MXN"

    interval_match = re.search(interval, raw, flags=re.I)
    period = ""
    if interval_match:
        p = _ascii(interval_match.group(0))
        if p in {"mensual", "mensuales", "mes", "monthly", "month"}:
            period = "mensual"
        elif p in {"anual", "anuales", "annual", "yearly"}:
            period = "anual"
        elif p in {"hora", "hourly", "hour"}:
            period = "hora"

    return {
        "min_amount": min(amounts),
        "max_amount": max(amounts) if len(amounts) > 1 else min(amounts),
        "currency": cur,
        "interval": period,
        "raw": raw,
    }


def apply_salary_extraction(row: dict[str, Any], source_label: str = "descripción") -> dict[str, Any]:
    if row.get("salary"):
        row["salary_source"] = row.get("salary_source") or "estructurado"
        return row
    hit = extract_salary_from_text(row.get("description"), strict=True)
    if not hit:
        return row
    row.update({k: hit[k] for k in ("min_amount", "max_amount", "currency", "interval")})
    row["salary"] = salary_text(row)
    row["salary_source"] = source_label
    row["salary_raw"] = hit["raw"]
    return row


def dedupe_key(row: dict[str, Any]) -> str:
    title = re.sub(r"[^a-z0-9]+", " ", _ascii(row.get("title")))
    company = re.sub(r"[^a-z0-9]+", " ", _ascii(row.get("company")))
    city = _ascii(row.get("city") or city_bucket(row.get("location")))
    raw = f"{company}|{title.strip()}|{city}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _row_base(**kwargs: Any) -> dict[str, Any]:
    row = {
        "id": "", "source": "", "title": "", "company": "", "location": "", "city": "",
        "posted_at": None, "date_kind": "unknown", "posted_label": "", "job_url": "", "direct_url": "",
        "description": "", "job_type": "", "job_level": "", "job_function": "", "min_amount": None,
        "max_amount": None, "currency": "", "interval": "", "is_remote": False, "salary": "",
        "salary_source": "", "salary_raw": "",
    }
    row.update(kwargs)
    row["city"] = row.get("city") or city_bucket(row.get("location"))
    row["salary"] = row.get("salary") or salary_text(row)
    if row["salary"] and not row.get("salary_source"):
        row["salary_source"] = "estructurado"
    row = apply_salary_extraction(row)
    row["dedupe_key"] = dedupe_key(row)
    return row


def search_job_boards(
    cities: list[str], hours_old: int, queries: list[str], results_wanted: int,
    sites: list[str], progress: Callable[[str], None] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    try:
        from jobspy import scrape_jobs
    except Exception as exc:
        return [], [f"JobSpy no disponible: {exc}"]

    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    total = max(1, len(cities) * len(queries))
    n = 0
    search_cities = ["Todo México"] if "Todo México" in cities else cities
    for city in search_cities:
        location = {
            "CDMX": "Mexico City, Mexico",
            "Guadalajara": "Guadalajara, Jalisco, Mexico",
            "Monterrey": "Monterrey, Nuevo Leon, Mexico",
            "Todo México": "Mexico",
        }.get(city, city)
        for query in queries:
            n += 1
            if progress:
                progress(f"LinkedIn/boards {n}/{total}: {query} · {city}")
            try:
                df = scrape_jobs(
                    site_name=sites,
                    search_term=query,
                    location=location,
                    results_wanted=results_wanted,
                    hours_old=hours_old,
                    country_indeed="Mexico",
                    fetch_description=False,
                    verbose=0,
                )
                attrs = getattr(df, "attrs", {}) or {}
                for site, msg in (attrs.get("errors") or {}).items():
                    errors.append(f"{site}: {msg}")
                for _, r in df.iterrows():
                    data = r.to_dict()
                    source = str(data.get("site") or "").lower()
                    posted, date_kind, posted_label = normalize_board_date(data.get("date_posted"))
                    resolved_city = city_bucket(data.get("location"), city if city != "Todo México" else None)
                    if city != "Todo México" and resolved_city != city:
                        continue
                    if city == "Todo México" and resolved_city == "Otra" and not is_mexico_location(data.get("location")) and not bool(data.get("is_remote") or False):
                        continue
                    if city == "Todo México" and resolved_city == "Otra":
                        resolved_city = "Remoto México" if bool(data.get("is_remote") or False) else "Otra México"
                    row = _row_base(
                        id=str(data.get("id") or ""), source=source, title=str(data.get("title") or ""),
                        company=str(data.get("company") or ""), location=str(data.get("location") or ""),
                        city=resolved_city, posted_at=posted,
                        date_kind=date_kind, posted_label=posted_label,
                        job_url=str(data.get("job_url") or ""), direct_url=str(data.get("job_url_direct") or data.get("job_url") or ""),
                        description=str(data.get("description") or ""), job_type=str(data.get("job_type") or ""),
                        job_level=str(data.get("job_level") or ""), job_function=str(data.get("job_function") or ""),
                        min_amount=data.get("min_amount"), max_amount=data.get("max_amount"), currency=str(data.get("currency") or ""),
                        interval=str(data.get("interval") or ""), is_remote=bool(data.get("is_remote") or False),
                    )
                    if is_finance_job(row):
                        if row["posted_at"] is None:
                            continue
                        if row["date_kind"] == "posted_date":
                            days = local_days_ago(row["posted_at"])
                            max_days = 0 if hours_old <= 24 else 1
                            if days is not None and 0 <= days <= max_days:
                                rows.append(row)
                        else:
                            a = age_hours(row["posted_at"])
                            if a is not None and a <= hours_old + 1:
                                rows.append(row)
            except Exception as exc:
                errors.append(f"{','.join(sites)} · {query} · {city}: {exc}")
            time.sleep(2.5 if "linkedin" in sites else 0.5)
    return rows, errors


def _slugify(value: Any) -> str:
    s = _ascii(value)
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def _occ_search_url(query: str, state: str | None, page: int) -> str:
    q = _slugify(query)
    keyword = f"de-{q}" if page <= 1 else f"de-{q}-pagina-{page}"
    suffix = f"en-{_slugify(state)}/" if state else ""
    return f"https://www.occ.com.mx/empleos/{keyword}/{suffix}"


def _occ_posted(label: str) -> tuple[datetime | None, str, str]:
    plain = _ascii(label)
    now = datetime.now(MX_TZ)
    if "hoy" in plain:
        d = now.date()
        return datetime(d.year, d.month, d.day, 12, tzinfo=MX_TZ).astimezone(timezone.utc), "posted_date", d.isoformat()
    if "ayer" in plain:
        d = (now - timedelta(days=1)).date()
        return datetime(d.year, d.month, d.day, 12, tzinfo=MX_TZ).astimezone(timezone.utc), "posted_date", d.isoformat()
    m = re.search(r"hace\s+(\d+)\s+hora", plain)
    if m:
        dt = utcnow() - timedelta(hours=int(m.group(1)))
        return dt, "relative_posted", label
    m = re.search(r"hace\s+(\d+)\s+dia", plain)
    if m:
        d = (now - timedelta(days=int(m.group(1)))).date()
        return datetime(d.year, d.month, d.day, 12, tzinfo=MX_TZ).astimezone(timezone.utc), "posted_date", d.isoformat()
    return None, "unknown", label


def _parse_occ_cards(page_html: str) -> list[dict[str, Any]]:
    cards: list[dict[str, Any]] = []
    chunks = str(page_html).split('class="card-job-offer')
    for chunk in chunks[1:]:
        id_match = re.search(r"data-id=['\"](\d+)['\"]", chunk)
        title_match = re.search(r"<h2[^>]*>([\s\S]*?)</h2>", chunk, flags=re.I)
        if not id_match or not title_match:
            continue
        job_id = id_match.group(1)
        title = clean_html(title_match.group(1))
        if not title:
            continue

        company_match = re.search(r"h-\[21px\][^>]*>\s*<span[^>]*>([\s\S]*?)</span>", chunk, flags=re.I)
        location_match = re.search(r"no-alter-loc-text[^>]*>[\s\S]*?<p[^>]*>([\s\S]*?)</p>", chunk, flags=re.I)
        company = clean_html(company_match.group(1)) if company_match else ""
        if _ascii(company) == "empresa confidencial":
            company = "?"
        location = clean_html(location_match.group(1)) if location_match else ""

        plain = clean_html(chunk)
        date_match = re.search(r"\b(Hoy|Ayer|Hace\s+\d+\s+(?:horas?|d[ií]as?))\b", plain, flags=re.I)
        posted_label = date_match.group(1) if date_match else ""
        posted_at, date_kind, normalized_label = _occ_posted(posted_label)

        salary_hit = extract_salary_from_text(plain, strict=False)
        card = {
            "id": job_id,
            "title": title,
            "company": company,
            "location": location,
            "posted_at": posted_at,
            "date_kind": date_kind,
            "posted_label": normalized_label or posted_label,
            "job_url": f"https://www.occ.com.mx/empleo/oferta/{job_id}/",
        }
        if salary_hit:
            card.update({k: salary_hit[k] for k in ("min_amount", "max_amount", "currency", "interval")})
            card["salary_raw"] = salary_hit["raw"]
        cards.append(card)
    return cards


def search_occ(
    cities: list[str], hours_old: int, queries: list[str], max_pages: int = 2,
    progress: Callable[[str], None] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/156.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "es-MX,es;q=0.9,en;q=0.7",
    })

    scopes: list[tuple[str, str | None]]
    if "Todo México" in cities:
        scopes = [("Todo México", None)]
    else:
        scopes = [(c, OCC_STATE_SLUGS.get(c)) for c in cities]

    seen: set[str] = set()
    total = max(1, len(scopes) * len(queries) * max_pages)
    step = 0
    for scope_name, state in scopes:
        for query in queries:
            first_page_answered = False
            for page in range(1, max_pages + 1):
                step += 1
                if progress:
                    progress(f"OCC {step}/{total}: {query} · {scope_name} · pág {page}")
                url = _occ_search_url(query, state, page)
                try:
                    response = session.get(url, timeout=30)
                    if response.status_code != 200:
                        raise RuntimeError(f"HTTP {response.status_code}")
                    cards = _parse_occ_cards(response.text)
                    if page == 1:
                        first_page_answered = True
                    if not cards:
                        plain = _ascii(clean_html(response.text))
                        if page == 1 and "no hay empleos que coincidan" not in plain:
                            errors.append(f"OCC · {query} · {scope_name}: cero tarjetas; posible challenge o cambio de markup")
                        break

                    fresh = 0
                    for card in cards:
                        if card["id"] in seen:
                            continue
                        seen.add(card["id"])
                        fresh += 1

                        resolved_city = city_bucket(card.get("location"))
                        if scope_name != "Todo México" and resolved_city != scope_name:
                            continue
                        if scope_name == "Todo México" and resolved_city == "Otra" and not is_mexico_location(card.get("location")):
                            continue

                        row = _row_base(
                            id=f"occ-{card['id']}", source="occ", title=card.get("title") or "",
                            company=card.get("company") or "?", location=card.get("location") or "",
                            city=resolved_city if resolved_city != "Otra" else "Otra México",
                            posted_at=card.get("posted_at"), date_kind=card.get("date_kind") or "unknown",
                            posted_label=card.get("posted_label") or "", job_url=card.get("job_url") or "",
                            direct_url=card.get("job_url") or "", description="",
                            min_amount=card.get("min_amount"), max_amount=card.get("max_amount"),
                            currency=card.get("currency") or "", interval=card.get("interval") or "",
                            salary_source="OCC" if card.get("min_amount") is not None else "",
                            salary_raw=card.get("salary_raw") or "",
                        )
                        if not is_finance_job(row) or row.get("posted_at") is None:
                            continue

                        if row.get("date_kind") == "posted_date":
                            days = local_days_ago(row.get("posted_at"))
                            max_days = 0 if hours_old <= 24 else 1
                            if days is None or not (0 <= days <= max_days):
                                continue
                        else:
                            age = age_hours(row.get("posted_at"))
                            if age is None or age > hours_old + 1:
                                continue
                        rows.append(row)

                    if fresh == 0:
                        break
                except Exception as exc:
                    if page == 1:
                        errors.append(f"OCC · {query} · {scope_name}: {exc}")
                    break
                time.sleep(0.8)
            if not first_page_answered:
                continue
    return rows, list(dict.fromkeys(errors))


def load_ats_sources(path: str | Path) -> list[dict[str, str]]:
    p = Path(path)
    if not p.exists():
        return []
    with p.open("r", encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    return [r for r in rows if str(r.get("enabled", "true")).strip().lower() not in {"0", "false", "no", "off"}]


def _requests_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT, "Accept": "application/json,text/plain,*/*"})
    return s


def fetch_greenhouse(company: str, board: str) -> list[dict[str, Any]]:
    url = f"https://boards-api.greenhouse.io/v1/boards/{board.strip('/')}/jobs?content=true"
    data = _requests_session().get(url, timeout=30).json()
    out = []
    for j in data.get("jobs", []):
        out.append(_row_base(
            id=str(j.get("id") or ""), source="greenhouse", title=j.get("title") or "", company=company,
            location=(j.get("location") or {}).get("name") or "", posted_at=parse_dt(j.get("updated_at")),
            date_kind="updated_at", posted_label=str(j.get("updated_at") or ""), job_url=j.get("absolute_url") or "",
            direct_url=j.get("absolute_url") or "", description=clean_html(j.get("content")),
        ))
    return out


def fetch_lever(company: str, board: str) -> list[dict[str, Any]]:
    url = f"https://api.lever.co/v0/postings/{board.strip('/')}?mode=json"
    data = _requests_session().get(url, timeout=30).json()
    out = []
    for j in data if isinstance(data, list) else []:
        created = j.get("createdAt")
        posted = datetime.fromtimestamp(created / 1000, tz=timezone.utc) if created else None
        cat = j.get("categories") or {}
        desc = j.get("descriptionPlain") or j.get("description") or ""
        out.append(_row_base(
            id=str(j.get("id") or ""), source="lever", title=j.get("text") or "", company=company,
            location=cat.get("location") or "", posted_at=posted, date_kind="created_at",
            posted_label=str(created or ""), job_url=j.get("hostedUrl") or "", direct_url=j.get("applyUrl") or j.get("hostedUrl") or "",
            description=clean_html(desc), job_type=cat.get("commitment") or "",
        ))
    return out


def fetch_ashby(company: str, board: str) -> list[dict[str, Any]]:
    url = f"https://api.ashbyhq.com/posting-api/job-board/{board.strip('/')}?includeCompensation=true"
    data = _requests_session().get(url, timeout=30).json()
    out = []
    for j in data.get("jobs", []):
        comp = j.get("compensation") or {}
        out.append(_row_base(
            id=str(j.get("id") or j.get("jobUrl") or ""), source="ashby", title=j.get("title") or "", company=company,
            location=j.get("location") or "", posted_at=parse_dt(j.get("publishedAt")), date_kind="published_at",
            posted_label=str(j.get("publishedAt") or ""), job_url=j.get("jobUrl") or "", direct_url=j.get("applyUrl") or j.get("jobUrl") or "",
            description=j.get("descriptionPlain") or "", salary=str(comp.get("compensationTierSummary") or ""),
        ))
    return out


def fetch_smartrecruiters(company: str, board: str, max_results: int = 500) -> list[dict[str, Any]]:
    session = _requests_session()
    out: list[dict[str, Any]] = []
    offset = 0
    limit = 100
    while offset < max_results:
        url = f"https://api.smartrecruiters.com/v1/companies/{board.strip('/')}/postings"
        r = session.get(url, params={"limit": limit, "offset": offset}, timeout=30)
        r.raise_for_status()
        data = r.json()
        posts = data.get("content", [])
        for j in posts:
            loc = j.get("location") or {}
            location = ", ".join(str(x) for x in [loc.get("city"), loc.get("region"), loc.get("country")] if x)
            posting_id = str(j.get("id") or j.get("uuid") or "")
            posting_url = f"https://jobs.smartrecruiters.com/{board.strip('/')}/{posting_id}"
            out.append(_row_base(
                id=posting_id, source="smartrecruiters", title=j.get("name") or "", company=company,
                location=location, posted_at=parse_dt(j.get("releasedDate")), date_kind="published_at",
                posted_label=str(j.get("releasedDate") or ""), job_url=posting_url, direct_url=posting_url,
                description="", detail_api=f"https://api.smartrecruiters.com/v1/companies/{board.strip('/')}/postings/{posting_id}",
                job_type=(j.get("typeOfEmployment") or {}).get("label") or "",
                job_level=(j.get("experienceLevel") or {}).get("label") or "",
                job_function=(j.get("function") or {}).get("label") or "",
            ))
        offset += len(posts)
        total = int(data.get("totalFound") or 0)
        if not posts or len(posts) < limit or (total > 0 and offset >= total):
            break
    return out


def _workday_parts(board: str) -> tuple[str, str]:
    u = urlparse(board)
    if not u.scheme:
        raise ValueError("Workday board debe ser URL completa")
    parts = [p for p in u.path.split("/") if p]
    tenant = u.netloc.split(".")[0]
    if "wday" in parts and "cxs" in parts:
        cxs_idx = parts.index("cxs")
        if len(parts) < cxs_idx + 3:
            raise ValueError("URL CXS de Workday incompleta")
        tenant = parts[cxs_idx + 1]
        site = parts[cxs_idx + 2]
    else:
        parts = [p for p in parts if p not in {"en-US", "es-MX", "en-GB"}]
        if not parts:
            raise ValueError("URL de Workday sin site")
        site = parts[0]
    endpoint = f"{u.scheme}://{u.netloc}/wday/cxs/{tenant}/{site}/jobs"
    public_base = f"{u.scheme}://{u.netloc}/{site}"
    return endpoint, public_base


def _parse_workday_posted(label: str) -> datetime | None:
    s = _ascii(label)
    now = utcnow()
    if any(x in s for x in ("today", "posted today", "hoy")):
        return now
    if any(x in s for x in ("yesterday", "ayer")):
        return now - timedelta(hours=24)
    m = re.search(r"(\d+)\s*(day|days|dia|dias|día|días)", s)
    if m:
        return now - timedelta(days=int(m.group(1)))
    m = re.search(r"(\d+)\s*(hour|hours|hora|horas)", s)
    if m:
        return now - timedelta(hours=int(m.group(1)))
    return parse_dt(label)


def fetch_workday(company: str, board: str, max_results: int = 250) -> list[dict[str, Any]]:
    endpoint, public_base = _workday_parts(board)
    s = _requests_session()
    out = []
    offset = 0
    limit = 20
    while offset < max_results:
        payload = {"appliedFacets": {}, "limit": limit, "offset": offset, "searchText": ""}
        r = s.post(endpoint, json=payload, timeout=30)
        r.raise_for_status()
        data = r.json()
        posts = data.get("jobPostings", [])
        for j in posts:
            ext = j.get("externalPath") or ""
            direct = public_base.rstrip("/") + (ext if ext.startswith("/") else "/" + ext)
            label = j.get("postedOn") or ""
            detail_api = endpoint.rsplit("/jobs", 1)[0] + ext
            out.append(_row_base(
                id=str(j.get("bulletFields", [""])[0] if j.get("bulletFields") else ext), source="workday",
                title=j.get("title") or "", company=company, location=j.get("locationsText") or "",
                posted_at=_parse_workday_posted(label), date_kind="relative_posted", posted_label=label,
                job_url=direct, direct_url=direct, description="", detail_api=detail_api,
            ))
        offset += len(posts)
        total = int(data.get("total") or 0)
        if not posts or len(posts) < limit or (total > 0 and offset >= total):
            break
    return out


def enrich_ats_detail(row: dict[str, Any]) -> dict[str, Any]:
    detail_api = row.get("detail_api")
    if not detail_api or row.get("description"):
        return apply_salary_extraction(row)
    try:
        data = _requests_session().get(str(detail_api), timeout=25).json()
        source = row.get("source")
        description = ""
        if source == "workday":
            info = data.get("jobPostingInfo") or {}
            description = clean_html(
                info.get("jobDescription")
                or info.get("description")
                or data.get("jobDescription")
                or ""
            )
        elif source == "smartrecruiters":
            sections = ((data.get("jobAd") or {}).get("sections") or {})
            parts = []
            if isinstance(sections, dict):
                for section in sections.values():
                    if isinstance(section, dict):
                        parts.append(str(section.get("text") or section.get("description") or ""))
                    elif section:
                        parts.append(str(section))
            description = clean_html(" ".join(parts))
        if description:
            row["description"] = description
    except Exception:
        # Detail enrichment must never discard an otherwise valid posting.
        pass
    return apply_salary_extraction(row)


def fetch_ats(
    sources: list[dict[str, str]], hours_old: int, cities: list[str],
    progress: Callable[[str], None] | None = None,
) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    for i, src in enumerate(sources, start=1):
        company = (src.get("company") or "").strip()
        ats = (src.get("ats") or "").strip().lower()
        board = (src.get("board") or "").strip()
        if progress:
            progress(f"ATS {i}/{len(sources)}: {company} · {ats}")
        try:
            if ats == "greenhouse": found = fetch_greenhouse(company, board)
            elif ats == "lever": found = fetch_lever(company, board)
            elif ats == "ashby": found = fetch_ashby(company, board)
            elif ats == "workday": found = fetch_workday(company, board)
            elif ats == "smartrecruiters": found = fetch_smartrecruiters(company, board)
            else:
                errors.append(f"{company}: ATS no soportado '{ats}'")
                continue
            for row in found:
                row["city"] = city_bucket(row.get("location"))
                nationwide = "Todo México" in cities
                if not nationwide and row["city"] not in cities:
                    continue
                if nationwide and row["city"] == "Otra" and not is_mexico_location(row.get("location")) and not row.get("is_remote"):
                    continue
                if nationwide and row["city"] == "Otra":
                    row["city"] = "Remoto México" if row.get("is_remote") else "Otra México"
                if not is_finance_job(row):
                    continue
                a = age_hours(row.get("posted_at"))
                if a is None:
                    continue
                if a <= hours_old + 1:
                    rows.append(enrich_ats_detail(row))
        except Exception as exc:
            errors.append(f"{company} ({ats}): {exc}")
    return rows, errors


def merge_dedupe(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        row["dedupe_key"] = row.get("dedupe_key") or dedupe_key(row)
        groups.setdefault(row["dedupe_key"], []).append(row)
    merged = []
    for _, items in groups.items():
        items.sort(key=lambda r: (SOURCE_PRIORITY.get(str(r.get("source")), 9), age_hours(r.get("posted_at")) or 999999))
        best = dict(items[0])
        best["sources"] = ", ".join(dict.fromkeys(str(i.get("source")) for i in items if i.get("source")))
        best["linkedin_url"] = next((str(i.get("job_url")) for i in items if i.get("source") == "linkedin" and i.get("job_url")), "")
        best["direct_url"] = next((str(i.get("direct_url")) for i in items if i.get("source") in {"workday", "greenhouse", "lever", "ashby", "smartrecruiters"} and i.get("direct_url")), best.get("direct_url") or best.get("job_url") or "")
        salary_item = next((i for i in items if i.get("salary")), None)
        if salary_item:
            for field in ("salary", "salary_source", "salary_raw", "min_amount", "max_amount", "currency", "interval"):
                best[field] = salary_item.get(field)
        description_item = next((i for i in items if i.get("description")), None)
        if description_item and not best.get("description"):
            best["description"] = description_item.get("description")
        dts = [parse_dt(i.get("posted_at")) for i in items if parse_dt(i.get("posted_at"))]
        if dts:
            best["posted_at"] = max(dts)
        if best.get("date_kind") == "posted_date":
            days = local_days_ago(best.get("posted_at"))
            best["age_hours"] = None
            if days == 0:
                best["freshness"] = "HOY (sin hora)"
            elif days == 1:
                best["freshness"] = "AYER (sin hora)"
            else:
                best["freshness"] = "FECHA APROX."
            best["sort_age"] = (days or 0) * 24
        else:
            best["age_hours"] = age_hours(best.get("posted_at"))
            best["freshness"] = "HOT 0–24h" if (best["age_hours"] is not None and best["age_hours"] <= 24) else "FRESH 24–48h"
            best["sort_age"] = best["age_hours"] if best["age_hours"] is not None else 999999
        merged.append(best)
    merged.sort(key=lambda r: (r.get("sort_age", 999999), str(r.get("company"))))
    return merged


def enrich_ranking(rows: list[dict[str, Any]], target_companies_path: str | Path | None = None) -> list[dict[str, Any]]:
    targets = load_target_companies(target_companies_path)
    for row in rows:
        row["seniority"] = classify_seniority(row.get("title"))
        row["priority_company"] = company_is_target(row.get("company"), targets)
        score, reason = astra_score(row, targets)
        row["astra_score"] = score
        row["score_reason"] = reason
    rows.sort(key=lambda r: (-int(r.get("astra_score") or 0), r.get("sort_age", 999999), str(r.get("company"))))
    return rows


def init_db(path: str | Path) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(p) as con:
        con.execute("""
        CREATE TABLE IF NOT EXISTS jobs (
            dedupe_key TEXT PRIMARY KEY, title TEXT, company TEXT, location TEXT, city TEXT,
            sources TEXT, posted_at TEXT, date_kind TEXT, posted_label TEXT, salary TEXT,
            direct_url TEXT, linkedin_url TEXT, description TEXT, first_seen_at TEXT, last_seen_at TEXT,
            payload TEXT
        )""")
        con.commit()


def upsert_jobs(path: str | Path, rows: list[dict[str, Any]]) -> None:
    init_db(path)
    now = utcnow().isoformat()
    with sqlite3.connect(path) as con:
        for r in rows:
            posted = parse_dt(r.get("posted_at"))
            payload = json.dumps({k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in r.items()}, ensure_ascii=False, default=str)
            con.execute("""
            INSERT INTO jobs (dedupe_key,title,company,location,city,sources,posted_at,date_kind,posted_label,salary,direct_url,linkedin_url,description,first_seen_at,last_seen_at,payload)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(dedupe_key) DO UPDATE SET
              title=excluded.title, company=excluded.company, location=excluded.location, city=excluded.city,
              sources=excluded.sources, posted_at=excluded.posted_at, date_kind=excluded.date_kind,
              posted_label=excluded.posted_label, salary=excluded.salary, direct_url=excluded.direct_url,
              linkedin_url=excluded.linkedin_url, description=excluded.description, last_seen_at=excluded.last_seen_at,
              payload=excluded.payload
            """, (
                r.get("dedupe_key"), r.get("title"), r.get("company"), r.get("location"), r.get("city"),
                r.get("sources"), posted.isoformat() if posted else None, r.get("date_kind"), r.get("posted_label"),
                r.get("salary"), r.get("direct_url"), r.get("linkedin_url"), r.get("description"), now, now, payload,
            ))
        con.commit()


def run_search(config: dict[str, Any], cities: list[str], hours_old: int, sources: list[str], results_wanted: int,
               ats_csv: str | Path, progress: Callable[[str], None] | None = None,
               target_companies_path: str | Path | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    board_sites = [s for s in sources if s in {"linkedin", "indeed", "glassdoor"}]
    if board_sites:
        r, e = search_job_boards(cities, hours_old, config.get("finance_queries", ["finance", "FP&A", "controller", "treasury"]), results_wanted, board_sites, progress)
        rows.extend(r); errors.extend(e)
    if "ats" in sources:
        r, e = fetch_ats(load_ats_sources(ats_csv), hours_old, cities, progress)
        rows.extend(r); errors.extend(e)
    if "occ" in sources:
        r, e = search_occ(cities, hours_old, config.get("occ_queries", ["finanzas", "finance", "controller", "gerente de finanzas"]), int(config.get("occ_max_pages", 2)), progress)
        rows.extend(r); errors.extend(e)
    merged = merge_dedupe(rows)
    return enrich_ranking(merged, target_companies_path), list(dict.fromkeys(errors))
