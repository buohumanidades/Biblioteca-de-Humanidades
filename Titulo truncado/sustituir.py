import json
import glob
import re

# 1. Cargar final.json y generar los índices de búsqueda
with open('final.json', 'r', encoding='utf-8') as f:
    datos_final = json.load(f)

titulos_por_signatura = {}
titulos_por_hg = {}

for registro in datos_final:
    signatura = registro.get("signatura")
    titulo = registro.get("titulo")
    
    if not signatura or not titulo:
        continue
        
    if signatura.startswith("HG"):
        fecha = registro.get("fecha")
        autor = registro.get("Autor")
        if fecha and autor:
            titulos_por_hg[(signatura, fecha, autor)] = titulo
    else:
        titulos_por_signatura[signatura] = titulo

# 2. Localizar y procesar los ficheros a modificar
archivos_destino = [f for f in sorted(glob.glob("libros*.json")) if re.match(r'libros\d+\.json', f)]
errores = []

print("=== RESULTADOS DE SUSTITUCIÓN ===")

for archivo in archivos_destino:
    contador_procesados = 0
    contador_sustituidos = 0
    
    try:
        with open(archivo, 'r', encoding='utf-8') as f:
            datos_destino = json.load(f)
            
        for registro in datos_destino:
            contador_procesados += 1
            signatura = registro.get("signatura")
            
            if not signatura:
                errores.append(f"ADVERTENCIA: Registro sin signatura omitido en {archivo}.")
                continue
            
            nuevo_titulo = None
            
            # 3. Evaluar la condición de sustitución
            if signatura.startswith("HG"):
                fecha = registro.get("fecha")
                autor = registro.get("Autor")
                clave = (signatura, fecha, autor)
                nuevo_titulo = titulos_por_hg.get(clave)
            else:
                nuevo_titulo = titulos_por_signatura.get(signatura)
            
            # 4. Actualizar el título si hay coincidencia
            if nuevo_titulo:
                registro["titulo"] = nuevo_titulo
                contador_sustituidos += 1
                
        # 5. Sobrescribir el archivo con los títulos actualizados
        with open(archivo, 'w', encoding='utf-8') as f_out:
            json.dump(datos_destino, f_out, ensure_ascii=False, indent=4)
            
        print(f"[{archivo}] Procesados: {contador_procesados} | Sustituidos: {contador_sustituidos}")
        
    except Exception as e:
        errores.append(f"ERROR: Fallo al procesar {archivo}. Detalle: {e}")

if errores:
    print("\n=== ADVERTENCIAS Y ERRORES ===")
    for error in errores:
        print(error)