import json
import glob
import re

def es_inacabada(titulo):
    titulo_limpio = titulo.replace('"', '').replace("'", "").strip()
    palabras = titulo_limpio.split()
    
    if not palabras:
        return False
        
    ultima_palabra = palabras[-1].lower()
    
    # 1. Termina en un artículo, preposición o conjunción
    colgantes = {
        'el', 'la', 'los', 'las', 'un', 'una', 'unos', 'unas',
        'de', 'del', 'en', 'por', 'con', 'a', 'al', 'y', 'e', 'o', 'u',
        'para', 'como', 'sin', 'sobre', 'su', 'sus', 'the', 'and', 'in', 'of', 'or'
    }
    
    if ultima_palabra in colgantes:
        return True
        
    # 2. La última palabra es un fragmento cortado de 1 o 2 letras
    if len(ultima_palabra) <= 2 and ultima_palabra not in colgantes:
        return True
        
    return False

archivos_origen = [f for f in sorted(glob.glob("libros*.json")) if re.match(r'libros\d+\.json', f)]
registros_inacabados = []

for archivo in archivos_origen:
    try:
        with open(archivo, 'r', encoding='utf-8') as f:
            datos = json.load(f)
            
        for registro in datos:
            titulo = registro.get("titulo", "")
            
            # Comprueba la longitud (>= 55) y si la frase cumple los patrones de truncamiento
            if len(titulo) >= 55 and es_inacabada(titulo):
                registros_inacabados.append(registro)
                
    except Exception as e:
        print(f"Error leyendo {archivo}: {e}")

with open('truncados.json', 'w', encoding='utf-8') as f_out:
    json.dump(registros_inacabados, f_out, ensure_ascii=False, indent=4)

print(f"Fichero 'truncados.json' creado con {len(registros_inacabados)} títulos inacabados.")