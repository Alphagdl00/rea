from __future__ import annotations

import csv
import hashlib
import json
import re
import sqlite3
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlparse

import pandas as pd
import requests

USER_AGENT = "AstraFinanceRadar/2.0 (+local job-search tool)"
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
    "CDMX": ["ciudad de mexico", "ciudad de méxico", "mexico city", "méxico city", "cdmx", "distrito federal", "santa fe", "polanco", "miguel hidalgo", "cuajimalpa", "azcapotzalco"],
}
SOURCE_PRIORITY = {"workday": 0, "greenhouse": 0, "lever": 0, "ashby": 0, "linkedin": 1, "indeed": 2, "glassdoor": 3}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _ascii(value: Any) -> str:
    text = str(value or "").strip().lower()
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def clean_html(text: Any) -> str:
    if not text:
        return ""
    s = re.sub(r"<[^>]+>", " ", str(text))
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
    return requested_city or "Otra"


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
    }
    row.update(kwargs)
    row["city"] = row.get("city") or city_bucket(row.get("location"))
    row["salary"] = row.get("salary") or salary_text(row)
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
    for city in cities:
        location = {"CDMX": "Mexico City, Mexico", "Guadalajara": "Guadalajara, Jalisco, Mexico", "Monterrey": "Monterrey, Nuevo Leon, Mexico"}.get(city, city)
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
                    posted = parse_dt(data.get("date_posted"))
                    source = str(data.get("site") or "").lower()
                    row = _row_base(
                        id=str(data.get("id") or ""), source=source, title=str(data.get("title") or ""),
                        company=str(data.get("company") or ""), location=str(data.get("location") or ""),
                        city=city_bucket(data.get("location"), city), posted_at=posted,
                        date_kind="posted_at", posted_label=str(data.get("date_posted") or ""),
                        job_url=str(data.get("job_url") or ""), direct_url=str(data.get("job_url_direct") or data.get("job_url") or ""),
                        description=str(data.get("description") or ""), job_type=str(data.get("job_type") or ""),
                        job_level=str(data.get("job_level") or ""), job_function=str(data.get("job_function") or ""),
                        min_amount=data.get("min_amount"), max_amount=data.get("max_amount"), currency=str(data.get("currency") or ""),
                        interval=str(data.get("interval") or ""), is_remote=bool(data.get("is_remote") or False),
                    )
                    if is_finance_job(row):
                        a = age_hours(row["posted_at"])
                        if a is None or a <= hours_old + 1:
                            rows.append(row)
            except Exception as exc:
                errors.append(f"{','.join(sites)} · {query} · {city}: {exc}")
            time.sleep(0.45)
    return rows, errors


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
            out.append(_row_base(
                id=str(j.get("bulletFields", [""])[0] if j.get("bulletFields") else ext), source="workday",
                title=j.get("title") or "", company=company, location=j.get("locationsText") or "",
                posted_at=_parse_workday_posted(label), date_kind="relative_posted", posted_label=label,
                job_url=direct, direct_url=direct, description="",
            ))
        offset += len(posts)
        total = int(data.get("total") or 0)
        if not posts or offset >= total:
            break
    return out


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
            else:
                errors.append(f"{company}: ATS no soportado '{ats}'")
                continue
            for row in found:
                row["city"] = city_bucket(row.get("location"))
                if row["city"] not in cities:
                    continue
                if not is_finance_job(row):
                    continue
                a = age_hours(row.get("posted_at"))
                if a is None:
                    continue
                if a <= hours_old + 1:
                    rows.append(row)
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
        best["direct_url"] = next((str(i.get("direct_url")) for i in items if i.get("source") in {"workday", "greenhouse", "lever", "ashby"} and i.get("direct_url")), best.get("direct_url") or best.get("job_url") or "")
        dts = [parse_dt(i.get("posted_at")) for i in items if parse_dt(i.get("posted_at"))]
        if dts:
            best["posted_at"] = max(dts)
        best["age_hours"] = age_hours(best.get("posted_at"))
        best["freshness"] = "HOT 0–24h" if (best["age_hours"] is not None and best["age_hours"] <= 24) else "FRESH 24–48h"
        merged.append(best)
    merged.sort(key=lambda r: (r.get("age_hours") is None, r.get("age_hours") or 0, str(r.get("company"))))
    return merged


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
               ats_csv: str | Path, progress: Callable[[str], None] | None = None) -> tuple[list[dict[str, Any]], list[str]]:
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    board_sites = [s for s in sources if s in {"linkedin", "indeed", "glassdoor"}]
    if board_sites:
        r, e = search_job_boards(cities, hours_old, config.get("finance_queries", ["finance", "FP&A", "controller", "treasury"]), results_wanted, board_sites, progress)
        rows.extend(r); errors.extend(e)
    if "ats" in sources:
        r, e = fetch_ats(load_ats_sources(ats_csv), hours_old, cities, progress)
        rows.extend(r); errors.extend(e)
    return merge_dedupe(rows), list(dict.fromkeys(errors))
