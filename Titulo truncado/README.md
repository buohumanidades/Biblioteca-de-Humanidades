# Corrección de títulos truncados (`libros<N>.json`)

Esta carpeta contiene el flujo completo en **3 pasos** para detectar títulos cortados a 60 caracteres en `libros1.json`–`libros3.json`, recuperar los títulos completos desde el OPAC de la Universidad de Oviedo y sustituirlos de forma segura.

## Archivos

- `comun.py`: utilidades compartidas (soporte automático para formato compacto `[Autor, titulo, fecha, signatura]` y formato objeto `{"Autor", "titulo", "fecha", "signatura"}`, normalización de texto y escritura atómica).
- `truncados.py`: **Paso 1** — analiza `libros1.json`, `libros2.json`, `libros3.json` y genera `truncados.json`.
- `buscarenopac.py`: **Paso 2** — consulta el OPAC (`absysweb.cpd.uniovi.es`, Biblioteca de Humanidades) para los registros de `truncados.json` y genera `final.json` (con autoguardado y reanudación segura).
- `sustituir.py`: **Paso 3** — lee `final.json` y actualiza los títulos en `libros<N>.json` protegiendo contra colisiones de signaturas compartidas (`HG`, `HA`, `LDP`, `HR`, etc.).

## Requisitos (solo para `buscarenopac.py`)

`truncados.py` y `sustituir.py` solo usan Python estándar. Para `buscarenopac.py` en modo HTTP rápido solo necesitas:

```powershell
pip install beautifulsoup4 requests
```

*(Opcional: si alguna vez necesitases usar `--motor playwright`, instala también `pip install playwright` y `playwright install chromium`.)*

## Flujo de trabajo paso a paso

### Paso 1: Detectar títulos truncados (`truncados.py`)

```powershell
python truncados.py --sin-duplicados
```

Genera `truncados.json`. Opciones útiles:
- `--dir <carpeta>`: indica la carpeta donde están los `libros<N>.json` (si no están en la carpeta actual ni en la raíz del proyecto).
- `--incluir-motivo`: añade `motivo_truncado` para revisar por qué se marcó cada título.
- `--solo-heuristica`: aplica solo heurísticas lingüísticas (sin marcar automáticamente todos los títulos de 60 caracteres).

### Paso 2: Recuperar títulos completos desde el OPAC (`buscarenopac.py`)

Conviene probar primero con 20 libros:

```powershell
python buscarenopac.py --limite 20
```

Y luego lanzar el proceso completo:

```powershell
python buscarenopac.py --concurrencia 4
```

- **Motor rápido automático (`--motor auto`):** usa peticiones HTTP directas con Keep-Alive y cookies (sin abrir navegador) y solo recurre a Playwright si el OPAC lo exigiera.
- **Concurrencia y caché:** consulta varios libros en paralelo (`--concurrencia 4`, recomendado 3–6) y resuelve al instante los ejemplares repetidos en memoria sin volver a consultar el OPAC.
- **No modifica** `truncados.json` ni `libros<N>.json`.
- **Reanudación segura:** guarda el progreso cada 20 libros en `final.json` y `no_encontrados.json` (mostrando velocidad y tiempo restante estimado). Si pulsas `Ctrl+C`, al volver a ejecutarlo continúa donde se quedó.
- Opciones útiles: `--motor http|playwright|auto`, `--concurrencia 4`, `--pausa 0.35`, `--reintentar-fallidos`, `--guardar-html-error`.

### Paso 3: Simular y aplicar la sustitución (`sustituir.py`)

Primero simula los cambios sin tocar ningún archivo:

```powershell
python sustituir.py --dry-run
```

Si todo se ve correcto, aplica la sustitución real:

```powershell
python sustituir.py
```

Medidas de seguridad automáticas:
1. Crea copia de seguridad en `backups/<fecha_hora>/` antes de sobrescribir (desactivable con `--sin-backup`).
2. Verifica que el título nuevo continúe el truncado y cruza por `(signatura, fecha, autor)` antes que solo por `signatura`, evitando corromper libros con signaturas compartidas (`HG`, `HA`, `LDP`, etc.).
3. Mantiene el formato original (minificado compacto o indentado) y escribe de forma atómica.
