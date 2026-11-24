# Biblioteca de Humanidades · Universidad de Oviedo

Aplicación web estática (GitHub Pages) para localizar libros, consultar bibliografía recomendada por grados y explorar materias en la Biblioteca de Humanidades (Campus de El Milán).

## Estructura principal

- `index.html`: portada, buscador principal, acceso a materias/grados y modal del Asistente Bibliográfico IA.
- `resultados.html`: buscador sobre el catálogo (`libros1.json`–`libros3.json`) con paginación incremental y apertura directa del mapa.
- `localizador.html` y `Encontrado.html`: resolución de signaturas/zonas (`signaturas.json`, `plano.json`, `todassignaturas.json`) y plano interactivo (`donde.jpg`).
- `1.html`, `7.html`, `8.html`, `9.html`, `Encontradomateria.html`: navegación por grandes áreas temáticas (CDU).
- `Grados/grado3.html` y `Grados/grado3.json`: bibliografía recomendada del Grado en Filosofía con disponibilidad física precalculada.
- `BNE.html`: asistente independiente de consulta sobre el catálogo de la BNE.
- `js/`: módulos compartidos:
  - `js/utils.js`: normalización de texto, `escapeHTML` y sanitización con DOMPurify.
  - `js/signaturas.js`: extracción de signatura base, comparación natural (`numeric: true`) y resolución directa de mapas con caché en memoria.
  - `js/buscador.js`: carga paralela vía `manifest.json` y algoritmo de búsqueda en 4 niveles.
  - `js/asistente.js`: motor bibliotecario local en lenguaje natural (IDF, apellidos del catálogo, sinónimos, filtros, CDU y guías de investigación).
  - `js/aprendizaje.js`: aprendizaje continuo en cliente (`localStorage`) y sincronización opcional con Google Apps Script (feedback implícito/explícito, re-ranking dinámico y lagunas de búsqueda).
  - `js/horarios.js`: motor de estado de apertura en tiempo real (`Europe/Madrid`) que lee `data/horarios.json`.
- `data/horarios.json`: calendario editable de horarios habituales, periodos especiales (verano, Navidad, exámenes), festivos fijos y anuales (nacionales, Asturias, Oviedo y Universidad de Oviedo) y cierres/horarios puntuales.
- `data/sinonimos.json`: diccionario editable de sinónimos bibliográficos y equivalencias de fechas/siglas (`"36"`, `"98"`, `"27"`, `"asturianu"`, etc.) sin tocar JavaScript.
- `scripts/google_apps_script_asistente_rag.gs`: plantilla actualizada para Google Apps Script con RAG sobre los candidatos reales del catálogo, modelos activos (Gemini / Groq) y registro colectivo en Google Sheets (`Feedback`, `Lagunas`, `Sinonimos`).

## Actualización de datos del catálogo

Cuando se exporten nuevos ficheros `libros1.json`, `libros2.json`, `libros3.json`, `Grados/libros_1.json`, `Grados/libros_2.json` o `Grados/grado3.json`, ejecuta:

```bash
python scripts/optimizar_datos.py
```

El script:
1. Compacta los catálogos JSON en arrays sin duplicados ni registros `EXP`.
2. Precalcula el campo `disponibilidad` en `Grados/grado3.json` para evitar descargar todo el catálogo en el cliente.
3. Actualiza `manifest.json` y optimiza las imágenes añadidas.

> Nota: Si actualizas `Grados/grado3.json`, recuerda incrementar el parámetro `?v=...` en `Grados/grado3.html` (`fetch('grado3.json?v=...')`) para invalidar la caché del navegador.

## Archivos heredados (candidatos a retirar cuando se confirme que no tienen enlaces externos)

- `materia.html` (sustituida por `1.html`, `7.html`, `8.html`, `9.html` y `Encontradomateria.html`).
- `Asistente.html` (versión standalone anterior; el asistente activo está integrado en `index.html`).
- `libros.json` (exportación antigua no referenciada por las páginas actuales).
