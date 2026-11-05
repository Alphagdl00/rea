# Astra Finance Radar v3.4

Radar local de vacantes financieras para **todo México** o para Guadalajara, Monterrey y CDMX, centrado en publicaciones de las últimas **24 o 48 horas**.

## Qué hace esta versión

- Busca LinkedIn mediante `python-jobspy`, sin iniciar sesión con tu cuenta personal.
- Consulta ATS corporativos directamente: **Workday, Greenhouse, Lever, Ashby y SmartRecruiters**.
- Consulta **OCC** mediante sus páginas públicas server-rendered, sin iniciar sesión.
- Permite buscar **Todo México** y clasifica resultados por región/estado cuando la ubicación lo permite.
- Busca un universo amplio de Finanzas: Finance, Financial, FP&A, Controller, Treasury y Accounting Manager, y después filtra localmente títulos/funciones financieras.
- Deduplica LinkedIn vs ATS por empresa + puesto + ciudad.
- Clasifica automáticamente el seniority: **Executive / Senior / Manager / Other / IC**.
- Calcula un **Score Astra 0–100** y ordena por relevancia ejecutiva, empresa objetivo, fuente ATS y frescura.
- El tablero muestra por defecto **Executive + Senior + Manager**, pero conserva IC/Other para consulta.
- Mantiene un catálogo de empresas objetivo en `target_companies.csv`.
- Cuando la misma vacante existe en LinkedIn y en un ATS, prioriza el **enlace corporativo directo** y conserva el enlace de LinkedIn.
- Separa **HOT 0–24h** y **FRESH 24–48h**.
- Muestra salario cuando la fuente lo publica.
- Si no hay salario estructurado, intenta extraer **rangos salariales dentro de la descripción** y marca la fuente del dato.
- Para Workday y SmartRecruiters consulta el detalle solo de vacantes financieras recientes para enriquecer descripción/salario.
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

### OCC

OCC se consulta como bolsa nacional por palabras clave. Astra usa páginas públicas de búsqueda y, cuando la tarjeta no trae fecha o salario, consulta el detalle solo de la vacante financiera candidata. La integración no usa cuenta ni sesión. Si OCC presenta un challenge o cambia el markup, Astra muestra el aviso en lugar de fingir una búsqueda vacía.

### ATS

`ats_sources.csv` contiene el catálogo de empresas/boards. Formato:

```csv
company,ats,board,enabled
Empresa,greenhouse,board-token,true
Empresa,lever,board-token,true
Empresa,ashby,board-token,true
Empresa,workday,https://tenant.wdX.myworkdayjobs.com/Site,true
```

Esta versión amplía las fuentes ATS verificadas a Authentic Brands Group, Takeda, Ipsen, Aptiv, Baxter, BorgWarner, Carrier, Flex, HPE, HP, IQVIA, Jabil, Medtronic, Stryker y Bosch. El catálogo puede seguir creciendo editando `ats_sources.csv`.

## Cómo trata las fechas

| Fuente | Campo utilizado | Etiqueta en Astra |
|---|---|---|
| LinkedIn / JobSpy | `date_posted` | Publicación |
| Lever | `createdAt` | Creación/publicación |
| Ashby | `publishedAt` | Publicación |
| SmartRecruiters | `releasedDate` | Publicación |
| Workday | `postedOn` | Publicación relativa |
| Greenhouse | `updated_at` | Actualización |
| OCC | tarjeta pública + `datePosted` del detalle cuando está disponible | Publicación |

Greenhouse no proporciona de forma consistente una fecha original de publicación en su endpoint público. Por eso Astra no la presenta como tal.

## Arquitectura

- `app.py`: interfaz Streamlit.
- `astra_core.py`: conectores, filtros, normalización, deduplicación y SQLite.
- `ats_sources.csv`: catálogo de ATS.
- `target_companies.csv`: universo de empresas objetivo para scoring.
- `config.json`: términos de búsqueda.
- `run_astra.bat`: arranque de un clic en Windows.
- `THIRD_PARTY_NOTICES.md`: atribución MIT del diseño del parser OCC.

## Nota de uso responsable

No se conecta a tu sesión personal de LinkedIn, no usa cookies de tu cuenta y no automatiza solicitudes de empleo. LinkedIn puede cambiar o limitar sus endpoints; por eso Astra está diseñado como un agregador híbrido y no depende de una sola fuente.

## Score Astra

El score no reemplaza el juicio humano. Sirve para ordenar el ruido:

- Executive parte arriba de Senior, Senior arriba de Manager, y Manager arriba de IC.
- Empresas del universo objetivo reciben prioridad.
- Vacantes con ATS corporativo directo reciben prioridad sobre agregadores.
- Vacantes recientes reciben prioridad.
- Salario publicado y confirmación por múltiples fuentes suman puntos.
- Internships/assistant/auxiliar/clerk reciben penalización.

## Corrección de Workday v3

Algunos tenants Workday devuelven `total=0` aunque sí regresen páginas de vacantes. Astra ya no corta la paginación por ese valor: continúa mientras reciba páginas completas, hasta llegar al final o al límite de seguridad.

## Salarios en v3.1

Astra diferencia el origen del dato:

- **estructurado**: la fuente entrega mínimo/máximo/moneda de forma explícita.
- **OCC**: rango visible en la tarjeta o detalle de OCC.
- **descripción**: rango detectado dentro del texto del anuncio.

El extractor busca expresiones explícitas como sueldo, salario, compensation, salary o pay range y requiere cifras monetarias. No convierte beneficios como aguinaldo, prima vacacional o días de descanso en salario.

Para proteger la cobertura de LinkedIn frente a 429, Astra **no abre automáticamente la descripción de cada vacante de LinkedIn**. Si LinkedIn entrega salario estructurado, se conserva; la detección en texto se concentra en ATS y OCC, donde podemos hacerlo con menos presión de requests.

## Todo México

Selecciona **Todo México** en Ubicación. Esa opción reemplaza las ciudades individuales durante la corrida. LinkedIn/Indeed/Glassdoor buscan a nivel país, los ATS aceptan cualquier ubicación mexicana y OCC omite el filtro estatal. La tabla conserva la ubicación original y clasifica la región cuando es posible.

## Compensation Fit v3.2

Astra no trata el seniority como sinónimo de oportunidad atractiva. Si una vacante publica compensación y puede normalizarse a MXN mensuales, el score se ajusta contra pisos editables por nivel:

- Executive: 140,000 MXN/mes
- Senior: 110,000 MXN/mes
- Manager: 90,000 MXN/mes

Los pisos iniciales son conservadores y se pueden modificar desde el panel lateral.

Ajustes de score:

- <= 50% del piso: -40
- < 70% del piso: -30
- < 85% del piso: -20
- < 100% del piso: -10
- >= 100% del piso: +3
- >= 125% del piso: +6

Si la vacante no publica salario, Astra no la penaliza. El objetivo es evitar que un título ejecutivo con compensación débil aparezca artificialmente arriba del ranking.

La tabla muestra:

- `MXN/mes`: midpoint normalizado de la compensación publicada.
- `Fit compensación`: comparación contra el piso configurado.
- `Δ Score`: ajuste aplicado al Score Astra.

## LinkedIn priority enrichment v3.3

LinkedIn search cards normally provide title, company, location and date, but not always compensation or description. Astra now enriches a **small priority subset**:

- Only roles classified as Executive, Senior or Manager.
- Only roles that still have no detected salary after deduplication.
- Maximum 8 LinkedIn job detail pages per run by default.
- Executive roles are enriched before Senior/Manager roles.
- 3-second delay between detail requests.
- The enrichment stops immediately on HTTP 429.

No LinkedIn account, cookies or authenticated session are used.

When a public detail page exposes a compensation block or an explicit salary range in the description, Astra records it as `LinkedIn detalle` and recalculates Compensation Fit. This is specifically intended to catch cases where a title such as CFO looks attractive but the published compensation is below the configured floor.

Settings live in `config.json`:

```json
{
  "linkedin_priority_enrichment": true,
  "linkedin_enrichment_max_jobs": 8,
  "linkedin_enrichment_delay_seconds": 3.0
}
```

## LinkedIn detail diagnostics v3.4

The LinkedIn detail enrichment now uses the same request headers/session behavior as JobSpy, but Astra controls the HTTP response itself instead of relying on JobSpy's internal method that can swallow detail-page failures.

After each run Astra displays a visible summary such as:

```
LinkedIn detalle resumen: 6/8 enriquecidas; 2 con salario
```

If LinkedIn returns HTTP 403/429, enrichment stops immediately and the UI reports it. This makes it possible to distinguish:

- detail page reached, no salary published;
- detail page enriched successfully;
- LinkedIn blocked the detail pass.

The salary parser also supports single monthly values such as `$70,000/mo`, not only salary ranges.
