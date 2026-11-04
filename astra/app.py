from __future__ import annotations

import json
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import streamlit as st

from astra_core import init_db, run_search, upsert_jobs

BASE = Path(__file__).resolve().parent
CONFIG_PATH = BASE / "config.json"
ATS_PATH = BASE / "ats_sources.csv"
DB_PATH = BASE / "data" / "astra_jobs.db"
MX_TZ = ZoneInfo("America/Mexico_City")

st.set_page_config(page_title="Astra Finance Radar", page_icon="📡", layout="wide")


@st.cache_data
def load_config():
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


config = load_config()
init_db(DB_PATH)

st.title("Astra Finance Radar")
st.caption("LinkedIn + ATS corporativos · Finanzas · Guadalajara / Monterrey / CDMX · 24–48 horas")

with st.sidebar:
    st.header("Búsqueda")
    cities = st.multiselect("Ciudades", ["Guadalajara", "Monterrey", "CDMX"], default=["Guadalajara", "Monterrey", "CDMX"])
    window = st.radio("Antigüedad máxima", [24, 48], index=1, format_func=lambda x: f"{x} horas")
    st.subheader("Fuentes")
    use_linkedin = st.checkbox("LinkedIn", value=True)
    use_ats = st.checkbox("ATS corporativos", value=True)
    use_indeed = st.checkbox("Indeed", value=False)
    use_glassdoor = st.checkbox("Glassdoor", value=False)
    results_wanted = st.slider("Resultados por consulta/fuente", min_value=20, max_value=100, value=50, step=10)
    search_now = st.button("Buscar ahora", type="primary", use_container_width=True)
    st.caption("No requiere iniciar sesión en LinkedIn. Si LinkedIn limita peticiones, Astra conserva ATS y muestra el error sin inventar resultados.")

sources = []
if use_linkedin:
    sources.append("linkedin")
if use_ats:
    sources.append("ats")
if use_indeed:
    sources.append("indeed")
if use_glassdoor:
    sources.append("glassdoor")

if search_now:
    if not cities:
        st.error("Selecciona al menos una ciudad.")
    elif not sources:
        st.error("Selecciona al menos una fuente.")
    else:
        status = st.empty()

        def progress(msg: str):
            status.info(msg)

        with st.spinner("Buscando vacantes reales..."):
            jobs, errors = run_search(config, cities, window, sources, results_wanted, ATS_PATH, progress)
            upsert_jobs(DB_PATH, jobs)
            st.session_state["jobs"] = jobs
            st.session_state["errors"] = errors
        status.success(f"Búsqueda terminada: {len(jobs)} vacantes únicas.")

jobs = st.session_state.get("jobs", [])
errors = st.session_state.get("errors", [])

if errors:
    with st.expander(f"Avisos de fuentes ({len(errors)})", expanded=False):
        for e in errors:
            st.warning(e)

if not jobs:
    st.info("Pulsa **Buscar ahora**. La primera ejecución puede tardar más porque consulta varias combinaciones de ciudad y términos financieros.")
    st.stop()

raw = pd.DataFrame(jobs)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Vacantes únicas", len(raw))
c2.metric("HOT ≤24h", int((raw["age_hours"] <= 24).sum()))
c3.metric("ATS presentes", int(raw["sources"].str.contains("workday|greenhouse|lever|ashby", case=False, na=False).sum()))
c4.metric("Con LinkedIn", int(raw["sources"].str.contains("linkedin", case=False, na=False).sum()))

st.subheader("Resultados")
f1, f2, f3 = st.columns([2, 1, 1])
text_filter = f1.text_input("Filtrar título / empresa", placeholder="Ej. director, controller, pharma...")
city_filter = f2.multiselect("Ciudad", sorted(raw["city"].dropna().unique().tolist()), default=[])
fresh_filter = f3.multiselect("Ventana", ["HOT 0–24h", "FRESH 24–48h"], default=[])

df = raw.copy()
if text_filter:
    needle = text_filter.lower()
    df = df[df.apply(lambda r: needle in str(r.get("title", "")).lower() or needle in str(r.get("company", "")).lower(), axis=1)]
if city_filter:
    df = df[df["city"].isin(city_filter)]
if fresh_filter:
    df = df[df["freshness"].isin(fresh_filter)]


def local_time(v):
    try:
        ts = pd.to_datetime(v, utc=True)
        return ts.tz_convert(MX_TZ).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ""


df["Publicado / actualizado"] = df["posted_at"].apply(local_time)
df["Edad"] = df["age_hours"].apply(lambda x: f"{x:.1f} h" if pd.notna(x) else "")
df["Fecha fuente"] = df["date_kind"].map({
    "posted_at": "Publicación",
    "published_at": "Publicación",
    "created_at": "Creación/publicación",
    "relative_posted": "Publicación relativa",
    "updated_at": "Actualización",
}).fillna(df["date_kind"])

view_cols = [
    "freshness", "Edad", "title", "company", "city", "location", "sources",
    "Publicado / actualizado", "Fecha fuente", "salary", "direct_url", "linkedin_url",
]
view = df[[c for c in view_cols if c in df.columns]].rename(columns={
    "freshness": "Ventana",
    "title": "Puesto",
    "company": "Empresa",
    "city": "Ciudad",
    "location": "Ubicación",
    "sources": "Fuentes",
    "salary": "Salario",
    "direct_url": "Aplicar",
    "linkedin_url": "LinkedIn",
})

st.dataframe(
    view,
    use_container_width=True,
    hide_index=True,
    column_config={
        "Aplicar": st.column_config.LinkColumn("Aplicar", display_text="Abrir"),
        "LinkedIn": st.column_config.LinkColumn("LinkedIn", display_text="Ver"),
    },
)

csv_bytes = view.to_csv(index=False).encode("utf-8-sig")
st.download_button("Descargar resultados CSV", csv_bytes, file_name="astra_finance_jobs.csv", mime="text/csv")

st.divider()
st.subheader("Cómo interpretar las fechas")
st.caption("LinkedIn/JobSpy, Lever y Ashby entregan fecha de publicación/creación. Workday suele entregar una etiqueta relativa ('Today', '2 Days Ago'). Greenhouse expone updated_at, por lo que Astra la marca explícitamente como actualización y no la presenta como fecha original de publicación.")
