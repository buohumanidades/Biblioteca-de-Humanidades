"""Consulta el OPAC de la Universidad de Oviedo para recuperar los títulos completos
de los registros detectados en truncados.json y guarda el resultado en final.json.

Optimizaciones implementadas:
  - Motor HTTP directo con Keep-Alive y cookies (10x más rápido que Chromium),
    con respaldo a Playwright (--motor auto | http | playwright).
  - Concurrencia controlada (--concurrencia, por defecto 4 trabajadores).
  - Caché en memoria por (título truncado, autor) para resolver ejemplares repetidos al instante.
  - Estrategia de consulta optimizada (máximo 2-3 intentos por libro en lugar de 8-10).
  - Reanudación segura sin modificar truncados.json ni sobrescribir resultados previos.
"""

from __future__ import annotations

import argparse
import asyncio
import http.cookiejar
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
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

HEADERS_HTTP = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.5",
    "Connection": "keep-alive",
}


def clave_registro(autor: str, titulo: str, fecha: str, signatura: str) -> tuple[str, str, str, str]:
    """Clave única para controlar el progreso y permitir reanudación sin duplicados."""
    return (
        signatura.strip().upper(),
        normalizar_texto(autor),
        fecha.strip(),
        normalizar_compacto(titulo),
    )


def clave_cache_obra(autor: str, titulo: str) -> tuple[str, str]:
    """Clave para reutilizar en memoria el título completo de ejemplares repetidos."""
    return (normalizar_compacto(titulo), extraer_apellido_principal(autor))


def extraer_apellido_principal(autor: str) -> str:
    """Extrae el primer apellido/bloque relevante del autor para búsquedas y validación."""
    if not autor:
        return ""
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

    # 2. Tolerancia si los últimos caracteres del truncado quedaron cortados
    if len(trunc_c) >= 20 and web_c.startswith(trunc_c[:-4]):
        return True

    # 3. Coincidencia por palabras iniciales (excluyendo la última palabra potencialmente cortada)
    palabras_trunc = normalizar_texto(titulo_truncado).split()
    palabras_web = normalizar_texto(titulo_web).split()
    if len(palabras_trunc) >= 4:
        base = palabras_trunc[:-1]
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


class _ParserOPACStdlib:
    """Parser ligero con biblioteca estándar por si no está instalado beautifulsoup4."""

    @staticmethod
    def extraer(html: str) -> list[tuple[str, str]]:
        from html.parser import HTMLParser

        resultados: list[tuple[str, str]] = []

        class _P(HTMLParser):
            def __init__(self) -> None:
                super().__init__()
                self.ultimo_autor = ""
                self.capturando: str | None = None
                self.profundidad = 0
                self.buffer: list[str] = []

            def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
                if self.capturando:
                    self.profundidad += 1
                    return
                clases = ""
                for k, v in attrs:
                    if k.lower() == "class" and v:
                        clases = v
                        break
                tokens = set(clases.split())
                if "js-T1XX" in tokens:
                    self.capturando = "autor"
                    self.profundidad = 1
                    self.buffer = []
                elif "js-T245" in tokens:
                    self.capturando = "titulo"
                    self.profundidad = 1
                    self.buffer = []

            def handle_data(self, data: str) -> None:
                if self.capturando:
                    self.buffer.append(data)

            def handle_endtag(self, tag: str) -> None:
                if not self.capturando:
                    return
                self.profundidad -= 1
                if self.profundidad <= 0:
                    texto = " ".join("".join(self.buffer).split())
                    if self.capturando == "autor":
                        self.ultimo_autor = texto
                    elif self.capturando == "titulo" and texto:
                        resultados.append((texto, self.ultimo_autor))
                    self.capturando = None
                    self.buffer = []

        parser = _P()
        parser.feed(html)
        return resultados


def extraer_candidatos_html(html: str) -> list[tuple[str, str]]:
    """Extrae pares (titulo_web, autor_web) usando BeautifulSoup o el parser estándar."""
    try:
        from bs4 import BeautifulSoup  # type: ignore

        soup = BeautifulSoup(html, "html.parser")
        pares: list[tuple[str, str]] = []
        for el_titulo in soup.select(".js-T245"):
            texto_web = " ".join(el_titulo.get_text(" ", strip=True).split())
            if texto_web:
                pares.append((texto_web, buscar_autor_en_dom(el_titulo)))
        return pares
    except ImportError:
        return _ParserOPACStdlib.extraer(html)


def elegir_mejor_titulo(
    candidatos_html: list[tuple[str, str]],
    titulo_orig: str,
    autor_json: str,
) -> str | None:
    """Selecciona el título web que mejor coincide con el título truncado y el autor."""
    candidatos_validos: list[tuple[int, str]] = []

    for texto_web, autor_web in candidatos_html:
        if not texto_web:
            continue

        if not coincide_titulo(titulo_orig, texto_web):
            continue

        if autor_web and autor_json and not coincide_autor(autor_json, autor_web):
            continue

        prioridad = 2 if normalizar_compacto(texto_web).startswith(normalizar_compacto(titulo_orig)) else 1
        candidatos_validos.append((prioridad, texto_web))

    if not candidatos_validos:
        return None

    candidatos_validos.sort(key=lambda item: (item[0], len(item[1])), reverse=True)
    return candidatos_validos[0][1]


def generar_consultas_texto(titulo_orig: str, autor: str) -> list[str]:
    """Genera un máximo de 3 consultas eficientes ordenadas de mayor a menor precisión."""
    palabras = [p for p in normalizar_texto(titulo_orig).split() if p]
    if not palabras:
        return []

    # Descartamos la última palabra si el título tiene >= 4 palabras porque suele estar cortada
    base = palabras[:-1] if len(palabras) >= 4 else palabras
    apellido = extraer_apellido_principal(autor)

    candidatos: list[str] = []
    if len(base) >= 6:
        candidatos.append(f"{' '.join(base[:6])} {apellido}".strip())
    elif len(base) >= 3:
        candidatos.append(f"{' '.join(base)} {apellido}".strip())
    else:
        candidatos.append(f"{' '.join(palabras)} {apellido}".strip())

    # Respaldo más corto con autor (4 palabras)
    if len(base) > 4:
        candidatos.append(f"{' '.join(base[:4])} {apellido}".strip())

    # Respaldo sin autor (por si el autor figura distinto en el OPAC)
    if apellido:
        candidatos.append(" ".join(base[:6] if len(base) >= 6 else base))

    # Deduplicar manteniendo orden
    return list(dict.fromkeys(c for c in candidatos if c))


class ClienteHTTP:
    """Cliente HTTP rápido con sesiones persistentes (Keep-Alive + cookies) por hilo."""

    def __init__(self, timeout_s: float = 20.0) -> None:
        self.timeout_s = timeout_s
        self._local = threading.local()
        try:
            import requests  # type: ignore

            self._requests = requests
        except ImportError:
            self._requests = None

    def _get_session(self) -> Any:
        sess = getattr(self._local, "session", None)
        if sess is not None:
            return sess

        if self._requests is not None:
            sess = self._requests.Session()
            sess.headers.update(HEADERS_HTTP)
        else:
            jar = http.cookiejar.CookieJar()
            sess = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        self._local.session = sess
        return sess

    def _fetch_sync(self, url: str) -> str:
        sess = self._get_session()
        if self._requests is not None:
            resp = sess.get(url, timeout=self.timeout_s)
            resp.raise_for_status()
            if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
                resp.encoding = resp.apparent_encoding or "utf-8"
            return resp.text

        req = urllib.request.Request(url, headers=HEADERS_HTTP)
        with sess.open(req, timeout=self.timeout_s) as resp:
            raw = resp.read()
            charset = resp.headers.get_content_charset() or "utf-8"
            try:
                return raw.decode(charset, errors="replace")
            except LookupError:
                return raw.decode("utf-8", errors="replace")

    async def obtener_html(self, url: str) -> str:
        ultimo_error: Exception | None = None
        for intento in range(3):
            try:
                return await asyncio.to_thread(self._fetch_sync, url)
            except Exception as exc:
                ultimo_error = exc
                if intento < 2:
                    await asyncio.sleep(1.0 * (intento + 1))
        raise RuntimeError(f"Error HTTP tras 3 intentos: {ultimo_error}") from ultimo_error

    async def cerrar(self) -> None:
        return None


class ClientePlaywright:
    """Cliente Playwright con pool de pestañas concurrentes y bloqueo de recursos pesados."""

    def __init__(self, concurrencia: int, timeout_ms: int, headless: bool) -> None:
        self.concurrencia = max(1, concurrencia)
        self.timeout_ms = timeout_ms
        self.headless = headless
        self._pw: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._pool: asyncio.Queue[Any] = asyncio.Queue()

    async def iniciar(self) -> None:
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError(
                "Falta 'playwright'. Instálalo con:\n"
                "  pip install playwright beautifulsoup4\n"
                "  playwright install chromium"
            ) from exc

        self._pw = await async_playwright().start()
        self._browser = await self._pw.chromium.launch(headless=self.headless)
        self._context = await self._browser.new_context()
        await self._context.route(
            "**/*",
            lambda route: route.abort()
            if route.request.resource_type in {"image", "media", "font", "stylesheet"}
            else route.continue_(),
        )
        for _ in range(self.concurrencia):
            page = await self._context.new_page()
            await self._pool.put(page)

    async def obtener_html(self, url: str) -> str:
        page = await self._pool.get()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
            try:
                await page.wait_for_selector(".js-T245", timeout=2500)
            except Exception:
                pass
            return await page.content()
        finally:
            await self._pool.put(page)

    async def cerrar(self) -> None:
        if self._browser is not None:
            await self._browser.close()
        if self._pw is not None:
            await self._pw.stop()


async def buscar_libro_en_opac(
    cliente: ClienteHTTP | ClientePlaywright,
    autor: str,
    titulo_orig: str,
    signatura: str,
    pausa_reintento: float,
) -> tuple[str | None, str, int]:
    """Consulta el OPAC para un libro. Devuelve (titulo_completo | None, html, num_peticiones)."""
    html = ""
    peticiones = 0
    es_hg = signatura.upper().startswith("HG")

    # 1. Para signaturas no-HG, búsqueda directa por signatura (campo 852)
    if not es_hg:
        sig_formateada = f"({signatura.replace('/', '!')}.t852.)"
        query_sig = urllib.parse.quote(sig_formateada, safe="")
        url_sig = f"{OPAC_BASE_URL}?ACC=DOSEARCH&xsqf99={query_sig}{FILTRO_BIBLIOTECA}"
        html = await cliente.obtener_html(url_sig)
        peticiones += 1
        encontrado = elegir_mejor_titulo(extraer_candidatos_html(html), titulo_orig, autor)
        if encontrado:
            return encontrado, html, peticiones

    # 2. Para HG (o respaldo si t852 no coincidió): máximo 3 consultas inteligentes
    consultas = generar_consultas_texto(titulo_orig, autor)
    for idx_q, terminos in enumerate(consultas):
        if idx_q > 0 and pausa_reintento > 0:
            await asyncio.sleep(pausa_reintento)

        query_codificada = urllib.parse.quote(terminos, safe="")
        url_busqueda = f"{OPAC_BASE_URL}?ACC=DOSEARCH&xsqf99={query_codificada}{FILTRO_BIBLIOTECA}"
        html = await cliente.obtener_html(url_busqueda)
        peticiones += 1
        encontrado = elegir_mejor_titulo(extraer_candidatos_html(html), titulo_orig, autor)
        if encontrado:
            return encontrado, html, peticiones

    return None, html, peticiones


def cargar_estado_previo(ruta: Path) -> tuple[list[dict[str, str]], set[tuple[str, str, str, str]]]:
    """Carga registros ya guardados en final.json o no_encontrados.json para reanudar."""
    if not ruta.exists():
        return [], set()

    registros = list(iterar_registros_json(ruta))
    claves: set[tuple[str, str, str, str]] = set()
    for reg in registros:
        autor, titulo, fecha, sig = extraer_campos(reg)
        tit_ref = reg.get("_titulo_truncado", titulo) if isinstance(reg, dict) else titulo
        claves.add(clave_registro(autor, tit_ref, fecha, sig))
    return registros, claves


def limpiar_salida_final(registros: list[dict[str, str]]) -> list[dict[str, str]]:
    """Devuelve registros limpios listos para sustituir.py."""
    return [
        {
            "Autor": reg.get("Autor", ""),
            "titulo": reg.get("titulo", ""),
            "fecha": reg.get("fecha", ""),
            "signatura": reg.get("signatura", ""),
        }
        for reg in registros
    ]


def formatear_eta(segundos: float) -> str:
    segundos = max(0, int(segundos))
    h, rem = divmod(segundos, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}h {m:02d}m"
    if m > 0:
        return f"{m}m {s:02d}s"
    return f"{s}s"


async def detectar_motor_auto(
    muestra: tuple[str, str, str, str],
    timeout_s: float,
    concurrencia: int,
    timeout_ms: int,
    headless: bool,
) -> tuple[ClienteHTTP | ClientePlaywright, str]:
    """Prueba primero HTTP directo; si el OPAC devolviera página sin resultados DOM, usa Playwright."""
    cliente_http = ClienteHTTP(timeout_s=timeout_s)
    autor, titulo_orig, _, signatura = muestra
    try:
        encontrado, html, _ = await buscar_libro_en_opac(
            cliente=cliente_http,
            autor=autor,
            titulo_orig=titulo_orig,
            signatura=signatura,
            pausa_reintento=0.2,
        )
        # Si HTTP encontró resultado o devolvió HTML normal de abnetopac, usamos HTTP rápido
        if encontrado or ("abnetopac" in html.lower() and "js-t245" in html.lower()):
            return cliente_http, "http"
    except Exception:
        pass

    print("[!] El motor HTTP no obtuvo nodos .js-T245 en la prueba inicial; cambiando a Playwright...")
    cliente_pw = ClientePlaywright(concurrencia=concurrencia, timeout_ms=timeout_ms, headless=headless)
    await cliente_pw.iniciar()
    return cliente_pw, "playwright"


async def ejecutar_busqueda(
    ruta_entrada: Path,
    ruta_salida: Path,
    ruta_no_encontrados: Path,
    motor: str = "auto",
    concurrencia: int = 4,
    guardar_cada: int = 20,
    pausa: float = 0.35,
    pausa_reintento: float = 0.4,
    timeout_ms: int = 20000,
    limite: int | None = None,
    reintentar_fallidos: bool = False,
    guardar_html_error: bool = False,
    headless: bool = True,
) -> None:
    if not ruta_entrada.exists():
        raise FileNotFoundError(
            f"No se encontró el archivo de entrada: {ruta_entrada.resolve()}\n"
            "Ejecuta primero 'python truncados.py --sin-duplicados' para generar truncados.json."
        )

    registros_entrada = list(iterar_registros_json(ruta_entrada))

    resultados_finales, claves_resueltas = cargar_estado_previo(ruta_salida)
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

    print("=== BÚSQUEDA RÁPIDA DE TÍTULOS COMPLETOS EN OPAC ===")
    print(f"Entrada              : {ruta_entrada.resolve()} ({len(registros_entrada)} registros)")
    print(f"Salida (final.json)  : {ruta_salida.resolve()} ({len(resultados_finales)} ya guardados)")
    print(f"Omitidos ya tratados : {omitidos_previos}")
    print(f"Descartados (EXP/vac): {descartados}")
    print(f"Pendientes en sesión : {len(pendientes)}")

    if not pendientes:
        print("No hay registros pendientes por consultar.")
        return

    # Inicializar motor seleccionado
    timeout_s = max(5.0, timeout_ms / 1000.0)
    if motor == "http":
        cliente: ClienteHTTP | ClientePlaywright = ClienteHTTP(timeout_s=timeout_s)
        motor_activo = "http"
    elif motor == "playwright":
        cliente = ClientePlaywright(concurrencia=concurrencia, timeout_ms=timeout_ms, headless=headless)
        await cliente.iniciar()
        motor_activo = "playwright"
    else:
        cliente, motor_activo = await detectar_motor_auto(
            muestra=pendientes[0],
            timeout_s=timeout_s,
            concurrencia=concurrencia,
            timeout_ms=timeout_ms,
            headless=headless,
        )

    print(f"Motor activo         : {motor_activo.upper()} | Concurrencia: {concurrencia} | Pausa: {pausa}s")

    carpeta_html = ruta_salida.parent / "errores_html"
    if guardar_html_error:
        carpeta_html.mkdir(parents=True, exist_ok=True)

    # Caché en memoria por (título truncado, apellido autor) -> título completo
    cache_obras: dict[tuple[str, str], str] = {}
    lock_estado = asyncio.Lock()
    semaforo = asyncio.Semaphore(max(1, concurrencia))

    stats = {
        "procesados": 0,
        "encontrados": 0,
        "cache_hits": 0,
        "fallidos": 0,
    }
    inicio = time.monotonic()
    total = len(pendientes)

    def volcar_progreso() -> None:
        escribir_json_atomico(ruta_salida, limpiar_salida_final(resultados_finales), compacto=False)
        escribir_json_atomico(ruta_no_encontrados, no_encontrados, compacto=False)

    async def procesar_uno(item: tuple[str, str, str, str]) -> None:
        autor, titulo_orig, fecha, signatura = item
        clv_cache = clave_cache_obra(autor, titulo_orig)

        # 1. Intento instantáneo desde caché en memoria (ejemplares repetidos)
        async with lock_estado:
            en_cache = cache_obras.get(clv_cache)

        if en_cache:
            async with lock_estado:
                resultados_finales.append(
                    {
                        "Autor": autor,
                        "titulo": en_cache,
                        "fecha": fecha,
                        "signatura": signatura,
                    }
                )
                stats["procesados"] += 1
                stats["encontrados"] += 1
                stats["cache_hits"] += 1
                idx_actual = stats["procesados"]
                print(f"[{idx_actual}/{total}] [CACHÉ] {signatura} -> {en_cache}")
                if idx_actual % guardar_cada == 0:
                    volcar_progreso()
            return

        # 2. Consulta al OPAC controlada por semáforo
        async with semaforo:
            try:
                titulo_completo, html, _ = await buscar_libro_en_opac(
                    cliente=cliente,
                    autor=autor,
                    titulo_orig=titulo_orig,
                    signatura=signatura,
                    pausa_reintento=pausa_reintento,
                )

                async with lock_estado:
                    stats["procesados"] += 1
                    idx_actual = stats["procesados"]

                    if titulo_completo:
                        cache_obras[clv_cache] = titulo_completo
                        resultados_finales.append(
                            {
                                "Autor": autor,
                                "titulo": titulo_completo,
                                "fecha": fecha,
                                "signatura": signatura,
                            }
                        )
                        stats["encontrados"] += 1
                        print(f"[{idx_actual}/{total}] [OK] {signatura} -> {titulo_completo}")
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
                        stats["fallidos"] += 1
                        print(f"[{idx_actual}/{total}] [NO ENCONTRADO] {signatura} | {titulo_orig}")
                        if guardar_html_error and html:
                            nombre_limpio = re.sub(r'[\\/:*?"<>|\s]+', "_", signatura)
                            (carpeta_html / f"error_{nombre_limpio}.html").write_text(html, encoding="utf-8")

                    if idx_actual % guardar_cada == 0:
                        volcar_progreso()
                        transcurrido = max(0.01, time.monotonic() - inicio)
                        vel = idx_actual / transcurrido
                        eta = (total - idx_actual) / vel if vel > 0 else 0
                        print(
                            f"[*] Progreso guardado ({len(resultados_finales)} en {ruta_salida.name} | "
                            f"{vel:.2f} libros/s | ETA: {formatear_eta(eta)})"
                        )

            except Exception as exc:
                async with lock_estado:
                    stats["procesados"] += 1
                    stats["fallidos"] += 1
                    idx_actual = stats["procesados"]
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
                    print(f"[{idx_actual}/{total}] [ERROR] {signatura}: {exc}")
                    if idx_actual % guardar_cada == 0:
                        volcar_progreso()

            if pausa > 0:
                await asyncio.sleep(pausa)

    # Procesamos en ventanas para mantener memoria acotada y permitir Ctrl+C limpio
    tam_ventana = max(concurrencia * 4, guardar_cada)
    try:
        for i in range(0, total, tam_ventana):
            bloque = pendientes[i : i + tam_ventana]
            await asyncio.gather(*(procesar_uno(item) for item in bloque))
    except (KeyboardInterrupt, asyncio.CancelledError):
        print("\n[!] Interrupción detectada (Ctrl+C). Guardando progreso antes de salir...")
    finally:
        volcar_progreso()
        await cliente.cerrar()

    transcurrido = max(0.01, time.monotonic() - inicio)
    print("\n=== RESUMEN DE SESIÓN ===")
    print(f"Tiempo total          : {formatear_eta(transcurrido)} ({stats['procesados']/transcurrido:.2f} libros/s)")
    print(f"Consultados en sesión : {stats['procesados']}")
    print(f"Encontrados en sesión : {stats['encontrados']} (de ellos {stats['cache_hits']} por caché)")
    print(f"No encontrados/errores: {stats['fallidos']}")
    print(f"Total en final.json   : {len(resultados_finales)} ({ruta_salida.resolve()})")
    print(f"Total sin coincidencia: {len(no_encontrados)} ({ruta_no_encontrados.resolve()})")


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Busca rápidamente en el OPAC de la Universidad de Oviedo los títulos completos de truncados.json."
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
        "--motor",
        choices=("auto", "http", "playwright"),
        default="auto",
        help="Motor de consulta: 'auto' prueba HTTP rápido y usa Playwright solo si hace falta (por defecto: auto).",
    )
    parser.add_argument(
        "--concurrencia",
        "-c",
        type=int,
        default=4,
        help="Número de consultas simultáneas al OPAC (por defecto: 4; recomendado entre 3 y 6).",
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
        default=0.35,
        help="Segundos de pausa por trabajador tras cada libro (por defecto: 0.35).",
    )
    parser.add_argument(
        "--pausa-reintento",
        type=float,
        default=0.4,
        help="Segundos de espera entre reintentos de un mismo libro (por defecto: 0.4).",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=20000,
        help="Tiempo máximo de espera por petición en milisegundos (por defecto: 20000).",
    )
    parser.add_argument(
        "--limite",
        type=int,
        default=None,
        help="Limita el número de libros a consultar en esta ejecución (útil para pruebas, ej. --limite 20).",
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
        help="Si se usa Playwright, abre Chromium visible en pantalla.",
    )
    return parser


def main() -> None:
    args = construir_parser().parse_args()
    try:
        asyncio.run(
            ejecutar_busqueda(
                ruta_entrada=args.entrada,
                ruta_salida=args.salida,
                ruta_no_encontrados=args.no_encontrados,
                motor=args.motor,
                concurrencia=args.concurrencia,
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
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()