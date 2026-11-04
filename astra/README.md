# Astra Finance Radar v2

Radar local de vacantes financieras para **Guadalajara, Monterrey y CDMX**, centrado en publicaciones de las últimas **24 o 48 horas**.

## Qué hace esta versión

- Busca LinkedIn mediante `python-jobspy`, sin iniciar sesión con tu cuenta personal.
- Consulta ATS corporativos directamente: **Workday, Greenhouse, Lever y Ashby**.
- Busca un universo amplio de Finanzas: Finance, Financial, FP&A, Controller, Treasury y Accounting Manager, y después filtra localmente títulos/funciones financieras.
- Deduplica LinkedIn vs ATS por empresa + puesto + ciudad.
- Cuando la misma vacante existe en LinkedIn y en un ATS, prioriza el **enlace corporativo directo** y conserva el enlace de LinkedIn.
- Separa **HOT 0–24h** y **FRESH 24–48h**.
- Muestra salario cuando la fuente lo publica.
- Guarda cada ejecución en SQLite (`data/astra_jobs.db`) y permite exportar CSV.
- Nunca convierte una fecha de actualización de Greenhouse en una supuesta fecha de publicación: la etiqueta como **Actualización**.

## Windows: ejecución

1. Abre la carpeta `astra`.
2. Haz doble clic en `run_astra.bat`.
3. La primera vez crea `.venv`, instala dependencias y abre `http://localhost:8501`.
4. Presiona **Buscar ahora**.

Requiere Python 3.10 o superior. **No requiere ejecutar como Administrador.**

## Fuentes

### LinkedIn

Astra usa JobSpy. `hours_old` se envía a la búsqueda para limitar los resultados a 24/48 horas. LinkedIn puede responder con HTTP 429 si se hacen demasiadas consultas; Astra muestra el error y continúa con los ATS, en vez de fabricar resultados.

### ATS

`ats_sources.csv` contiene el catálogo de empresas/boards. Formato:

```csv
company,ats,board,enabled
Empresa,greenhouse,board-token,true
Empresa,lever,board-token,true
Empresa,ashby,board-token,true
Empresa,workday,https://tenant.wdX.myworkdayjobs.com/Site,true
```

Esta versión incluye tres fuentes iniciales que ya habíamos identificado: Authentic Brands Group, Takeda e Ipsen. El siguiente paso natural es cargar aquí el catálogo de empresas objetivo de Astra.

## Cómo trata las fechas

| Fuente | Campo utilizado | Etiqueta en Astra |
|---|---|---|
| LinkedIn / JobSpy | `date_posted` | Publicación |
| Lever | `createdAt` | Creación/publicación |
| Ashby | `publishedAt` | Publicación |
| Workday | `postedOn` | Publicación relativa |
| Greenhouse | `updated_at` | Actualización |

Greenhouse no proporciona de forma consistente una fecha original de publicación en su endpoint público. Por eso Astra no la presenta como tal.

## Arquitectura

- `app.py`: interfaz Streamlit.
- `astra_core.py`: conectores, filtros, normalización, deduplicación y SQLite.
- `ats_sources.csv`: catálogo de ATS.
- `config.json`: términos de búsqueda.
- `run_astra.bat`: arranque de un clic en Windows.

## Nota de uso responsable

No se conecta a tu sesión personal de LinkedIn, no usa cookies de tu cuenta y no automatiza solicitudes de empleo. LinkedIn puede cambiar o limitar sus endpoints; por eso Astra está diseñado como un agregador híbrido y no depende de una sola fuente.
