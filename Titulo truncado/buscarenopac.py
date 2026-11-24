import json
import glob
import asyncio
import urllib.parse
import re
import unicodedata
from playwright.async_api import async_playwright
from bs4 import BeautifulSoup

# --- CONFIGURACIÓN ---
FILTRO_BIBLIOTECA = "&subcat=BH" 

def limpiar_texto(texto):
    if not texto:
        return ""
    texto = texto.lower()
    texto = ''.join(c for c in unicodedata.normalize('NFD', texto) if unicodedata.category(c) != 'Mn')
    texto = re.sub(r'[^\w\s]', '', texto)
    return " ".join(texto.split())

async def procesar_ficheros():
    # Buscamos los json originales, evitando procesar los lotes que vayamos creando
    archivos_json = [f for f in glob.glob("libro*.json") if "_lote" not in f and "_actualizado" not in f]
    
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        page = await browser.new_page()
        
        for archivo in archivos_json:
            print(f"\n--- Procesando {archivo} ---")
            
            with open(archivo, 'r', encoding='utf-8') as f:
                registros_crudos = json.load(f)
                
            # --- FILTRO INICIAL ---
            pendientes = []
            descartados = []
            
            for reg in registros_crudos:
                sig = reg.get("signatura", "")
                tit = reg.get("titulo", "")
                
                if sig and tit and not sig.upper().startswith('EXP') and tit.strip():
                    pendientes.append(reg)
                else:
                    descartados.append(reg)
                
            total_libros = len(pendientes)
            print(f"Total de libros válidos a procesar en este archivo: {total_libros}")
            
            lote_actual = []
            lote_contador = 1
            idx = 0
            
            # Usamos un bucle while para ir vaciando la lista de pendientes
            while pendientes:
                # Extraemos el primer elemento de los pendientes (se borra de la lista)
                registro = pendientes.pop(0)
                idx += 1
                signatura = registro.get("signatura")
                
                try:
                    autor_json = registro.get("autor", "").strip()
                    titulo_orig = registro.get("titulo", "")
                    
                    titulo_encontrado = None
                    elementos_titulo = []
                    html = ""
                    
                    if signatura.lower().startswith('hg'):
                        palabras_titulo_json = titulo_orig.split()
                        
                        while len(palabras_titulo_json) >= 3:
                            titulo_intento = " ".join(palabras_titulo_json)
                            terminos_busqueda = f"{titulo_intento} {autor_json}".strip()
                            query_codificada = urllib.parse.quote(terminos_busqueda)
                            url_busqueda = f"https://absysweb.cpd.uniovi.es/cgi-bin/abnetopac?ACC=DOSEARCH&xsqf99={query_codificada}{FILTRO_BIBLIOTECA}"
                            
                            await page.goto(url_busqueda)
                            await page.wait_for_load_state("networkidle")
                            
                            html = await page.content()
                            soup = BeautifulSoup(html, 'html.parser')
                            elementos_titulo = soup.select(".js-T245")
                            
                            if elementos_titulo:
                                break 
                                
                            palabras_titulo_json.pop()
                            await asyncio.sleep(2) 
                            
                    else:
                        sig_formateada = f"({signatura.replace('/', '!')}.t852.)"
                        url_busqueda = f"https://absysweb.cpd.uniovi.es/cgi-bin/abnetopac?ACC=DOSEARCH&xsqf99={sig_formateada}{FILTRO_BIBLIOTECA}"
                        await page.goto(url_busqueda)
                        await page.wait_for_load_state("networkidle")
                        html = await page.content()
                        soup = BeautifulSoup(html, 'html.parser')
                        elementos_titulo = soup.select(".js-T245")

                    if elementos_titulo:
                        for el_titulo in elementos_titulo:
                            texto_web = el_titulo.get_text(strip=True)
                            texto_web_limpio = limpiar_texto(texto_web)
                            coincidencia = False
                            
                            if signatura.lower().startswith('hg'):
                                palabras_json_comp = titulo_orig.split()
                                
                                if len(palabras_json_comp) >= 3:
                                    while len(palabras_json_comp) >= 3:
                                        candidato = " ".join(palabras_json_comp)
                                        candidato_limpio = limpiar_texto(candidato)
                                        
                                        if candidato_limpio in texto_web_limpio:
                                            coincidencia = True
                                            break
                                        palabras_json_comp.pop()
                                else:
                                    candidato_limpio = limpiar_texto(titulo_orig)
                                    if candidato_limpio in texto_web_limpio:
                                        coincidencia = True
                            else:
                                palabras_clave_titulo = " ".join(limpiar_texto(titulo_orig).split()[:2])
                                palabras_web_2 = " ".join(texto_web_limpio.split()[:2])
                                if palabras_clave_titulo == palabras_web_2:
                                    coincidencia = True
                            
                            if coincidencia:
                                el_autor = el_titulo.parent.select_one(".js-T1XX")
                                if el_autor and autor_json:
                                    autor_web_limpio = limpiar_texto(el_autor.get_text(strip=True))
                                    autor_json_limpio = limpiar_texto(autor_json)
                                    if autor_json_limpio in autor_web_limpio or autor_web_limpio in autor_json_limpio:
                                        titulo_encontrado = texto_web
                                        break
                                else:
                                    titulo_encontrado = texto_web
                                    break
                    
                    if titulo_encontrado:
                        registro["titulo"] = titulo_encontrado
                        print(f"[{idx}/{total_libros}] [OK] {signatura} -> {titulo_encontrado}")
                    else:
                        print(f"[{idx}/{total_libros}] [Aviso] No coincidencia para {signatura}. Guardando HTML...")
                        nombre_limpio = signatura.replace('/', '_').replace(':', '_').replace('*', '_').replace('?', '_')
                        with open(f"error_{nombre_limpio}.html", "w", encoding="utf-8") as f_html:
                            f_html.write(html)
                            
                except Exception as e:
                    print(f"[{idx}/{total_libros}] [Error] Fallo en signatura {signatura}: {e}")
                
                # Añadimos el registro (procesado con éxito o fallido) al lote actual
                lote_actual.append(registro)
                
                # --- LÓGICA DE AUTOGUARDADO CADA 20 Y ACTUALIZACIÓN DEL ORIGINAL ---
                if len(lote_actual) == 20:
                    nombre_lote = archivo.replace(".json", f"_lote{lote_contador:03d}.json")
                    
                    # 1. Guardamos el lote de 20
                    with open(nombre_lote, 'w', encoding='utf-8') as f_lote:
                        json.dump(lote_actual, f_lote, ensure_ascii=False, indent=4)
                        
                    # 2. Actualizamos el archivo original para que solo tenga los descartados + pendientes restantes
                    registros_restantes = descartados + pendientes
                    with open(archivo, 'w', encoding='utf-8') as f_orig:
                        json.dump(registros_restantes, f_orig, ensure_ascii=False, indent=4)
                        
                    print(f"[*] Guardado '{nombre_lote}'. El archivo original se ha actualizado y le quedan {len(pendientes)} registros por procesar.")
                    
                    # Reiniciamos el lote
                    lote_actual = []
                    lote_contador += 1
                
                await asyncio.sleep(3) 
            
            # --- GUARDADO FINAL SI QUEDAN REGISTROS EN EL ÚLTIMO LOTE ---
            if lote_actual:
                nombre_lote = archivo.replace(".json", f"_lote{lote_contador:03d}.json")
                with open(nombre_lote, 'w', encoding='utf-8') as f_lote:
                    json.dump(lote_actual, f_lote, ensure_ascii=False, indent=4)
                
                # El archivo original queda solo con los descartados, ya que los pendientes se agotaron
                with open(archivo, 'w', encoding='utf-8') as f_orig:
                    json.dump(descartados, f_orig, ensure_ascii=False, indent=4)
                    
                print(f"[*] Guardado lote final '{nombre_lote}'. Archivo original vaciado de registros válidos.")
            
        await browser.close()

if __name__ == "__main__":
    asyncio.run(procesar_ficheros())