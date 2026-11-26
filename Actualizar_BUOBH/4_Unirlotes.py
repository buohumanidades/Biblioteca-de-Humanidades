"""Une lotes antiguos (`truncados_lote*.json` o `libros_lote*.json`) en `final.json`.
Nota: La versión optimizada de `3_buscarenopac.py` ya genera `final.json` directamente,
por lo que este script solo es necesario si deseas combinar lotes de ejecuciones previas.
"""

import glob
import json

todos_los_libros = []

# Busca lotes tanto con el prefijo 'truncados_lote' como 'libros_lote'
archivos = sorted(set(glob.glob("truncados_lote*.json") + glob.glob("libros_lote*.json")))

if not archivos:
    print("[Aviso] No se encontraron ficheros 'truncados_lote*.json' ni 'libros_lote*.json'.")
else:
    for archivo in archivos:
        with open(archivo, "r", encoding="utf-8") as f:
            datos = json.load(f)
            todos_los_libros.extend(datos)
            print(f"Procesado: {archivo} ({len(datos)} registros)")

    with open("final.json", "w", encoding="utf-8") as f_out:
        json.dump(todos_los_libros, f_out, ensure_ascii=False, indent=2)

    print(f"Fichero final.json creado con {len(todos_los_libros)} registros en total.")