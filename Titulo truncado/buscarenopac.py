"""Consulta el OPAC de la Universidad de Oviedo para recuperar los títulos completos
de los registros detectados en truncados.json y guarda el resultado en final.json.

Características:
  - Lee truncados.json (o cualquier JSON en formato objeto o lista compacta) sin modificarlo.
  - Guarda el progreso de forma atómica en final.json (y no_encontrados.json) cada N libros.
  - Reanudación segura: si se detiene (Ctrl+C o fallo de red), al volver a ejecutarlo
    salta los registros ya resueltos sin perder ni sobrescribir resultados anteriores.
  - Corrige la lectura de "Autor"/"autor", codifica correctamente las URLs y valida
    tanto el prefijo del título como el autor para evitar falsas coincidencias.
"""

from __future__ import annotations

import argparse
import asyncio
import re
import urllib.parse
from pathlib import Path
from typing import Any

from comun import (
    escribir_json_atomico,
    extraer_campos,
    iterar_registros_json,
    normalizar_compacto,
    normalizar_texto,
)

OPAC_BASE_URL = "https://absysweb.cpd.uniovi.es/cgi-bin/abnetopac"
FILTRO_BIBLIOTECA = "&subcat=BH"


def clave_registro(autor: str, titulo: str, fecha: str, signatura: str) -> tuple[str, str, str, str]:
    """Clave única para controlar el progreso y permitir reanudación sin duplicados."""
    return (
        signatura.strip().upper(),
        normalizar_texto(autor),
        fecha.strip(),
        normalizar_compacto(titulo),
    )


def extraer_apellido_principal(autor: str) -> str:
    """Extrae el primer apellido/bloque relevante del autor para búsquedas y validación."""
    if not autor:
        return ""
    # Quita años tipo "1948-" o "1865-1936."
    sin_fechas = re.sub(r"\b\d{4}\s*-\s*\d{0,4}\b", "", autor)
    parte = sin_fechas.split(",")[0]
    return normalizar_texto(parte)


def coincide_autor(autor_json: str, autor_web: str) -> bool:
    """Comprueba de forma flexible si el autor del OPAC corresponde al del JSON."""
    a_json = normalizar_texto(autor_json)
    a_web = normalizar_texto(autor_web)
    if not a_json or not a_web:
        return True
    if a_json in a_web or a_web in a_json:
        return True

    apellido_json = extraer_apellido_principal(autor_json)
    if apellido_json and apellido_json in a_web:
        return True

    tokens_json = {t for t in a_json.split() if len(t) >= 3 and not t.isdigit()}
    tokens_web = {t for t in a_web.split() if len(t) >= 3 and not t.isdigit()}
    return bool(tokens_json and tokens_web and (tokens_json & tokens_web))


def coincide_titulo(titulo_truncado: str, titulo_web: str) -> bool:
    """Comprueba si titulo_web es una versión completa del titulo_truncado."""
    trunc_c = normalizar_compacto(titulo_truncado)
    web_c = normalizar_compacto(titulo_web)
    if not trunc_c or not web_c:
        return False

    # 1. Coincidencia exacta de prefijo (tolerando signos eliminados como ':' o '-')
    if web_c.startswith(trunc_c):
        return True

    # 2. Tolerancia si la última palabra/caracteres del truncado quedaron cortados
    if len(trunc_c) >= 20 and web_c.startswith(trunc_c[:-4]):
        return True

    # 3. Coincidencia por palabras iniciales (al menos 4 palabras o todas menos la última cortada)
    palabras_trunc = normalizar_texto(titulo_truncado).split()
    palabras_web = normalizar_texto(titulo_web).split()
    if len(palabras_trunc) >= 4:
        base = palabras_trunc[:-1]  # excluye la última palabra potencialmente cortada
        if len(base) >= 3 and " ".join(base) in " ".join(palabras_web):
            return True
    elif palabras_trunc and " ".join(palabras_trunc) in " ".join(palabras_web):
        return True

    return False


def buscar_autor_en_dom(el_titulo: Any) -> str:
    """Localiza el elemento de autor (.js-T1XX) asociado a un resultado .js-T245."""
    nodo = el_titulo.parent
    for _ in range(3):
        if nodo is None:
            break
        el_autor = nodo.select_one(".js-T1XX")
        if el_autor:
            return el_autor.get_text(" ", strip=True)
        nodo = nodo.parent
    return ""


def elegir_mejor_titulo(
    elementos_titulo: list[Any],
    titulo_orig: str,
    autor_json: str,
) -> str | None:
    """Selecciona el título web que mejor coincide con el título truncado y el autor."""
    candidatos_validos: list[tuple[int, str]] = []

    for el_titulo in elementos_titulo:
        texto_web = " ".join(el_titulo.get_text(" ", strip=True).split())
        if not texto_web:
            continue

        if not coincide_titulo(titulo_orig, texto_web):
            continue

        autor_web = buscar_autor_en_dom(el_titulo)
        if autor_web and autor_json and not coincide_autor(autor_json, autor_web):
            continue

        # Prioriza coincidencias exactas de prefijo y títulos más completos
        prioridad = 2 if normalizar_compacto(texto_web).startswith(normalizar_compacto(titulo_orig)) else 1
        candidatos_validos.append((prioridad, texto_web))

    if not candidatos_validos:
        return None

    candidatos_validos.sort(key=lambda item: (item[0], len(item[1])), reverse=True)
    return candidatos_validos[0][1]


async def consultar_url(page: Any, url: str, timeout_ms: int) -> tuple[str, list[Any]]:
    """Carga una URL del OPAC y extrae los elementos .js-T245."""
    from bs4 import BeautifulSoup

    await page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
    try:
        await page.wait_for_load_state("networkidle", timeout=min(timeout_ms, 10000))
    except Exception:
        # Algunos recursos secundarios pueden tardar; si el DOM ya está listo continuamos
        pass

    html = await page.content()
    soup = BeautifulSoup(html, "html.parser")
    return html, soup.select(".js-T245")


async def buscar_libro_en_opac(
    page: Any,
    autor: str,
    titulo_orig: str,
    signatura: str,
    timeout_ms: int,
    pausa_reintento: float,
) -> tuple[str | None, str]:
    """Consulta el OPAC para un libro y devuelve (titulo_completo | None, ultimo_html)."""
    html = ""
    es_hg = signatura.upper().startswith("HG")

    # 1. Para signaturas no-HG, intentar primero búsqueda directa por signatura (campo 852)
    if not es_hg:
        sig_formateada = f"({signatura.replace('/', '!')}.t852.)"
        query_sig = urllib.parse.quote(sig_formateada, safe="")
        url_sig = f"{OPAC_BASE_URL}?ACC=DOSEARCH&xsqf99={query_sig}{FILTRO_BIBLIOTECA}"
        html, elementos = await consultar_url(page, url_sig, timeout_ms)
        encontrado = elegir_mejor_titulo(elementos, titulo_orig, autor)
        if encontrado:
            return encontrado, html

    # 2. Para HG (o respaldo si t852 no devolvió coincidencia): búsqueda por texto + apellido autor
    palabras = titulo_orig.split()
    # Si el título tiene varias palabras, descartamos primero la última si podría estar cortada
    intentos: list[list[str]] = []
    if len(palabras) >= 4:
        intentos.append(palabras[:-1])
    intentos.append(palabras)

    apellido = extraer_apellido_principal(autor)
    probados: set[str] = set()

    for base_palabras in intentos:
        actual = list(base_palabras)
        while len(actual) >= 3:
            titulo_intento = " ".join(actual)
            terminos = f"{titulo_intento} {apellido}".strip()
            if terminos in probados:
                actual.pop()
                continue
            probados.add(terminos)

            query_codificada = urllib.parse.quote(terminos, safe="")
            url_busqueda = f"{OPAC_BASE_URL}?ACC=DOSEARCH&xsqf99={query_codificada}{FILTRO_BIBLIOTECA}"
            html, elementos = await consultar_url(page, url_busqueda, timeout_ms)
            encontrado = elegir_mejor_titulo(elementos, titulo_orig, autor)
            if encontrado:
                return encontrado, html

            actual.pop()
            if pausa_reintento > 0:
                await asyncio.sleep(pausa_reintento)

    return None, html


def cargar_estado_previo(ruta: Path) -> tuple[list[dict[str, str]], set[tuple[str, str, str, str]]]:
    """Carga registros ya guardados en final.json o no_encontrados.json para reanudar."""
    if not ruta.exists():
        return [], set()

    registros = list(iterar_registros_json(ruta))
    claves: set[tuple[str, str, str, str]] = set()
    for reg in registros:
        autor, titulo, fecha, sig = extraer_campos(reg)
        # Si guardamos titulo_original, usamos ese para la clave de reanudación
        tit_ref = reg.get("_titulo_truncado", titulo) if isinstance(reg, dict) else titulo
        claves.add(clave_registro(autor, tit_ref, fecha, sig))
    return registros, claves


def limpiar_salida_final(registros: list[dict[str, str]]) -> list[dict[str, str]]:
    """Devuelve registros limpios listos para sustituir.py (sin metadatos internos)."""
    salida: list[dict[str, str]] = []
    for reg in registros:
        salida.append(
            {
                "Autor": reg.get("Autor", ""),
                "titulo": reg.get("titulo", ""),
                "fecha": reg.get("fecha", ""),
                "signatura": reg.get("signatura", ""),
            }
        )
    return salida


async def ejecutar_busqueda(
    ruta_entrada: Path,
    ruta_salida: Path,
    ruta_no_encontrados: Path,
    guardar_cada: int = 20,
    pausa: float = 2.5,
    pausa_reintento: float = 1.5,
    timeout_ms: int = 25000,
    limite: int | None = None,
    reintentar_fallidos: bool = False,
    guardar_html_error: bool = False,
    headless: bool = True,
) -> None:
    try:
        from playwright.async_api import async_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Falta 'playwright'. Instálalo con:\n"
            "  pip install playwright beautifulsoup4\n"
            "  playwright install chromium"
        ) from exc

    if not ruta_entrada.exists():
        raise FileNotFoundError(
            f"No se encontró el archivo de entrada: {ruta_entrada.resolve()}\n"
            "Ejecuta primero 'python truncados.py' para generar truncados.json."
        )

    registros_entrada = list(iterar_registros_json(ruta_entrada))

    # Cargar progreso previo para reanudación segura
    resultados_finales, claves_resueltas = cargar_estado_previo(ruta_salida)
    # También indexamos por (sig, autor, fecha) por si final.json ya tiene el título cambiado
    resueltas_por_id = {
        (r.get("signatura", "").strip().upper(), normalizar_texto(r.get("Autor", "")), r.get("fecha", "").strip())
        for r in resultados_finales
        if isinstance(r, dict)
    }

    if reintentar_fallidos:
        no_encontrados: list[dict[str, str]] = []
        claves_fallidas: set[tuple[str, str, str, str]] = set()
    else:
        no_encontrados, claves_fallidas = cargar_estado_previo(ruta_no_encontrados)

    pendientes: list[tuple[str, str, str, str]] = []
    omitidos_previos = 0
    descartados = 0

    for reg in registros_entrada:
        autor, titulo, fecha, sig = extraer_campos(reg)
        if not sig or not titulo or sig.upper().startswith("EXP"):
            descartados += 1
            continue

        clv = clave_registro(autor, titulo, fecha, sig)
        id_basico = (sig.strip().upper(), normalizar_texto(autor), fecha.strip())
        if clv in claves_resueltas or clv in claves_fallidas or id_basico in resueltas_por_id:
            omitidos_previos += 1
            continue

        pendientes.append((autor, titulo, fecha, sig))

    if limite is not None:
        pendientes = pendientes[:limite]

    print("=== BÚSQUEDA DE TÍTULOS COMPLETOS EN OPAC ===")
    print(f"Entrada              : {ruta_entrada.resolve()} ({len(registros_entrada)} registros)")
    print(f"Salida (final.json)  : {ruta_salida.resolve()} ({len(resultados_finales)} ya guardados)")
    print(f"Omitidos ya tratados : {omitidos_previos}")
    print(f"Descartados (EXP/vac): {descartados}")
    print(f"Pendientes en sesión : {len(pendientes)}")

    if not pendientes:
        print("No hay registros pendientes por consultar.")
        return

    carpeta_html = ruta_salida.parent / "errores_html"
    if guardar_html_error:
        carpeta_html.mkdir(parents=True, exist_ok=True)

    procesados_sesion = 0
    encontrados_sesion = 0
    fallidos_sesion = 0

    def volcar_progreso() -> None:
        escribir_json_atomico(ruta_salida, limpiar_salida_final(resultados_finales), compacto=False)
        escribir_json_atomico(ruta_no_encontrados, no_encontrados, compacto=False)

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=headless)
        context = await browser.new_context()

        # Bloquea imágenes, fuentes y hojas de estilo para acelerar las consultas al OPAC
        await context.route(
            "**/*",
            lambda route: route.abort()
            if route.request.resource_type in {"image", "media", "font", "stylesheet"}
            else route.continue_(),
        )
        page = await context.new_page()

        try:
            total = len(pendientes)
            for idx, (autor, titulo_orig, fecha, signatura) in enumerate(pendientes, start=1):
                procesados_sesion += 1
                try:
                    titulo_completo, html = await buscar_libro_en_opac(
                        page=page,
                        autor=autor,
                        titulo_orig=titulo_orig,
                        signatura=signatura,
                        timeout_ms=timeout_ms,
                        pausa_reintento=pausa_reintento,
                    )

                    if titulo_completo:
                        resultados_finales.append(
                            {
                                "Autor": autor,
                                "titulo": titulo_completo,
                                "fecha": fecha,
                                "signatura": signatura,
                            }
                        )
                        encontrados_sesion += 1
                        print(f"[{idx}/{total}] [OK] {signatura} -> {titulo_completo}")
                    else:
                        no_encontrados.append(
                            {
                                "Autor": autor,
                                "titulo": titulo_orig,
                                "fecha": fecha,
                                "signatura": signatura,
                                "_titulo_truncado": titulo_orig,
                            }
                        )
                        fallidos_sesion += 1
                        print(f"[{idx}/{total}] [NO ENCONTRADO] {signatura} | {titulo_orig}")

                        if guardar_html_error and html:
                            nombre_limpio = re.sub(r'[\\/:*?"<>|\s]+', "_", signatura)
                            (carpeta_html / f"error_{nombre_limpio}.html").write_text(html, encoding="utf-8")

                except Exception as exc:
                    no_encontrados.append(
                        {
                            "Autor": autor,
                            "titulo": titulo_orig,
                            "fecha": fecha,
                            "signatura": signatura,
                            "_titulo_truncado": titulo_orig,
                            "_error": str(exc),
                        }
                    )
                    fallidos_sesion += 1
                    print(f"[{idx}/{total}] [ERROR] {signatura}: {exc}")

                if procesados_sesion % guardar_cada == 0:
                    volcar_progreso()
                    print(
                        f"[*] Progreso guardado ({len(resultados_finales)} en {ruta_salida.name}, "
                        f"{len(no_encontrados)} en {ruta_no_encontrados.name})."
                    )

                if idx < total and pausa > 0:
                    await asyncio.sleep(pausa)

        except KeyboardInterrupt:
            print("\n[!] Interrupción detectada (Ctrl+C). Guardando progreso antes de salir...")
        finally:
            volcar_progreso()
            await browser.close()

    print("\n=== RESUMEN DE SESIÓN ===")
    print(f"Consultados en sesión : {procesados_sesion}")
    print(f"Encontrados en sesión : {encontrados_sesion}")
    print(f"No encontrados/errores: {fallidos_sesion}")
    print(f"Total en final.json   : {len(resultados_finales)} ({ruta_salida.resolve()})")
    print(f"Total sin coincidencia: {len(no_encontrados)} ({ruta_no_encontrados.resolve()})")


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Busca en el OPAC de la Universidad de Oviedo los títulos completos de truncados.json y genera final.json."
    )
    parser.add_argument(
        "--entrada",
        "-i",
        type=Path,
        default=Path("truncados.json"),
        help="Archivo JSON con los títulos truncados (por defecto: truncados.json).",
    )
    parser.add_argument(
        "--salida",
        "-o",
        type=Path,
        default=Path("final.json"),
        help="Archivo JSON de salida con los títulos completos (por defecto: final.json).",
    )
    parser.add_argument(
        "--no-encontrados",
        type=Path,
        default=Path("no_encontrados.json"),
        help="Archivo JSON donde se registran los títulos sin coincidencia (por defecto: no_encontrados.json).",
    )
    parser.add_argument(
        "--guardar-cada",
        type=int,
        default=20,
        help="Frecuencia de autoguardado en número de registros (por defecto: 20).",
    )
    parser.add_argument(
        "--pausa",
        type=float,
        default=2.5,
        help="Segundos de espera entre libros para no saturar el OPAC (por defecto: 2.5).",
    )
    parser.add_argument(
        "--pausa-reintento",
        type=float,
        default=1.5,
        help="Segundos de espera entre reintentos de búsqueda para un mismo libro (por defecto: 1.5).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=25000,
        help="Tiempo máximo de espera por página en milisegundos (por defecto: 25000).",
    )
    parser.add_argument(
        "--limite",
        type=int,
        default=None,
        help="Limita el número de libros a consultar en esta ejecución (útil para pruebas, ej. --limite 10).",
    )
    parser.add_argument(
        "--reintentar-fallidos",
        action="store_true",
        help="Vuelve a consultar los registros guardados previamente en no_encontrados.json.",
    )
    parser.add_argument(
        "--guardar-html-error",
        action="store_true",
        help="Guarda el HTML de los casos no encontrados dentro de errores_html/ para depuración.",
    )
    parser.add_argument(
        "--mostrar-navegador",
        action="store_true",
        help="Abre Chromium visible en pantalla en lugar de modo oculto (headless).",
    )
    return parser


def main() -> None:
    args = construir_parser().parse_args()
    asyncio.run(
        ejecutar_busqueda(
            ruta_entrada=args.entrada,
            ruta_salida=args.salida,
            ruta_no_encontrados=args.no_encontrados,
            guardar_cada=args.guardar_cada,
            pausa=args.pausa,
            pausa_reintento=args.pausa_reintento,
            timeout_ms=args.timeout,
            limite=args.limite,
            reintentar_fallidos=args.reintentar_fallidos,
            guardar_html_error=args.guardar_html_error,
            headless=not args.mostrar_navegador,
        )
    )


if __name__ == "__main__":
    main()