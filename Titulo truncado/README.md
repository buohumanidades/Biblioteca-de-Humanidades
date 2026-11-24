# Corrección de títulos truncados (`libros<N>.json`)

Esta carpeta contiene las herramientas para **detectar títulos cortados a 60 caracteres** en los archivos `libros1.json`, `libros2.json`, `libros3.json`, etc. y **sustituirlos de forma segura** por sus títulos completos procedentes de `final.json`.

## Archivos

- `comun.py`: utilidades compartidas (soporte automático para formato compacto `[Autor, titulo, fecha, signatura]` y formato objeto `{"Autor", "titulo", "fecha", "signatura"}`, normalización de texto y escritura atómica).
- `truncados.py`: analiza los archivos `libros<N>.json` y genera `truncados.json` con los registros cuyo título parece inacabado.
- `sustituir.py`: lee `final.json` y actualiza los títulos en `libros<N>.json` protegiendo contra colisiones de signaturas compartidas (`HG`, `HA`, `LDP`, `HR`, etc.).

## Flujo de trabajo paso a paso

### Paso 1: Detectar títulos truncados

Desde esta carpeta (detecta automáticamente `libros1.json`..`libros3.json` en la raíz del proyecto, o usa `--dir` si los tienes en otra ruta):

```bash
python truncados.py
```

Opciones útiles:
- `--dir <carpeta>`: indica una carpeta concreta donde están los `libros<N>.json`.
- `--salida truncados.json`: ruta del archivo de salida.
- `--sin-duplicados`: evita registros repetidos en `truncados.json`.
- `--incluir-motivo`: añade `motivo_truncado` (`palabra_colgante`, `fragmento_corto`, `puntuacion_abierta`, `parentesis_sin_cerrar`, `limite_caracteres`) para facilitar la revisión.
- `--solo-heuristica`: ignora la regla de límite exacto de 60 caracteres y aplica solo heurísticas lingüísticas.

### Paso 2: Obtener los títulos completos (`final.json`)

Completa o enriquece los títulos a partir de `truncados.json` y guarda el resultado como `final.json` (se acepta tanto formato de lista de objetos como lista compacta).

### Paso 3: Simular la sustitución antes de modificar nada

```bash
python sustituir.py --final final.json --dry-run
```

Esto muestra cuántos registros se actualizarán, ejemplos de `ANTES -> DESPUÉS` y posibles advertencias, **sin tocar ningún archivo**.

### Paso 4: Aplicar la sustitución

```bash
python sustituir.py --final final.json
```

Qué hace automáticamente por seguridad:
1. **Copia de seguridad**: guarda los originales en `backups/<fecha_hora>/` antes de escribir (puede desactivarse con `--sin-backup`).
2. **Cruce seguro sin colisiones**: busca en orden `(signatura, fecha, autor)` -> `(signatura, autor)` -> `signatura` (solo si no empieza por `HG`), y verifica que el título nuevo continúe el título truncado (tolerando tildes y puntuación eliminada). Si hay ambigüedad o el prefijo no coincide, no sobrescribe el registro y lo avisa.
3. **Mantiene el formato original**: si los `libros<N>.json` están en formato compacto minificado, los vuelve a guardar exactamente en formato compacto minificado.
4. **Escritura atómica**: escribe primero en un archivo temporal y lo reemplaza al final para evitar archivos corruptos si se interrumpe la ejecución.
