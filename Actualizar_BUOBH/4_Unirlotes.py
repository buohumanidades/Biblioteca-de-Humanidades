import json
import glob

todos_los_libros = []

# Busca todos los ficheros que sigan el patrón de nombre
archivos = sorted(glob.glob("libros_lote*.json"))

for archivo in archivos:
    with open(archivo, 'r', encoding='utf-8') as f:
        datos = json.load(f)
        # Añade los elementos de la lista al conjunto global
        todos_los_libros.extend(datos)
        print(f"Procesado: {archivo} ({len(datos)} registros)")

# Escribe la lista combinada en el archivo final
with open("final.json", 'w', encoding='utf-8') as f_out:
    json.dump(todos_los_libros, f_out, ensure_ascii=False, indent=4)

print(f"Fichero final.json creado con {len(todos_los_libros)} registros en total.")