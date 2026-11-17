# Biblioteca de Humanidades · Universidad de Oviedo

Aplicación web estática (GitHub Pages) para localizar libros, consultar bibliografía recomendada por grados y explorar materias en la Biblioteca de Humanidades (Campus de El Milán).

## Estructura principal

- `index.html`: portada, buscador principal, acceso a materias/grados y modal del Asistente Bibliográfico IA.
- `resultados.html`: buscador sobre el catálogo (`libros1.json`–`libros3.json`) con paginación incremental y apertura directa del mapa.
- `localizador.html` y `Encontrado.html`: resolución de signaturas/zonas (`signaturas.json`, `plano.json`, `todassignaturas.json`) y plano interactivo (`donde.jpg`).
- `1.html`, `7.html`, `8.html`, `9.html`, `Encontradomateria.html`: navegación por grandes áreas temáticas (CDU).
- `Grados/grado3.html` y `Grados/grado3.json`: bibliografía recomendada del Grado en Filosofía con disponibilidad física precalculada.
- `titulo.html`: búsqueda rápida por título sobre `Grados/libros_1.json`–`Grados/libros_2.json`.
- `escaner.html`: lector OCR experimental de signaturas `HG` con cámara y Tesseract.js.
- `BNE.html`: asistente independiente de consulta sobre el catálogo de la BNE.
- `js/`: módulos compartidos:
  - `js/utils.js`: normalización de texto, `escapeHTML` y sanitización con DOMPurify.
  - `js/signaturas.js`: extracción de signatura base, comparación natural (`numeric: true`) y resolución directa de mapas con caché en memoria.
  - `js/buscador.js`: carga paralela vía `manifest.json` y algoritmo de búsqueda en 4 niveles.

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
