#!/usr/bin/env python3
"""Recupera los títulos completos de los registros truncados en `truncados.json`
y genera directamente `final.json` listo para `5_sustituir_titulos.html`.

Características principales:
  - Motor HTTP asíncrono directo con Keep-Alive y cookies (10x-20x más rápido
    que Chromium), con respaldo opcional a Playwright (`--motor auto|http|playwright`).
  - Concurrencia controlada con limitador de tasa y backoff exponencial por dominio.
  - Búsqueda en cascada multi-fuente:
      0. Resolución local contra `libros*.json` + caché persistente SQLite.
      1. OPAC Universidad de Oviedo (AbsysNet): t852 exacta, t852 base sin volumen,
         búsqueda estructurada por campos (xsqf02/xsqf03) con y sin `subcat=BH`.
      2. BNE (Biblioteca Nacional de España) vía SRU 1.2 (MARCXML oficial, CC0).
      3. Catálogos nacionales europeos vía SRU según idioma detectado:
         BnF (UNIMARC), DNB (MARC21), Library of Congress (MARCXML).
      4. Open Library Search API y Google Books API como respaldo final.
  - Limpieza automática tras `/` (mención de responsabilidad) preservando barras
    legítimas dentro del título (p. ej. `1936/1939`, `XVI/XVII`, `español/inglés`).
  - Emparejamiento robusto por prefijo compacto, autor normalizado y año (`fecha`),
    evitando falsos positivos en signaturas compartidas (`HR`, `HI`, `HG`, etc.).
  - Reanudación segura y escritura atómica: nunca modifica `truncados.json`.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import difflib
import glob
import http.cookiejar
import json
import os
import re
import signal
import sqlite3
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# --- CONFIGURACIÓN DE ENDPOINTS ---
OPAC_BASE_URL = "https://absysweb.cpd.uniovi.es/cgi-bin/abnetopac"
FILTRO_BIBLIOTECA = "&subcat=BH"

BNE_SRU_URL = "https://catalogo.bne.es/view/sru/34BNE_INST"
BNF_SRU_URL = "https://catalogue.bnf.fr/api/SRU"
DNB_SRU_URL = "https://services.dnb.de/sru/dnb"
LOC_SRU_URL = "https://lx2.loc.gov/sru/lcdb"
OPENLIBRARY_URL = "https://openlibrary.org/search.json"
GOOGLE_BOOKS_URL = "https://www.googleapis.com/books/v1/volumes"

FUENTES_DEFECTO = (
    "local",
    "opac",
    "bne",
    "bnf",
    "dnb",
    "loc",
    "openlibrary",
    "googlebooks",
)

HEADERS_HTTP = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xml,application/json;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.6,fr;q=0.4,de;q=0.4",
    "Connection": "keep-alive",
}

# Stopwords multilingües para construir consultas de palabras clave limpias
STOPWORDS = {
    # Español / Catalán / Gallego / Portugués
    "el", "la", "los", "las", "un", "una", "unos", "unas", "de", "del", "al",
    "en", "y", "e", "o", "u", "a", "con", "por", "para", "sin", "sobre",
    "entre", "hacia", "desde", "hasta", "segun", "ante", "bajo", "tras",
    "que", "como", "sus", "su", "es", "son", "se", "al", "lo", "le", "les",
    "els", "dels", "amb", "per", "pel", "pels", "dos", "das", "nos", "nas",
    "uma", "uns", "umas", "com", "em", "no", "na",
    # Inglés
    "the", "of", "and", "in", "to", "a", "an", "for", "on", "with", "at",
    "by", "from", "as", "into", "about", "through", "over", "between", "under",
    "its", "their", "his", "her", "this", "that", "these", "those",
    # Francés
    "du", "des", "au", "aux", "et", "ou", "dans", "sur", "par", "pour", "avec",
    "sans", "sous", "vers", "chez", "une", "qui", "que", "est", "sont", "ses",
    # Alemán
    "der", "die", "das", "den", "dem", "des", "ein", "eine", "einer", "eines",
    "einem", "einen", "und", "oder", "im", "in", "am", "an", "auf", "aus",
    "bei", "mit", "nach", "von", "vom", "zu", "zum", "zur", "uber", "fur",
    "durch", "als", "ist", "sind",
    # Italiano
    "il", "gli", "di", "da", "dal", "dalla", "dalle", "dallo", "degli",
    "dei", "delle", "dello", "nel", "nella", "nelle", "nello", "negli",
    "nei", "sul", "sulla", "sulle", "sullo", "tra", "fra", "per", "con",
}

ABREVIATURAS_PUNTO_FINAL = (
    "et al.", "s.a.", "s.l.", "s.f.", "s.n.", "ed.", "eds.", "vol.", "vols.",
    "t.", "n.", "no.", "num.", "p.", "pp.", "etc.", "dr.", "dra.", "sr.", "sra.",
    "st.", "mr.", "mrs.", "ms.", "u.s.", "u.s.a.", "d.c.", "a.c.", "b.c.",
    "a.d.", "j.c.", "ss.", "v.", "cf.", "op.", "cit.",
)

RE_VOLUMEN_SIG = re.compile(
    r"-(?:[IVXLCDM]{1,6}|\d{1,4}|[A-Z]|T\.\d+|V\.\d+|VOL\.?\d+)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Candidato:
    titulo: str
    autor: str = ""
    anio: str = ""
    fuente: str = ""


@dataclass(frozen=True)
class ResultadoBusqueda:
    titulo: str
    fuente: str
    confianza: float
    peticiones: int = 0


# =============================================================================
# 1. NORMALIZACIÓN, LIMPIEZA DE TÍTULOS Y DETECCIÓN DE IDIOMA
# =============================================================================

MAPA_CARACTERES_ESPECIALES = str.maketrans({
    "ß": "ss",
    "œ": "oe",
    "æ": "ae",
    "ø": "o",
    "đ": "d",
    "ð": "d",
    "þ": "th",
    "ł": "l",
    "ª": "a",
    "º": "o",
    "_": " ",
})


def a_ascii_seguro(texto: str) -> str:
    """Convierte texto a ASCII puro sin tildes ni caracteres especiales (`ß->ss`, `œ->oe`, `ø->o`, `_-> `)."""
    if not texto:
        return ""
    s = str(texto).lower().translate(MAPA_CARACTERES_ESPECIALES)
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^a-z0-9\s]", " ", s)
    return " ".join(s.split())


def normalizar_texto(texto: str) -> str:
    """Minúsculas, sin tildes/especiales, puntuación y `_` reemplazados por espacios."""
    return a_ascii_seguro(texto)


def normalizar_compacto(texto: str) -> str:
    """Alfanumérico puro sin tildes, puntuación ni espacios.
    Resuelve fusiones como `GijónXixón` vs `Gijón-Xixón`, `lEurope` vs `l'Europe` o `noche_` vs `noche...`.
    """
    if not texto:
        return ""
    return re.sub(r"[^a-z0-9]", "", a_ascii_seguro(texto))


def limpiar_titulo_salida(texto: str, quitar_barra: bool = True) -> str:
    """Limpia saltos de línea, mención de responsabilidad tras `/` y puntuación ISBD final.

    Preserva barras legítimas dentro del título como `1936/1939`, `siglos XVI/XVII`,
    `español/inglés` o `I/II` porque solo corta ante `/` precedida de espacio
    (` / Autor`, ` /Autor`) o `/` seguida de espacio + mayúscula/corchete fuera de dígitos.
    """
    if not texto:
        return ""

    s = " ".join(str(texto).replace("\r", " ").replace("\n", " ").split())

    if quitar_barra:
        # Caso 1 (99,9% en ISBD / AbsysNet): espacio + '/' (ej. ' / Autor' o ' /Autor')
        m = re.search(r"\s+/", s)
        if m:
            s = s[: m.start()]
        else:
            # Caso 2 (catalogación sin espacio previo): 'título/ Autor' o 'título/ [ed.]'
            m2 = re.search(r"(?<=[a-zA-ZÀ-ÿ\)\]])/\s+(?=[A-ZÀ-Ý\[])", s)
            if m2:
                s = s[: m2.start()]

    # Limpieza de espacios y puntuación ISBD colgante al final
    s = s.strip()
    s = re.sub(r"\s*[:;/,]+\s*$", "", s).strip()

    # Quitar punto final simple si no es puntos suspensivos ni abreviatura conocida
    if s.endswith(".") and not s.endswith("..."):
        lower_s = s.lower()
        if not any(lower_s.endswith(ab) for ab in ABREVIATURAS_PUNTO_FINAL):
            # También conservar siglas tipo 'S. XIX.' o 'U.S.A.' (letra + punto + letra + punto)
            if not re.search(r"(?:\b[A-Za-zÀ-ÿ]\.){2}$", s):
                s = s[:-1].rstrip()

    # Limpiar posible puntuación colgante que quedara antes del punto quitado
    s = re.sub(r"\s*[:;/,]+\s*$", "", s).strip()
    return " ".join(s.split())


def extraer_anio(texto: str) -> str:
    """Extrae el primer año válido de 4 dígitos (1400-2099) de un texto."""
    if not texto:
        return ""
    m = re.search(r"\b(1[4-9]\d{2}|20\d{2})\b", str(texto))
    return m.group(1) if m else ""


def limpiar_autor_busqueda(autor: str) -> str:
    """Elimina fechas (`1929-2017.`), paréntesis (`(España)`) y puntuación del autor."""
    if not autor:
        return ""
    s = str(autor)
    # Quitar paréntesis y su contenido (rompen sintaxis experta xsqf99 y CQL)
    s = re.sub(r"\([^)]*\)", " ", s)
    # Quitar fechas biográficas (1929-2017, 1983-, ca. 1450-1520, m. 1600)
    s = re.sub(r"\b(?:ca\.?|fl\.?|n\.?|m\.?)?\s*\d{3,4}\s*[-–]\s*\d{0,4}\b", " ", s, flags=re.IGNORECASE)
    s = re.sub(r"\b\d{4}\b", " ", s)
    # Quitar comillas y caracteres especiales
    s = re.sub(r'["\'<>()\[\]{}:;/\\]', " ", s)
    return " ".join(s.split())


def extraer_apellido_principal(autor: str) -> str:
    """Devuelve el apellido principal (o bloque inicial relevante) limpio y sin tildes."""
    limpio = limpiar_autor_busqueda(autor)
    if not limpio:
        return ""
    if "," in limpio:
        bloque = limpio.split(",", 1)[0]
    else:
        # Autor corporativo o nombre sin coma: tomar hasta 3 palabras relevantes
        bloque = limpio.split(".", 1)[0]
    tokens = [t for t in normalizar_texto(bloque).split() if len(t) >= 2 and not t.isdigit()]
    if not tokens:
        return ""
    if "," in limpio:
        particulas = {
            "de", "del", "la", "las", "los", "el", "di", "da", "dal", "della",
            "van", "von", "der", "den", "le", "du", "des", "mc", "mac", "san", "santa",
        }
        fin = 0
        while fin < len(tokens) - 1 and tokens[fin] in particulas:
            fin += 1
        return " ".join(tokens[: fin + 1])
    # Para entidades sin coma, devolver palabras significativas (máx. 3)
    sig = [t for t in tokens if t not in STOPWORDS]
    return " ".join((sig or tokens)[:3])


def separar_camel_y_limpiar_tokens(titulo: str) -> list[str]:
    """Divide fusiones CamelCase (`lEurope` -> `Europe`, `GijónXixón` -> `Gijón Xixón`),
    elimina `_` (usado por `...` en exportaciones) y devuelve tokens limpios para búsqueda.
    """
    if not titulo:
        return []
    # Quitar puntuación especial y guiones bajos preservando letras con tilde
    s = re.sub(r'["\'()\[\]{}:;/\\?¿!¡«»*_]', " ", titulo)
    tokens_salida: list[str] = []
    for raw in s.split():
        # Caso francés/italiano elidido sin apóstrofo: lEurope, dHistoire, dellArte, nellItalia
        m_elision = re.match(r"^(?:l|d|s|n|c|j|m|t|qu|dell|nell|sull|all|dall|un)([A-ZÀ-Ý][a-zà-ÿ]{2,}.*)$", raw)
        if m_elision:
            partes = re.sub(r"([a-zà-ÿ])([A-ZÀ-Ý])", r"\1 \2", m_elision.group(1)).split()
            tokens_salida.extend(partes)
            continue
        # División general CamelCase (GijónXixón, XVIeXVIIIe, indigenousAustralian)
        dividido = re.sub(r"([a-zà-ÿ])([A-ZÀ-Ý])", r"\1 \2", raw)
        for p in dividido.split():
            p_limpio = re.sub(r"[^\w]", "", p).replace("_", "")
            if p_limpio:
                tokens_salida.append(p_limpio)
    return tokens_salida


def recortar_palabras_truncadas(titulo: str) -> list[str]:
    """Devuelve los tokens del título descartando la última palabra si es probable
    que esté cortada por el límite de 60 caracteres.
    """
    raw_words = titulo.strip().split()
    if len(raw_words) >= 4:
        # En el 95,6% de los truncados (55-60 chars), la última palabra está cortada
        base_str = " ".join(raw_words[:-1])
    elif len(raw_words) >= 2 and len(raw_words[-1]) <= 2:
        base_str = " ".join(raw_words[:-1])
    else:
        base_str = titulo.strip()
    return separar_camel_y_limpiar_tokens(base_str)


def construir_consulta_palabras_clave_seguras(
    titulo_orig: str,
    minimo: int = 4,
    maximo: int = 5,
) -> str:
    """Construye una consulta de respaldo con al menos `minimo` (4) palabras clave seguras
    evitando caracteres especiales, tildes y palabras fusionadas que puedan hacer fallar
    el primer intento de búsqueda.

    Estrategia:
      1. Prioriza palabras clave puramente ASCII (`^[A-Za-z]{3,14}$`, sin stopwords ni acentos).
      2. Si hay < `minimo`, completa con el resto de palabras clave transliteradas a ASCII puro
         (sin tildes ni `ñ/ç/ä/ö/ü/ß/ø`), respetando su orden de aparición en el título.
    """
    tokens = recortar_palabras_truncadas(titulo_orig)
    if not tokens:
        tokens = separar_camel_y_limpiar_tokens(titulo_orig)

    # Clasificar cada token preservando su posición original
    puros_ascii: list[tuple[int, str]] = []
    con_especiales_ascii: list[tuple[int, str]] = []
    vistos: set[str] = set()

    for pos, tok in enumerate(tokens):
        tok_ascii = a_ascii_seguro(tok)
        if not tok_ascii or " " in tok_ascii or tok_ascii in vistos:
            continue
        if tok_ascii in STOPWORDS or len(tok_ascii) < 3 or tok_ascii.isdigit():
            continue
        # Evitar posibles palabras fusionadas sin guion (p. ej. `françaisespagnol`) salvo falta de opciones
        es_puro_ascii = bool(re.fullmatch(r"[A-Za-z]{3,14}", tok))
        vistos.add(tok_ascii)
        if es_puro_ascii:
            puros_ascii.append((pos, tok_ascii))
        elif len(tok_ascii) <= 14:
            con_especiales_ascii.append((pos, tok_ascii))

    # 1. Si ya tenemos >= minimo palabras clave 100% libres de caracteres especiales, usar solo esas
    if len(puros_ascii) >= minimo:
        seleccion = puros_ascii[:maximo]
    else:
        # 2. Completar hasta `minimo` (o `maximo`) con palabras clave transliteradas a ASCII puro
        faltan = maximo - len(puros_ascii)
        seleccion = puros_ascii + con_especiales_ascii[:faltan]
        # 3. Solo incluir palabras muy largas (>14 chars, posibles fusiones sin guion) si tenemos menos de 3 palabras
        if len(seleccion) < 3:
            ya_incluidas = {w for _, w in seleccion}
            for pos, tok in enumerate(tokens):
                tok_ascii = a_ascii_seguro(tok)
                if tok_ascii and " " not in tok_ascii and len(tok_ascii) >= 3 and tok_ascii not in ya_incluidas and tok_ascii not in STOPWORDS:
                    seleccion.append((pos, tok_ascii))
                    ya_incluidas.add(tok_ascii)
                    if len(seleccion) >= 3:
                        break
        seleccion.sort(key=lambda item: item[0])

    return " ".join(w for _, w in seleccion[:maximo])


def generar_frases_consulta(titulo_orig: str) -> list[str]:
    """Genera consultas progresivas:
      - Intento 1: primeras ~6 palabras limpias del título original (sin la palabra final cortada).
      - Intento 2: >=4 palabras clave seguras sin caracteres especiales ni tildes (inmune a
        problemas de codificación/diacríticos/fusiones).
      - Intento 3 (opcional): palabras clave reducidas si fueran distintas.
    """
    tokens = recortar_palabras_truncadas(titulo_orig)
    if not tokens:
        tokens = separar_camel_y_limpiar_tokens(titulo_orig)

    tokens_norm = [t for t in tokens if len(t) >= 2]
    candidatas: list[str] = []

    # Consulta 1: primeras 6 palabras limpias (sin la palabra final cortada ni fusiones >18 chars)
    tokens_q1 = [t for t in tokens_norm if len(t) <= 18]
    if len(tokens_q1) < 3:
        tokens_q1 = tokens_norm
    if tokens_q1:
        candidatas.append(" ".join(tokens_q1[:6]))

    # Consulta 2: >=4 palabras clave seguras sin caracteres especiales/diacríticos ni fusiones
    q_segura = construir_consulta_palabras_clave_seguras(titulo_orig, minimo=4, maximo=5)
    if q_segura:
        candidatas.append(q_segura)

    # Consulta 3: exactamente 4 palabras clave seguras si la 2 tenía 5 (o 3 si el título es muy corto)
    if q_segura and len(q_segura.split()) >= 5:
        candidatas.append(" ".join(q_segura.split()[:4]))

    # Deduplicar preservando orden (distinguiendo si una lleva tildes/especiales y la otra es ASCII puro)
    unicas: list[str] = []
    vistas: set[str] = set()
    for q in candidatas:
        clave_q = " ".join(q.lower().split())
        if clave_q and clave_q not in vistas:
            vistas.add(clave_q)
            unicas.append(q)
    return unicas[:3]


def detectar_idioma(titulo: str) -> str:
    """Detecta rápidamente el idioma probable del título (`es`, `fr`, `de`, `en`, `it`)."""
    t_lower = titulo.lower()
    palabras = set(re.findall(r"[a-zà-ÿß]+", t_lower))

    scores = {"es": 0, "fr": 0, "de": 0, "en": 0, "it": 0}

    # Caracteres altamente discriminantes
    if "ñ" in t_lower or "¿" in t_lower or "¡" in t_lower:
        scores["es"] += 5
    if "ß" in t_lower or any(c in t_lower for c in ("ä", "ö", "ü")):
        scores["de"] += 5
    if any(c in t_lower for c in ("œ", "ê", "è", "ç", "à")):
        scores["fr"] += 3
    if any(c in t_lower for c in ("é", "í", "ó", "ú", "á")):
        scores["es"] += 1

    marcadores = {
        "es": {"el", "los", "las", "del", "españa", "española", "español", "historia", "estudios", "diccionario", "sobre", "siglo", "siglos", "entre", "actas", "catálogo", "manual", "teoría", "política", "literatura", "dels", "amb"},
        "fr": {"le", "les", "des", "du", "dans", "sur", "avec", "pour", "aux", "une", "histoire", "études", "dictionnaire", "français", "française", "siècle", "siècles", "essai", "entre"},
        "de": {"der", "die", "das", "und", "von", "zur", "zum", "für", "über", "aus", "dem", "den", "des", "geschichte", "studien", "wörterbuch", "untersuchungen", "paulys", "französisches", "im", "mit"},
        "en": {"the", "of", "and", "in", "for", "with", "from", "studies", "history", "dictionary", "essays", "introduction", "theory", "language", "literature", "art", "medieval", "modern", "between"},
        "it": {"il", "lo", "gli", "della", "dello", "delle", "degli", "nel", "nella", "nelle", "sul", "sulla", "storia", "studi", "dizionario", "italiana", "italiano", "tra", "fra", "secolo"},
    }
    for lang, vocab in marcadores.items():
        scores[lang] += 2 * len(palabras & vocab)

    # Primer artículo del título pesa mucho
    primera = t_lower.split()[0] if t_lower.split() else ""
    if primera in {"the", "a", "an"}:
        scores["en"] += 4
    elif primera in {"le", "les", "du", "des", "un", "une"}:
        scores["fr"] += 3
    elif primera in {"der", "die", "das", "ein", "eine"}:
        scores["de"] += 4
    elif primera in {"il", "lo", "gli", "i"}:
        scores["it"] += 4
    elif primera in {"el", "los", "las", "la", "una"}:
        scores["es"] += 3

    mejor_lang, mejor_score = max(scores.items(), key=lambda kv: kv[1])
    return mejor_lang if mejor_score > 0 else "es"


def normalizar_signatura_base(signatura: str) -> str:
    """Devuelve una versión base de la signatura sin notas ni sufijos de volumen."""
    sig = signatura.strip()
    # Quitar notas tras espacio/punto (ej. 'LPP-1239. Ubicadas en el cuarto...')
    if " " in sig:
        sig = sig.split()[0].rstrip(".")
    # Quitar sufijo de volumen (-I, -II, -VIII, -311) si tiene al menos 2 guiones
    if sig.count("-") >= 2:
        sin_vol = RE_VOLUMEN_SIG.sub("", sig)
        if sin_vol and sin_vol != sig:
            return sin_vol
    return sig


# =============================================================================
# 2. MOTOR DE EMPAREJAMIENTO Y PUNTUACIÓN (SCORING)
# =============================================================================

def coincide_autor(autor_json: str, autor_web: str) -> tuple[bool, float]:
    """Comprueba coincidencia flexible de autor. Devuelve (coincide, puntuacion 0..1)."""
    if not autor_json.strip():
        return True, 0.5
    if not autor_web.strip():
        return True, 0.3

    a_json = normalizar_texto(limpiar_autor_busqueda(autor_json))
    a_web = normalizar_texto(limpiar_autor_busqueda(autor_web))
    if not a_json or not a_web:
        return True, 0.4

    if a_json == a_web:
        return True, 1.0
    if a_json in a_web or a_web in a_json:
        return True, 0.95

    ap_json = extraer_apellido_principal(autor_json)
    ap_web = extraer_apellido_principal(autor_web)
    if ap_json and (ap_json in a_web or (ap_web and ap_json == ap_web)):
        return True, 0.9

    # Tokens significativos (longitud >= 3, sin stopwords)
    toks_json = {t for t in a_json.split() if len(t) >= 3 and t not in STOPWORDS}
    toks_web = {t for t in a_web.split() if len(t) >= 3 and t not in STOPWORDS}
    if toks_json and toks_web:
        comunes = toks_json & toks_web
        if comunes:
            ratio = len(comunes) / min(len(toks_json), len(toks_web))
            if ratio >= 0.5:
                return True, 0.75 + 0.2 * ratio
            # Si coincide un apellido largo distintivo (>= 5 chars)
            if any(len(t) >= 5 for t in comunes):
                return True, 0.75

    return False, 0.0


def evaluar_candidato(
    titulo_orig: str,
    autor_json: str,
    fecha_json: str,
    candidato: Candidato,
    modo: str = "opac_texto",
    quitar_barra: bool = True,
) -> tuple[float, str] | None:
    """Evalúa si un candidato es una versión completa y fiable del título truncado.

    `modo`:
      - `opac_signatura`: resultado devuelto por signatura exacta t852 en Uniovi.
      - `opac_texto`: resultado devuelto por búsqueda textual en Uniovi.
      - `externo`: resultado de fuente externa (BNE, BnF, DNB, LoC, OpenLibrary, GoogleBooks).

    Devuelve `(score, titulo_limpio)` o `None` si no pasa las validaciones.
    """
    titulo_limpio = limpiar_titulo_salida(candidato.titulo, quitar_barra=quitar_barra)
    if not titulo_limpio:
        return None

    # No aceptar títulos más cortos que el actual (exigido por 5_sustituir_titulos.html)
    if len(titulo_limpio) < len(titulo_orig.strip()):
        return None

    trunc_c = normalizar_compacto(titulo_orig)
    cand_c = normalizar_compacto(titulo_limpio)
    if not trunc_c or not cand_c or len(cand_c) < len(trunc_c):
        return None

    # Si tras limpiar es exactamente igual al original sin aportar nada nuevo, descartar
    if titulo_limpio.strip() == titulo_orig.strip():
        return None

    # 1. Evaluación del prefijo de título (compacto, inmune a guiones/apóstrofos/puntuación)
    score_titulo = 0.0
    if cand_c.startswith(trunc_c):
        score_titulo = 100.0
    elif len(trunc_c) >= 24 and cand_c.startswith(trunc_c[:-2]):
        # Solo tolera los últimos 1-2 caracteres (posible corte de carácter especial o letra final)
        score_titulo = 93.0
    else:
        # Tolerancia a pequeñas variantes internas (p. ej. `Ma` vs `María` o artículo inicial),
        # exigiendo SIEMPRE que la cola del truncado (últimos 5-6 chars) también aparezca en la zona esperada
        # para no confundir obras con igual comienzo y distinto final (ej. `...en el Gabi` vs `...en el Depa`).
        cola_clave = trunc_c[-6:] if len(trunc_c) >= 12 else trunc_c[-4:]
        ventana_cola = cand_c[max(0, len(trunc_c) - 14) : len(trunc_c) + 14]
        tramo_cand = cand_c[: len(trunc_c) + 4]
        ratio_prefijo = difflib.SequenceMatcher(None, trunc_c, tramo_cand[: len(trunc_c)]).ratio()

        cola_presente = cola_clave in ventana_cola or (
            len(cola_clave) >= 5 and cola_clave[:-1] in ventana_cola
        )
        if cola_presente:
            if modo == "externo":
                if ratio_prefijo >= 0.92 and cand_c.startswith(trunc_c[:18]):
                    score_titulo = 88.0 * ratio_prefijo
            else:
                if ratio_prefijo >= 0.87 and (
                    cand_c.startswith(trunc_c[:14]) or trunc_c[:18] in cand_c[:26]
                ):
                    score_titulo = 88.0 * ratio_prefijo

    if score_titulo <= 0.0:
        return None

    # 2. Validación de autor
    autor_ok, score_autor = coincide_autor(autor_json, candidato.autor)
    if not autor_ok:
        # Si es externo y tenemos autor personal, nunca aceptar otro autor
        if modo == "externo":
            return None
        # En OPAC tampoco aceptar autor distinto salvo que el prefijo compacto sea idéntico en >=45 chars
        if not (modo == "opac_signatura" and score_titulo >= 94.0 and len(trunc_c) >= 45):
            return None

    # En fuentes externas, si el registro tiene autor personal pero la fuente no devolvió autor,
    # exigir coincidencia casi perfecta del título
    if modo == "externo" and autor_json.strip() and not candidato.autor.strip():
        if score_titulo < 94.0:
            return None

    # 3. Validación de año (fecha)
    anio_orig = extraer_anio(fecha_json)
    anio_cand = extraer_anio(candidato.anio)
    bonus_anio = 0.0
    if anio_orig and anio_cand:
        diff = abs(int(anio_orig) - int(anio_cand))
        if diff == 0:
            bonus_anio = 20.0
        elif diff <= 1:
            bonus_anio = 14.0
        elif diff <= 3:
            bonus_anio = 6.0
        elif diff > 15 and modo == "externo":
            # En fuentes externas, si la edición difiere >15 años, exigir prefijo muy exacto + autor
            if score_titulo < 94.0 or score_autor < 0.75:
                return None
            bonus_anio = -8.0

    # Bonus por completar efectivamente caracteres adicionales (preferir título completo frente a igual)
    extra_chars = len(cand_c) - len(trunc_c)
    bonus_completo = 8.0 if extra_chars >= 3 else (3.0 if len(titulo_limpio) > len(titulo_orig.strip()) else 0.0)

    # Penalizar títulos sospechosamente enormes (> 380 chars, posible concatenación de notas/índice)
    if len(titulo_limpio) > 380:
        bonus_completo -= 15.0

    score_total = score_titulo + (score_autor * 15.0) + bonus_anio + bonus_completo
    umbral_minimo = 102.0 if modo == "externo" else 88.0
    if score_total < umbral_minimo:
        return None

    return score_total, titulo_limpio


def elegir_mejor_candidato(
    candidatos: list[Candidato],
    titulo_orig: str,
    autor_json: str,
    fecha_json: str,
    modo: str,
    quitar_barra: bool = True,
) -> tuple[str, float] | None:
    """Devuelve `(mejor_titulo_limpio, score)` o `None` si ningún candidato es válido."""
    mejor: tuple[float, str] | None = None
    for cand in candidatos:
        res = evaluar_candidato(
            titulo_orig=titulo_orig,
            autor_json=autor_json,
            fecha_json=fecha_json,
            candidato=cand,
            modo=modo,
            quitar_barra=quitar_barra,
        )
        if res is not None:
            if mejor is None or res[0] > mejor[0]:
                mejor = res
    if mejor is None:
        return None
    return mejor[1], round(mejor[0], 2)


# =============================================================================
# 3. PARSERS DE RESPUESTAS (ABSYSNET HTML, MARCXML, UNIMARCXCHANGE, JSON)
# =============================================================================

def _extraer_candidatos_opac_stdlib(html: str) -> list[Candidato]:
    """Parser HTML ligero en biblioteca estándar (respaldo si falta BeautifulSoup)."""
    from html.parser import HTMLParser

    candidatos: list[Candidato] = []

    class _P(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.ultimo_autor = ""
            self.ultimo_anio = ""
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
            elif "js-T245" in tokens:
                self.capturando = "titulo"
            elif "js-T260" in tokens or "js-T264" in tokens or "js-date" in tokens:
                self.capturando = "anio"
            else:
                return
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
                elif self.capturando == "anio":
                    self.ultimo_anio = extraer_anio(texto)
                    if candidatos and not candidatos[-1].anio:
                        prev = candidatos[-1]
                        candidatos[-1] = Candidato(prev.titulo, prev.autor, self.ultimo_anio, "opac")
                elif self.capturando == "titulo" and texto:
                    candidatos.append(Candidato(texto, self.ultimo_autor, self.ultimo_anio, "opac"))
                self.capturando = None
                self.buffer = []

    p = _P()
    p.feed(html)
    return candidatos


def extraer_candidatos_opac(html: str) -> list[Candidato]:
    """Extrae candidatos `(titulo, autor, anio)` del HTML del OPAC AbsysNet."""
    if not html or "js-T245" not in html:
        return []
    try:
        from bs4 import BeautifulSoup  # type: ignore

        soup = BeautifulSoup(html, "html.parser")
        candidatos: list[Candidato] = []
        for el_tit in soup.select(".js-T245"):
            texto_tit = " ".join(el_tit.get_text(" ", strip=True).split())
            if not texto_tit:
                continue
            autor = ""
            anio = ""
            nodo = el_tit.parent
            for _ in range(4):
                if nodo is None:
                    break
                if not autor:
                    el_aut = nodo.select_one(".js-T1XX")
                    if el_aut:
                        autor = " ".join(el_aut.get_text(" ", strip=True).split())
                if not anio:
                    el_fecha = nodo.select_one(".js-T260, .js-T264, .js-date")
                    if el_fecha:
                        anio = extraer_anio(el_fecha.get_text(" ", strip=True))
                if autor and anio:
                    break
                # No subir por encima del contenedor de lista si tiene múltiples .js-T245
                if nodo.parent is not None and len(nodo.parent.select(".js-T245")) > 1:
                    break
                nodo = nodo.parent
            candidatos.append(Candidato(titulo=texto_tit, autor=autor, anio=anio, fuente="opac"))
        return candidatos
    except ImportError:
        return _extraer_candidatos_opac_stdlib(html)


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def extraer_candidatos_marcxml(xml_text: str, fuente: str) -> list[Candidato]:
    """Extrae candidatos de SRU MARC21-XML (BNE, DNB, Library of Congress)."""
    if not xml_text:
        return []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    candidatos: list[Candidato] = []
    # Cada <record> MARC dentro de recordData
    for rec in root.iter():
        if _local_name(rec.tag) != "record":
            continue
        # Distinguir <srw:record> exterior del <marc:record> interior (el interior tiene datafield)
        datafields = [ch for ch in rec if _local_name(ch.tag) == "datafield"]
        if not datafields:
            continue

        tit_partes: list[str] = []
        autores: list[str] = []
        anio = ""

        for cf in rec:
            if _local_name(cf.tag) == "controlfield" and cf.attrib.get("tag") == "008":
                txt = cf.text or ""
                if len(txt) >= 11:
                    anio = extraer_anio(txt[7:11])

        for df in datafields:
            tag = df.attrib.get("tag", "")
            subcampos = [
                (sf.attrib.get("code", ""), (sf.text or "").strip())
                for sf in df
                if _local_name(sf.tag) == "subfield" and (sf.text or "").strip()
            ]
            if tag == "245":
                # Solo a, b, n, p -> excluye $c (mención de responsabilidad) de raíz
                for code, val in subcampos:
                    if code in {"a", "b", "n", "p"}:
                        tit_partes.append(val)
            elif tag in {"100", "110", "111", "700", "710"}:
                txt_a = " ".join(v for c, v in subcampos if c in {"a", "b"})
                if txt_a:
                    autores.append(txt_a)
            elif tag in {"260", "264"} and not anio:
                for code, val in subcampos:
                    if code == "c":
                        a = extraer_anio(val)
                        if a:
                            anio = a
                            break

        if tit_partes:
            titulo = " ".join(tit_partes)
            candidatos.append(
                Candidato(
                    titulo=titulo,
                    autor=" ; ".join(autores[:2]),
                    anio=anio,
                    fuente=fuente,
                )
            )
    return candidatos


def extraer_candidatos_unimarcxml(xml_text: str, fuente: str = "bnf") -> list[Candidato]:
    """Extrae candidatos de SRU UNIMARCXchange (BnF)."""
    if not xml_text:
        return []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    candidatos: list[Candidato] = []
    for rec in root.iter():
        if _local_name(rec.tag) != "record":
            continue
        datafields = [ch for ch in rec if _local_name(ch.tag) == "datafield"]
        if not datafields:
            continue

        tit_a = ""
        tit_e: list[str] = []
        tit_hi: list[str] = []
        autores: list[str] = []
        anio = ""

        for df in datafields:
            tag = df.attrib.get("tag", "")
            subcampos = [
                (sf.attrib.get("code", ""), (sf.text or "").strip())
                for sf in df
                if _local_name(sf.tag) == "subfield" and (sf.text or "").strip()
            ]
            if tag == "200":
                # UNIMARC 200: $a título, $e subtítulo, $h/$i parte; omite $f/$g (responsabilidad)
                for code, val in subcampos:
                    if code == "a" and not tit_a:
                        tit_a = val
                    elif code == "e":
                        tit_e.append(val)
                    elif code in {"h", "i"}:
                        tit_hi.append(val)
            elif tag in {"700", "701", "702", "710", "711"}:
                partes_aut = [v for c, v in subcampos if c in {"a", "b"}]
                if partes_aut:
                    autores.append(", ".join(partes_aut))
            elif tag in {"210", "214"} and not anio:
                for code, val in subcampos:
                    if code == "d":
                        a = extraer_anio(val)
                        if a:
                            anio = a
                            break
            elif tag == "100" and not anio:
                for code, val in subcampos:
                    if code == "a" and len(val) >= 13:
                        anio = extraer_anio(val[9:13])

        if tit_a:
            partes = [tit_a]
            if tit_e:
                partes.append(": " + " : ".join(tit_e))
            if tit_hi:
                partes.append(". " + " ".join(tit_hi))
            candidatos.append(
                Candidato(
                    titulo=" ".join(partes),
                    autor=" ; ".join(autores[:2]),
                    anio=anio,
                    fuente=fuente,
                )
            )
    return candidatos


def extraer_candidatos_openlibrary(json_text: str) -> list[Candidato]:
    """Extrae candidatos del JSON de Open Library Search API."""
    if not json_text:
        return []
    try:
        data = json.loads(json_text)
    except json.JSONDecodeError:
        return []
    candidatos: list[Candidato] = []
    for doc in data.get("docs", [])[:10]:
        tit = (doc.get("title") or "").strip()
        sub = (doc.get("subtitle") or "").strip()
        if not tit:
            continue
        if sub and normalizar_compacto(sub) not in normalizar_compacto(tit):
            tit_completo = f"{tit} : {sub}"
        else:
            tit_completo = tit
        autores = doc.get("author_name") or []
        autor = " ; ".join(str(a) for a in autores[:2])
        anio = str(doc.get("first_publish_year") or "")
        candidatos.append(Candidato(titulo=tit_completo, autor=autor, anio=anio, fuente="openlibrary"))
    return candidatos


def extraer_candidatos_googlebooks(json_text: str) -> list[Candidato]:
    """Extrae candidatos del JSON de Google Books API."""
    if not json_text:
        return []
    try:
        data = json.loads(json_text)
    except json.JSONDecodeError:
        return []
    candidatos: list[Candidato] = []
    for item in data.get("items", [])[:10]:
        info = item.get("volumeInfo") or {}
        tit = (info.get("title") or "").strip()
        sub = (info.get("subtitle") or "").strip()
        if not tit:
            continue
        if sub and normalizar_compacto(sub) not in normalizar_compacto(tit):
            tit_completo = f"{tit} : {sub}"
        else:
            tit_completo = tit
        autores = info.get("authors") or []
        autor = " ; ".join(str(a) for a in autores[:2])
        anio = extraer_anio(str(info.get("publishedDate") or ""))
        candidatos.append(Candidato(titulo=tit_completo, autor=autor, anio=anio, fuente="googlebooks"))
    return candidatos


# =============================================================================
# 4. CLIENTE HTTP ASÍNCRONO CON LIMITADOR POR DOMINIO Y CIRCUIT BREAKER
# =============================================================================

class LimitadorDominio:
    """Controla concurrencia, intervalo mínimo y circuit breaker por host."""

    def __init__(self, max_concurrencia: int, intervalo_min_s: float) -> None:
        self.sem = asyncio.Semaphore(max(1, max_concurrencia))
        self.intervalo_min_s = max(0.0, intervalo_min_s)
        self._lock = asyncio.Lock()
        self._ultimo_ts = 0.0
        self._errores_consecutivos = 0
        self._pausado_hasta = 0.0

    def esta_pausado(self) -> bool:
        return time.monotonic() < self._pausado_hasta

    def registrar_exito(self) -> None:
        self._errores_consecutivos = 0

    def registrar_fallo(self, es_rate_limit: bool = False) -> None:
        self._errores_consecutivos += 1
        if es_rate_limit:
            self._pausado_hasta = time.monotonic() + min(60.0, 5.0 * self._errores_consecutivos)
        elif self._errores_consecutivos >= 6:
            # Circuit breaker temporal de 45s si un servicio externo cae
            self._pausado_hasta = time.monotonic() + 45.0
            self._errores_consecutivos = 0

    async def adquirir(self) -> None:
        await self.sem.acquire()
        if self.intervalo_min_s > 0:
            async with self._lock:
                ahora = time.monotonic()
                espera = self._ultimo_ts + self.intervalo_min_s - ahora
                if espera > 0:
                    await asyncio.sleep(espera)
                self._ultimo_ts = time.monotonic()

    def liberar(self) -> None:
        self.sem.release()


class ClienteHTTP:
    """Cliente HTTP rápido con Keep-Alive automático (`httpx` -> `requests` -> `urllib`)."""

    def __init__(self, timeout_s: float = 18.0, pausa_opac: float = 0.25) -> None:
        self.timeout_s = timeout_s
        self._httpx_client: Any = None
        self._requests_mod: Any = None
        self._local = threading.local()
        self.backend = "urllib"

        try:
            import httpx  # type: ignore

            self._httpx_mod = httpx
            self.backend = "httpx"
        except ImportError:
            self._httpx_mod = None
            try:
                import requests  # type: ignore

                self._requests_mod = requests
                self.backend = "requests"
            except ImportError:
                self._requests_mod = None
                self.backend = "urllib"

        self.limitadores: dict[str, LimitadorDominio] = {
            "absysweb.cpd.uniovi.es": LimitadorDominio(max_concurrencia=4, intervalo_min_s=pausa_opac),
            "catalogo.bne.es": LimitadorDominio(max_concurrencia=3, intervalo_min_s=0.25),
            "catalogue.bnf.fr": LimitadorDominio(max_concurrencia=3, intervalo_min_s=0.30),
            "services.dnb.de": LimitadorDominio(max_concurrencia=3, intervalo_min_s=0.30),
            "lx2.loc.gov": LimitadorDominio(max_concurrencia=2, intervalo_min_s=0.35),
            "openlibrary.org": LimitadorDominio(max_concurrencia=4, intervalo_min_s=0.20),
            "www.googleapis.com": LimitadorDominio(max_concurrencia=3, intervalo_min_s=0.30),
        }

    async def iniciar(self) -> None:
        if self._httpx_mod is not None:
            limits = self._httpx_mod.Limits(max_keepalive_connections=20, max_connections=30)
            self._httpx_client = self._httpx_mod.AsyncClient(
                headers=HEADERS_HTTP,
                timeout=self.timeout_s,
                follow_redirects=True,
                limits=limits,
            )

    def _get_limitador(self, url: str) -> LimitadorDominio:
        host = urllib.parse.urlparse(url).hostname or ""
        if host not in self.limitadores:
            self.limitadores[host] = LimitadorDominio(max_concurrencia=3, intervalo_min_s=0.25)
        return self.limitadores[host]

    def _fetch_sync(self, url: str) -> tuple[int, str]:
        if self._requests_mod is not None:
            sess = getattr(self._local, "session", None)
            if sess is None:
                sess = self._requests_mod.Session()
                sess.headers.update(HEADERS_HTTP)
                self._local.session = sess
            resp = sess.get(url, timeout=self.timeout_s)
            if resp.status_code == 200:
                if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
                    resp.encoding = resp.apparent_encoding or "utf-8"
                return 200, resp.text
            return resp.status_code, ""

        opener = getattr(self._local, "opener", None)
        if opener is None:
            jar = http.cookiejar.CookieJar()
            opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
            self._local.opener = opener
        req = urllib.request.Request(url, headers=HEADERS_HTTP)
        try:
            with opener.open(req, timeout=self.timeout_s) as resp:
                raw = resp.read()
                charset = resp.headers.get_content_charset() or "utf-8"
                try:
                    return resp.status, raw.decode(charset, errors="replace")
                except LookupError:
                    return resp.status, raw.decode("utf-8", errors="replace")
        except urllib.error.HTTPError as he:
            return he.code, ""

    async def obtener(self, url: str, reintentos: int = 2) -> str:
        lim = self._get_limitador(url)
        if lim.esta_pausado():
            return ""

        for intento in range(reintentos + 1):
            await lim.adquirir()
            try:
                if self._httpx_client is not None:
                    resp = await self._httpx_client.get(url)
                    status = resp.status_code
                    texto = resp.text if status == 200 else ""
                else:
                    status, texto = await asyncio.to_thread(self._fetch_sync, url)

                if status == 200:
                    lim.registrar_exito()
                    return texto
                if status in (429, 503):
                    lim.registrar_fallo(es_rate_limit=True)
                    if intento < reintentos:
                        await asyncio.sleep(1.5 * (intento + 1))
                        continue
                    return ""
                if status >= 500:
                    lim.registrar_fallo()
                    if intento < reintentos:
                        await asyncio.sleep(1.0 * (intento + 1))
                        continue
                    return ""
                return ""
            except Exception:
                lim.registrar_fallo()
                if intento < reintentos:
                    await asyncio.sleep(0.8 * (intento + 1))
                    continue
                return ""
            finally:
                lim.liberar()
        return ""

    async def cerrar(self) -> None:
        if self._httpx_client is not None:
            await self._httpx_client.aclose()


class ClientePlaywright:
    """Respaldo opcional con pool de pestañas Playwright bloqueando recursos pesados."""

    def __init__(self, concurrencia: int = 3, timeout_ms: int = 20000, headless: bool = True) -> None:
        self.concurrencia = max(1, concurrencia)
        self.timeout_ms = timeout_ms
        self.headless = headless
        self._pw: Any = None
        self._browser: Any = None
        self._context: Any = None
        self._pool: asyncio.Queue[Any] = asyncio.Queue()

    async def iniciar(self) -> None:
        from playwright.async_api import async_playwright  # type: ignore

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

    async def obtener(self, url: str) -> str:
        page = await self._pool.get()
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=self.timeout_ms)
            try:
                await page.wait_for_selector(".js-T245", timeout=2500)
            except Exception:
                pass
            return await page.content()
        except Exception:
            return ""
        finally:
            await self._pool.put(page)

    async def cerrar(self) -> None:
        if self._browser is not None:
            await self._browser.close()
        if self._pw is not None:
            await self._pw.stop()


# =============================================================================
# 5. CACHÉ PERSISTENTE SQLITE E ÍNDICE LOCAL (`libros*.json`)
# =============================================================================

class CacheSQLite:
    """Caché persistente en disco (modo WAL) por obra `(titulo_compacto, apellido, fecha)`."""

    def __init__(self, ruta: Path) -> None:
        self.ruta = ruta
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(ruta), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL;")
        self._conn.execute("PRAGMA synchronous=NORMAL;")
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS cache_obras (
                clave TEXT PRIMARY KEY,
                titulo_completo TEXT,
                fuente TEXT,
                confianza REAL,
                actualizado REAL
            )
            """
        )
        self._conn.commit()

    @staticmethod
    def construir_clave(titulo_orig: str, autor: str, fecha: str) -> str:
        return f"{normalizar_compacto(titulo_orig)}|{extraer_apellido_principal(autor)}|{extraer_anio(fecha)}"

    def obtener(self, clave: str) -> ResultadoBusqueda | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT titulo_completo, fuente, confianza FROM cache_obras WHERE clave = ?",
                (clave,),
            ).fetchone()
        if row is None:
            return None
        return ResultadoBusqueda(titulo=row[0] or "", fuente=row[1] or "", confianza=float(row[2] or 0.0))

    def guardar(self, clave: str, titulo_completo: str, fuente: str, confianza: float) -> None:
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO cache_obras (clave, titulo_completo, fuente, confianza, actualizado)
                VALUES (?, ?, ?, ?, ?)
                """,
                (clave, titulo_completo, fuente, confianza, time.time()),
            )
            self._conn.commit()

    def cerrar(self) -> None:
        with self._lock:
            self._conn.close()


def construir_indice_local(carpeta: Path, quitar_barra: bool = True) -> dict[tuple[str, str], list[Candidato]]:
    """Indexa títulos ya completos (>62 chars) presentes en `libros*.json` locales."""
    indice: dict[tuple[str, str], list[Candidato]] = {}
    archivos = sorted(carpeta.glob("libros*.json"))
    for ruta in archivos:
        try:
            with ruta.open("r", encoding="utf-8") as f:
                datos = json.load(f)
        except Exception:
            continue
        if not isinstance(datos, list):
            continue
        for reg in datos:
            if isinstance(reg, dict):
                tit = str(reg.get("titulo") or reg.get("Titulo") or "").strip()
                aut = str(reg.get("Autor") or reg.get("autor") or "").strip()
                fec = str(reg.get("fecha") or "").strip()
            elif isinstance(reg, list) and len(reg) >= 4:
                aut, tit, fec = str(reg[0] or ""), str(reg[1] or ""), str(reg[2] or "")
            else:
                continue
            if len(tit) <= 62:
                continue
            tit_limpio = limpiar_titulo_salida(tit, quitar_barra=quitar_barra)
            tc = normalizar_compacto(tit_limpio)
            if len(tc) < 30:
                continue
            clave = (tc[:28], extraer_apellido_principal(aut))
            indice.setdefault(clave, []).append(
                Candidato(titulo=tit_limpio, autor=aut, anio=fec, fuente="local")
            )
    return indice


# =============================================================================
# 6. BUSCADORES POR FUENTE (OPAC UNIOVI + BNE + BNF + DNB + LOC + OL + GBOOKS)
# =============================================================================

async def buscar_en_opac(
    cliente_http: ClienteHTTP,
    cliente_pw: ClientePlaywright | None,
    signatura: str,
    titulo_orig: str,
    autor: str,
    fecha: str,
    quitar_barra: bool,
) -> tuple[ResultadoBusqueda | None, str, int]:
    """Búsqueda en escalera en el OPAC de la Universidad de Oviedo."""
    ultimo_html = ""
    peticiones = 0
    es_hg = signatura.lower().startswith("hg")
    es_exp = signatura.upper().startswith("EXP")

    async def _fetch_opac(url: str) -> str:
        nonlocal ultimo_html, peticiones
        peticiones += 1
        if cliente_pw is not None:
            html = await cliente_pw.obtener(url)
        else:
            html = await cliente_http.obtener(url)
        ultimo_html = html
        return html

    # Paso 1: Signatura exacta en t852 (para no-HG y no-EXP sin espacios)
    if signatura and not es_hg and not es_exp and " " not in signatura.strip():
        sig_fmt = f"({signatura.strip().replace('/', '!')}.t852.)"
        url_sig = f"{OPAC_BASE_URL}?ACC=DOSEARCH&xsqf99={urllib.parse.quote(sig_fmt, safe='')}{FILTRO_BIBLIOTECA}"
        html = await _fetch_opac(url_sig)
        mejor = elegir_mejor_candidato(
            extraer_candidatos_opac(html), titulo_orig, autor, fecha, "opac_signatura", quitar_barra
        )
        if mejor:
            return ResultadoBusqueda(mejor[0], "opac:t852", mejor[1], peticiones), html, peticiones

        # Paso 2: Si la signatura tenía sufijo de volumen (-I, -II, -VIII, -311), probar signatura base
        sig_base = normalizar_signatura_base(signatura)
        if sig_base and sig_base != signatura.strip():
            sig_base_fmt = f"({sig_base.replace('/', '!')}.t852.)"
            url_base = f"{OPAC_BASE_URL}?ACC=DOSEARCH&xsqf99={urllib.parse.quote(sig_base_fmt, safe='')}{FILTRO_BIBLIOTECA}"
            html = await _fetch_opac(url_base)
            mejor = elegir_mejor_candidato(
                extraer_candidatos_opac(html), titulo_orig, autor, fecha, "opac_signatura", quitar_barra
            )
            if mejor:
                return ResultadoBusqueda(mejor[0], "opac:t852_base", mejor[1], peticiones), html, peticiones

    # Paso 3: Búsqueda estructurada por campos nativos de AbsysNet (xsqf02 título + xsqf03 autor)
    frases = generar_frases_consulta(titulo_orig)
    apellido = extraer_apellido_principal(autor)

    # 3a) Con filtro Biblioteca de Humanidades (&subcat=BH):
    #     Intento 1: frase inicial limpia (+ apellido)
    #     Intento 2 (si falla): >=4 palabras clave seguras sin caracteres especiales ni tildes
    for idx, frase in enumerate(frases[:2]):
        params: dict[str, str] = {"ACC": "DOSEARCH", "xsqf02": frase}
        if apellido and idx == 0:
            params["xsqf03"] = apellido
        url_q = f"{OPAC_BASE_URL}?{urllib.parse.urlencode(params)}{FILTRO_BIBLIOTECA}"
        html = await _fetch_opac(url_q)
        mejor = elegir_mejor_candidato(
            extraer_candidatos_opac(html), titulo_orig, autor, fecha, "opac_texto", quitar_barra
        )
        if mejor:
            etiqueta = "opac:campos_bh" if idx == 0 else "opac:kw4_bh"
            return ResultadoBusqueda(mejor[0], etiqueta, mejor[1], peticiones), html, peticiones

    # 3b) Si falló con subcat=BH, reintentar en todo el catálogo Uniovi (sin subcat=BH)
    #     probando primero frase principal y, si falla, >=4 palabras clave seguras sin especiales
    for idx, frase in enumerate(frases[:2]):
        params_global: dict[str, str] = {"ACC": "DOSEARCH", "xsqf02": frase}
        if apellido and idx == 0:
            params_global["xsqf03"] = apellido
        url_global = f"{OPAC_BASE_URL}?{urllib.parse.urlencode(params_global)}"
        html = await _fetch_opac(url_global)
        mejor = elegir_mejor_candidato(
            extraer_candidatos_opac(html), titulo_orig, autor, fecha, "opac_texto", quitar_barra
        )
        if mejor:
            etiqueta = "opac:global" if idx == 0 else "opac:kw4_global"
            return ResultadoBusqueda(mejor[0], etiqueta, mejor[1], peticiones), html, peticiones

    return None, ultimo_html, peticiones


def _escapar_cql(texto: str) -> str:
    return re.sub(r'["\\]', " ", texto).strip()


def _preparar_intentos_externos(titulo_orig: str) -> list[str]:
    """Devuelve hasta 2 consultas para fuentes externas:
      1. Frase principal limpia (o la de palabras clave si la 1.ª tiene fusiones largas).
      2. Respaldo de >=4 palabras clave seguras sin caracteres especiales/tildes.
    """
    frases = generar_frases_consulta(titulo_orig)
    if not frases:
        return []
    if len(frases) >= 2 and any(len(w) >= 15 for w in frases[0].split()):
        primera = frases[1]
        segunda = frases[2] if len(frases) >= 3 else ""
    else:
        primera = frases[0]
        segunda = frases[1] if len(frases) >= 2 else ""

    intentos = [primera]
    if segunda and segunda.lower() != primera.lower():
        intentos.append(segunda)
    return intentos


async def buscar_en_bne(
    cliente: ClienteHTTP,
    titulo_orig: str,
    autor: str,
    fecha: str,
    quitar_barra: bool,
) -> tuple[ResultadoBusqueda | None, int]:
    """Consulta la API SRU 1.2 oficial de la Biblioteca Nacional de España (MARCXML)."""
    intentos = _preparar_intentos_externos(titulo_orig)
    if not intentos:
        return None, 0
    apellido = extraer_apellido_principal(autor)
    peticiones = 0

    consultas_cql: list[str] = []
    t1 = _escapar_cql(intentos[0])
    if apellido:
        consultas_cql.append(f'alma.title="{t1}" and alma.creator="{_escapar_cql(apellido)}"')
    else:
        consultas_cql.append(f'alma.title="{t1}"')

    # 2.º intento: >=4 palabras clave seguras sin caracteres especiales/tildes (sin restricción rígida de autor)
    if len(intentos) >= 2:
        t2 = _escapar_cql(intentos[1])
        consultas_cql.append(f'alma.title all "{t2}"')
    elif apellido:
        consultas_cql.append(f'alma.title all "{t1}"')

    for cql in consultas_cql[:2]:
        params = {
            "version": "1.2",
            "operation": "searchRetrieve",
            "recordSchema": "marcxml",
            "maximumRecords": "10",
            "query": cql,
        }
        url = f"{BNE_SRU_URL}?{urllib.parse.urlencode(params)}"
        xml_text = await cliente.obtener(url, reintentos=1)
        peticiones += 1
        cands = extraer_candidatos_marcxml(xml_text, fuente="bne")
        mejor = elegir_mejor_candidato(cands, titulo_orig, autor, fecha, "externo", quitar_barra)
        if mejor:
            return ResultadoBusqueda(mejor[0], "bne", mejor[1], peticiones), peticiones
    return None, peticiones


async def buscar_en_bnf(
    cliente: ClienteHTTP,
    titulo_orig: str,
    autor: str,
    fecha: str,
    quitar_barra: bool,
) -> tuple[ResultadoBusqueda | None, int]:
    """Consulta la API SRU 1.2 de la Bibliothèque nationale de France (UNIMARCXchange)."""
    intentos = _preparar_intentos_externos(titulo_orig)
    if not intentos:
        return None, 0
    apellido = extraer_apellido_principal(autor)
    peticiones = 0

    for idx, frase in enumerate(intentos[:2]):
        t = _escapar_cql(frase)
        if apellido and idx == 0:
            cql = f'(bib.title all "{t}") and (bib.author all "{_escapar_cql(apellido)}")'
        else:
            cql = f'(bib.title all "{t}")'
        params = {
            "version": "1.2",
            "operation": "searchRetrieve",
            "recordSchema": "unimarcXchange",
            "maximumRecords": "10",
            "query": cql,
        }
        url = f"{BNF_SRU_URL}?{urllib.parse.urlencode(params)}"
        xml_text = await cliente.obtener(url, reintentos=1)
        peticiones += 1
        cands = extraer_candidatos_unimarcxml(xml_text, fuente="bnf")
        mejor = elegir_mejor_candidato(cands, titulo_orig, autor, fecha, "externo", quitar_barra)
        if mejor:
            return ResultadoBusqueda(mejor[0], "bnf", mejor[1], peticiones), peticiones
    return None, peticiones


async def buscar_en_dnb(
    cliente: ClienteHTTP,
    titulo_orig: str,
    autor: str,
    fecha: str,
    quitar_barra: bool,
) -> tuple[ResultadoBusqueda | None, int]:
    """Consulta la API SRU 1.1 de la Deutsche Nationalbibliothek (MARC21-XML)."""
    intentos = _preparar_intentos_externos(titulo_orig)
    if not intentos:
        return None, 0
    apellido = extraer_apellido_principal(autor)
    peticiones = 0

    for idx, frase in enumerate(intentos[:2]):
        t = _escapar_cql(frase)
        if apellido and idx == 0:
            cql = f'tit="{t}" and per="{_escapar_cql(apellido)}"'
        else:
            cql = f'tit all "{t}"'
        params = {
            "version": "1.1",
            "operation": "searchRetrieve",
            "recordSchema": "MARC21-xml",
            "maximumRecords": "10",
            "query": cql,
        }
        url = f"{DNB_SRU_URL}?{urllib.parse.urlencode(params)}"
        xml_text = await cliente.obtener(url, reintentos=1)
        peticiones += 1
        cands = extraer_candidatos_marcxml(xml_text, fuente="dnb")
        mejor = elegir_mejor_candidato(cands, titulo_orig, autor, fecha, "externo", quitar_barra)
        if mejor:
            return ResultadoBusqueda(mejor[0], "dnb", mejor[1], peticiones), peticiones
    return None, peticiones


async def buscar_en_loc(
    cliente: ClienteHTTP,
    titulo_orig: str,
    autor: str,
    fecha: str,
    quitar_barra: bool,
) -> tuple[ResultadoBusqueda | None, int]:
    """Consulta la API SRU 1.1 de la Library of Congress (MARCXML)."""
    intentos = _preparar_intentos_externos(titulo_orig)
    if not intentos:
        return None, 0
    apellido = extraer_apellido_principal(autor)
    peticiones = 0

    for idx, frase in enumerate(intentos[:2]):
        t = _escapar_cql(frase)
        if apellido and idx == 0:
            cql = f'dc.title="{t}" and bath.name="{_escapar_cql(apellido)}"'
        else:
            cql = f'dc.title all "{t}"'
        params = {
            "version": "1.1",
            "operation": "searchRetrieve",
            "recordSchema": "marcxml",
            "maximumRecords": "10",
            "query": cql,
        }
        url = f"{LOC_SRU_URL}?{urllib.parse.urlencode(params)}"
        xml_text = await cliente.obtener(url, reintentos=1)
        peticiones += 1
        cands = extraer_candidatos_marcxml(xml_text, fuente="loc")
        mejor = elegir_mejor_candidato(cands, titulo_orig, autor, fecha, "externo", quitar_barra)
        if mejor:
            return ResultadoBusqueda(mejor[0], "loc", mejor[1], peticiones), peticiones
    return None, peticiones


async def buscar_en_openlibrary(
    cliente: ClienteHTTP,
    titulo_orig: str,
    autor: str,
    fecha: str,
    quitar_barra: bool,
) -> tuple[ResultadoBusqueda | None, int]:
    """Consulta la API REST de Open Library."""
    intentos = _preparar_intentos_externos(titulo_orig)
    if not intentos:
        return None, 0
    apellido = extraer_apellido_principal(autor)
    peticiones = 0

    for idx, frase in enumerate(intentos[:2]):
        params: dict[str, str] = {
            "title": frase,
            "limit": "10",
            "fields": "title,subtitle,author_name,first_publish_year",
        }
        if apellido and idx == 0:
            params["author"] = apellido
        url = f"{OPENLIBRARY_URL}?{urllib.parse.urlencode(params)}"
        js = await cliente.obtener(url, reintentos=1)
        peticiones += 1
        cands = extraer_candidatos_openlibrary(js)
        mejor = elegir_mejor_candidato(cands, titulo_orig, autor, fecha, "externo", quitar_barra)
        if mejor:
            return ResultadoBusqueda(mejor[0], "openlibrary", mejor[1], peticiones), peticiones
    return None, peticiones


async def buscar_en_googlebooks(
    cliente: ClienteHTTP,
    titulo_orig: str,
    autor: str,
    fecha: str,
    quitar_barra: bool,
    api_key: str = "",
) -> tuple[ResultadoBusqueda | None, int]:
    """Consulta Google Books API."""
    intentos = _preparar_intentos_externos(titulo_orig)
    if not intentos:
        return None, 0
    apellido = extraer_apellido_principal(autor)
    peticiones = 0

    for idx, frase in enumerate(intentos[:2]):
        if idx == 0:
            q_parts = [f'intitle:"{_escapar_cql(frase)}"']
            if apellido:
                q_parts.append(f"inauthor:{_escapar_cql(apellido)}")
        else:
            # 2.º intento: >=4 palabras clave seguras sin comillas de frase exacta
            q_parts = [f"intitle:{_escapar_cql(w)}" for w in frase.split() if w]
        params: dict[str, str] = {
            "q": " ".join(q_parts),
            "maxResults": "10",
            "printType": "books",
            "fields": "items(volumeInfo(title,subtitle,authors,publishedDate))",
        }
        if api_key:
            params["key"] = api_key
        url = f"{GOOGLE_BOOKS_URL}?{urllib.parse.urlencode(params)}"
        js = await cliente.obtener(url, reintentos=1)
        peticiones += 1
        cands = extraer_candidatos_googlebooks(js)
        mejor = elegir_mejor_candidato(cands, titulo_orig, autor, fecha, "externo", quitar_barra)
        if mejor:
            return ResultadoBusqueda(mejor[0], "googlebooks", mejor[1], peticiones), peticiones
    return None, peticiones


def ordenar_fuentes_externas(fuentes_activas: list[str], idioma: str) -> list[str]:
    """Ordena las fuentes externas priorizando el catálogo nacional del idioma del libro."""
    preferencias = {
        "es": ["bne", "openlibrary", "googlebooks", "loc", "bnf", "dnb"],
        "fr": ["bnf", "bne", "openlibrary", "googlebooks", "loc", "dnb"],
        "de": ["dnb", "loc", "openlibrary", "googlebooks", "bne", "bnf"],
        "en": ["loc", "openlibrary", "googlebooks", "bne", "bnf", "dnb"],
        "it": ["bne", "bnf", "openlibrary", "googlebooks", "loc", "dnb"],
    }
    orden = preferencias.get(idioma, preferencias["es"])
    activas_set = set(fuentes_activas)
    return [f for f in orden if f in activas_set]


# =============================================================================
# 7. ESCRITURA ATÓMICA, REANUDACIÓN Y ORQUESTACIÓN PRINCIPAL
# =============================================================================

def escribir_json_atomico(ruta: Path, datos: Any) -> None:
    tmp = ruta.with_suffix(ruta.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(datos, f, ensure_ascii=False, indent=2)
    os.replace(tmp, ruta)


def clave_identidad_registro(reg: dict[str, Any]) -> str:
    """Identificador único del ejemplar para reanudar sin duplicar registros."""
    cb = str(reg.get("cbarras") or "").strip()
    if cb:
        return f"cb:{cb}"
    return (
        f"{str(reg.get('signatura') or '').strip().upper()}|"
        f"{normalizar_texto(str(reg.get('Autor') or reg.get('autor') or ''))}|"
        f"{str(reg.get('fecha') or '').strip()}|"
        f"{normalizar_compacto(str(reg.get('titulo') or ''))}"
    )


def formatear_eta(segundos: float) -> str:
    segundos = max(0, int(segundos))
    h, rem = divmod(segundos, 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return f"{h}h {m:02d}m"
    if m > 0:
        return f"{m}m {s:02d}s"
    return f"{s}s"


async def resolver_registro(
    reg: dict[str, Any],
    cliente_http: ClienteHTTP,
    cliente_pw: ClientePlaywright | None,
    cache: CacheSQLite,
    indice_local: dict[tuple[str, str], list[Candidato]],
    fuentes_activas: list[str],
    quitar_barra: bool,
    google_api_key: str,
    en_curso_locks: dict[str, asyncio.Lock],
    locks_guard: asyncio.Lock,
) -> tuple[ResultadoBusqueda | None, str]:
    """Resuelve un registro siguiendo la cascada configurada (con deduplicación en vuelo)."""
    signatura = str(reg.get("signatura") or "").strip()
    titulo_orig = str(reg.get("titulo") or "").strip()
    autor = str(reg.get("Autor") or reg.get("autor") or "").strip()
    fecha = str(reg.get("fecha") or "").strip()

    clave_obra = cache.construir_clave(titulo_orig, autor, fecha)

    # Lock por obra para que 2 ejemplares idénticos en paralelo no disparen 2 peticiones
    async with locks_guard:
        obra_lock = en_curso_locks.setdefault(clave_obra, asyncio.Lock())

    async with obra_lock:
        # 0a. Caché SQLite persistente
        en_cache = cache.obtener(clave_obra)
        if en_cache is not None and en_cache.titulo:
            return ResultadoBusqueda(en_cache.titulo, f"cache({en_cache.fuente})", en_cache.confianza, 0), ""

        # 0b. Índice local (`libros*.json`)
        if "local" in fuentes_activas and indice_local:
            tc = normalizar_compacto(titulo_orig)
            if len(tc) >= 28:
                cands_loc = indice_local.get((tc[:28], extraer_apellido_principal(autor)), [])
                mejor_loc = elegir_mejor_candidato(
                    cands_loc, titulo_orig, autor, fecha, "opac_texto", quitar_barra
                )
                if mejor_loc:
                    cache.guardar(clave_obra, mejor_loc[0], "local", mejor_loc[1])
                    return ResultadoBusqueda(mejor_loc[0], "local", mejor_loc[1], 0), ""

        ultimo_html = ""
        total_pet = 0

        # 1. OPAC Universidad de Oviedo (fuente primaria)
        if "opac" in fuentes_activas:
            res_opac, ultimo_html, pet_opac = await buscar_en_opac(
                cliente_http=cliente_http,
                cliente_pw=cliente_pw,
                signatura=signatura,
                titulo_orig=titulo_orig,
                autor=autor,
                fecha=fecha,
                quitar_barra=quitar_barra,
            )
            total_pet += pet_opac
            if res_opac:
                cache.guardar(clave_obra, res_opac.titulo, res_opac.fuente, res_opac.confianza)
                return res_opac, ultimo_html

        # 2-4. Fuentes externas ordenadas según el idioma del título
        idioma = detectar_idioma(titulo_orig)
        externas = ordenar_fuentes_externas(fuentes_activas, idioma)
        for fuente in externas:
            res_ext: ResultadoBusqueda | None = None
            pet_ext = 0
            if fuente == "bne":
                res_ext, pet_ext = await buscar_en_bne(cliente_http, titulo_orig, autor, fecha, quitar_barra)
            elif fuente == "bnf":
                res_ext, pet_ext = await buscar_en_bnf(cliente_http, titulo_orig, autor, fecha, quitar_barra)
            elif fuente == "dnb":
                res_ext, pet_ext = await buscar_en_dnb(cliente_http, titulo_orig, autor, fecha, quitar_barra)
            elif fuente == "loc":
                res_ext, pet_ext = await buscar_en_loc(cliente_http, titulo_orig, autor, fecha, quitar_barra)
            elif fuente == "openlibrary":
                res_ext, pet_ext = await buscar_en_openlibrary(cliente_http, titulo_orig, autor, fecha, quitar_barra)
            elif fuente == "googlebooks":
                res_ext, pet_ext = await buscar_en_googlebooks(
                    cliente_http, titulo_orig, autor, fecha, quitar_barra, api_key=google_api_key
                )
            total_pet += pet_ext
            if res_ext:
                cache.guardar(clave_obra, res_ext.titulo, res_ext.fuente, res_ext.confianza)
                return ResultadoBusqueda(res_ext.titulo, res_ext.fuente, res_ext.confianza, total_pet), ultimo_html

        return None, ultimo_html


async def detectar_motor_opac(
    muestra: dict[str, Any],
    cliente_http: ClienteHTTP,
    motor_solicitado: str,
    concurrencia: int,
    headless: bool,
) -> tuple[ClientePlaywright | None, str]:
    """Comprueba si HTTP directo obtiene HTML válido del OPAC; si no, activa Playwright."""
    if motor_solicitado == "http":
        return None, f"http ({cliente_http.backend})"
    if motor_solicitado == "playwright":
        pw = ClientePlaywright(concurrencia=min(concurrencia, 4), headless=headless)
        await pw.iniciar()
        return pw, "playwright"

    # Modo auto: probar 1 consulta real al OPAC por HTTP directo
    sig = str(muestra.get("signatura") or "").strip()
    tit = str(muestra.get("titulo") or "").strip()
    if sig and not sig.lower().startswith("hg") and " " not in sig:
        url = f"{OPAC_BASE_URL}?ACC=DOSEARCH&xsqf99={urllib.parse.quote(f'({sig.replace('/', '!')}.t852.)', safe='')}{FILTRO_BIBLIOTECA}"
    else:
        frases = generar_frases_consulta(tit)
        q = frases[0] if frases else tit
        url = f"{OPAC_BASE_URL}?ACC=DOSEARCH&xsqf02={urllib.parse.quote(q, safe='')}{FILTRO_BIBLIOTECA}"

    html = await cliente_http.obtener(url, reintentos=1)
    if html and ("abnetopac" in html.lower() or "js-t245" in html.lower()):
        return None, f"http ({cliente_http.backend})"

    print("[!] HTTP directo no recibió HTML estándar de AbsysNet; intentando respaldo con Playwright...")
    try:
        pw = ClientePlaywright(concurrencia=min(concurrencia, 3), headless=headless)
        await pw.iniciar()
        return pw, "playwright"
    except Exception as exc:
        print(f"[Aviso] Playwright no disponible ({exc}); continuando con motor HTTP ({cliente_http.backend}).")
        return None, f"http ({cliente_http.backend})"


async def ejecutar_proceso(args: argparse.Namespace) -> None:
    ruta_entrada = Path(args.entrada)
    if not ruta_entrada.exists():
        print(f"[Error] No se encontró el archivo de entrada: '{ruta_entrada}'.")
        return

    ruta_salida = Path(args.salida)
    ruta_no_enc = Path(args.no_encontrados)
    ruta_informe = Path(args.informe)
    ruta_cache = Path(args.cache)
    carpeta_debug = Path("debug_html")
    quitar_barra = not args.conservar_barra
    fuentes_activas = [f.strip().lower() for f in args.fuentes.split(",") if f.strip()]
    google_key = args.google_api_key or os.environ.get("GOOGLE_BOOKS_API_KEY", "")

    with ruta_entrada.open("r", encoding="utf-8") as f:
        registros_crudos = json.load(f)
    if not isinstance(registros_crudos, list):
        print("[Error] El archivo de entrada debe contener una lista JSON.")
        return

    # Cargar estado previo para reanudación segura
    resultados_finales: list[dict[str, Any]] = []
    no_encontrados: list[dict[str, Any]] = []
    claves_resueltas: set[str] = set()
    claves_fallidas: set[str] = set()

    if ruta_salida.exists():
        try:
            prev = json.loads(ruta_salida.read_text(encoding="utf-8"))
            if isinstance(prev, list):
                resultados_finales = prev
                claves_resueltas = {clave_identidad_registro(r) for r in prev if isinstance(r, dict)}
        except Exception as exc:
            print(f"[Aviso] No se pudo leer '{ruta_salida}' previo: {exc}")

    if ruta_no_enc.exists() and not args.reintentar_fallidos:
        try:
            prev_nf = json.loads(ruta_no_enc.read_text(encoding="utf-8"))
            if isinstance(prev_nf, list):
                no_encontrados = prev_nf
                claves_fallidas = {clave_identidad_registro(r) for r in prev_nf if isinstance(r, dict)}
        except Exception as exc:
            print(f"[Aviso] No se pudo leer '{ruta_no_enc}' previo: {exc}")

    # Filtrar pendientes (sin modificar truncados.json jamás)
    pendientes: list[dict[str, Any]] = []
    for reg in registros_crudos:
        if not isinstance(reg, dict):
            continue
        sig = str(reg.get("signatura") or "").strip()
        tit = str(reg.get("titulo") or "").strip()
        if not sig or not tit:
            continue
        if not args.incluir_exp and sig.upper().startswith("EXP"):
            continue
        cid = clave_identidad_registro(reg)
        if cid in claves_resueltas:
            continue
        if cid in claves_fallidas and not args.reintentar_fallidos:
            continue
        pendientes.append(reg)

    if args.limite and args.limite > 0:
        pendientes = pendientes[: args.limite]

    total_pendientes = len(pendientes)
    print(f"=== Buscador de títulos completos BUO Humanidades ===")
    print(f"Entrada (solo lectura): {ruta_entrada} ({len(registros_crudos)} registros)")
    print(f"Ya resueltos en '{ruta_salida}': {len(claves_resueltas)} | Fallidos previos omitidos: {len(claves_fallidas)}")
    print(f"Pendientes a procesar ahora: {total_pendientes}")
    print(f"Fuentes activas: {', '.join(fuentes_activas)} | Limpiar tras '/': {'Sí' if quitar_barra else 'No'}")

    if total_pendientes == 0:
        print("No hay registros pendientes por procesar.")
        return

    indice_local: dict[tuple[str, str], list[Candidato]] = {}
    if "local" in fuentes_activas and not args.sin_locales:
        t0_loc = time.monotonic()
        indice_local = construir_indice_local(ruta_entrada.parent, quitar_barra=quitar_barra)
        print(f"Índice local (`libros*.json`): {len(indice_local)} obras completas cargadas en {time.monotonic() - t0_loc:.2f}s")

    cache = CacheSQLite(ruta_cache)
    cliente_http = ClienteHTTP(timeout_s=args.timeout, pausa_opac=args.pausa_opac)
    await cliente_http.iniciar()
    cliente_pw: ClientePlaywright | None = None

    if "opac" in fuentes_activas:
        cliente_pw, motor_desc = await detectar_motor_opac(
            pendientes[0], cliente_http, args.motor, args.concurrencia, headless=not args.ver_navegador
        )
        print(f"Motor OPAC activo: {motor_desc} | Concurrencia: {args.concurrencia}")

    if args.guardar_html_error:
        carpeta_debug.mkdir(parents=True, exist_ok=True)

    # Preparar informe CSV incremental
    escribir_cabecera_csv = not ruta_informe.exists()
    csv_file = ruta_informe.open("a", encoding="utf-8", newline="")
    csv_writer = csv.writer(csv_file)
    if escribir_cabecera_csv:
        csv_writer.writerow(["cbarras", "signatura", "estado", "fuente", "confianza", "titulo_original", "titulo_final"])

    # Control de parada limpia (Ctrl+C)
    detener = asyncio.Event()

    def _on_sigint() -> None:
        if not detener.is_set():
            print("\n[!] Interrupción detectada (Ctrl+C). Guardando progreso actual de forma segura...")
            detener.set()

    loop = asyncio.get_running_loop()
    try:
        loop.add_signal_handler(signal.SIGINT, _on_sigint)
    except NotImplementedError:
        # Windows no soporta add_signal_handler en ProactorEventLoop
        signal.signal(signal.SIGINT, lambda *_: _on_sigint())

    cola: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    for reg in pendientes:
        cola.put_nowait(reg)

    en_curso_locks: dict[str, asyncio.Lock] = {}
    locks_guard = asyncio.Lock()
    estado_lock = asyncio.Lock()

    procesados = 0
    encontrados_sesion = 0
    sin_guardado = 0
    conteo_fuentes: Counter[str] = Counter()
    inicio_ts = time.monotonic()
    ultimo_volcado_ts = inicio_ts

    def _volcar_a_disco() -> None:
        nonlocal ultimo_volcado_ts
        escribir_json_atomico(ruta_salida, resultados_finales)
        escribir_json_atomico(ruta_no_enc, no_encontrados)
        csv_file.flush()
        ultimo_volcado_ts = time.monotonic()

    async def worker() -> None:
        nonlocal procesados, encontrados_sesion, sin_guardado
        while not detener.is_set():
            try:
                reg = cola.get_nowait()
            except asyncio.QueueEmpty:
                return

            sig = str(reg.get("signatura") or "").strip()
            tit_orig = str(reg.get("titulo") or "").strip()
            cb = str(reg.get("cbarras") or "").strip()

            try:
                res, html_err = await resolver_registro(
                    reg=reg,
                    cliente_http=cliente_http,
                    cliente_pw=cliente_pw,
                    cache=cache,
                    indice_local=indice_local,
                    fuentes_activas=fuentes_activas,
                    quitar_barra=quitar_barra,
                    google_api_key=google_key,
                    en_curso_locks=en_curso_locks,
                    locks_guard=locks_guard,
                )
            except Exception as exc:
                res, html_err = None, ""
                print(f"[Error] Fallo inesperado en {sig}: {exc}")

            async with estado_lock:
                procesados += 1
                sin_guardado += 1
                ahora_ts = time.monotonic()
                transcurrido = max(0.001, ahora_ts - inicio_ts)
                vel_min = (procesados / transcurrido) * 60.0
                eta = formatear_eta((total_pendientes - procesados) / (procesados / transcurrido))

                if res is not None:
                    reg_out = dict(reg)
                    reg_out["titulo"] = res.titulo
                    resultados_finales.append(reg_out)
                    encontrados_sesion += 1
                    conteo_fuentes[res.fuente] += 1
                    csv_writer.writerow([cb, sig, "OK", res.fuente, res.confianza, tit_orig, res.titulo])
                    print(
                        f"[{procesados}/{total_pendientes} | {vel_min:.0f}/min | ETA {eta}] "
                        f"[OK:{res.fuente}] {sig} -> {res.titulo}"
                    )
                else:
                    no_encontrados.append(dict(reg))
                    conteo_fuentes["no_encontrado"] += 1
                    csv_writer.writerow([cb, sig, "NO_ENCONTRADO", "", "", tit_orig, ""])
                    print(
                        f"[{procesados}/{total_pendientes} | {vel_min:.0f}/min | ETA {eta}] "
                        f"[--] Sin coincidencia: {sig} | {tit_orig}"
                    )
                    if args.guardar_html_error and html_err:
                        nombre_seguro = re.sub(r"[^A-Za-z0-9._-]", "_", sig)[:80]
                        (carpeta_debug / f"error_{nombre_seguro}_{cb}.html").write_text(html_err, encoding="utf-8")

                # Volcar a disco si superó el umbral de registros y han pasado al menos 5s desde el último volcado
                if sin_guardado >= args.guardar_cada and (ahora_ts - ultimo_volcado_ts) >= 5.0:
                    _volcar_a_disco()
                    sin_guardado = 0
                    print(f"[*] Punto de control guardado en '{ruta_salida}' ({len(resultados_finales)} resueltos).")

            cola.task_done()

    try:
        num_workers = max(1, min(args.concurrencia, total_pendientes))
        tareas = [asyncio.create_task(worker()) for _ in range(num_workers)]
        await asyncio.gather(*tareas)
    finally:
        _volcar_a_disco()
        csv_file.close()
        cache.cerrar()
        await cliente_http.cerrar()
        if cliente_pw is not None:
            await cliente_pw.cerrar()

    duracion = formatear_eta(time.monotonic() - inicio_ts)
    tasa = (encontrados_sesion / procesados * 100.0) if procesados else 0.0
    print("\n=== Resumen de la ejecución ===")
    print(f"Procesados esta sesión: {procesados} en {duracion}")
    print(f"Resueltos esta sesión:  {encontrados_sesion} ({tasa:.1f}%)")
    print(f"Total en '{ruta_salida}': {len(resultados_finales)} | En '{ruta_no_enc}': {len(no_encontrados)}")
    print("Desglose por fuente:")
    for fuente, cnt in conteo_fuentes.most_common():
        print(f"  - {fuente}: {cnt}")


def construir_parser_cli() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Recupera títulos completos desde OPAC Uniovi y catálogos externos generando final.json.",
    )
    p.add_argument("--entrada", default="truncados.json", help="JSON de entrada (nunca se modifica). Por defecto: truncados.json")
    p.add_argument("--salida", default="final.json", help="JSON de salida listo para 5_sustituir_titulos.html. Por defecto: final.json")
    p.add_argument("--no-encontrados", default="no_encontrados.json", help="JSON con registros no resueltos.")
    p.add_argument("--informe", default="informe_busqueda.csv", help="CSV de auditoría con la fuente de cada título.")
    p.add_argument("--cache", default="cache_busqueda.sqlite", help="Base de datos SQLite de caché persistente.")
    p.add_argument(
        "--fuentes",
        default=",".join(FUENTES_DEFECTO),
        help=f"Fuentes activas separadas por coma. Por defecto: {','.join(FUENTES_DEFECTO)}",
    )
    p.add_argument("--motor", choices=("auto", "http", "playwright"), default="auto", help="Motor para el OPAC de Uniovi.")
    p.add_argument("--concurrencia", type=int, default=6, help="Número de trabajadores concurrentes. Por defecto: 6")
    p.add_argument("--pausa-opac", type=float, default=0.25, help="Intervalo mínimo (s) entre peticiones al OPAC Uniovi.")
    p.add_argument("--timeout", type=float, default=18.0, help="Timeout HTTP por petición en segundos.")
    p.add_argument("--guardar-cada", type=int, default=25, help="Frecuencia de guardado atómico a disco.")
    p.add_argument("--limite", type=int, default=0, help="Procesar solo N registros (útil para pruebas rápidas).")
    p.add_argument("--conservar-barra", action="store_true", help="No eliminar la mención de responsabilidad tras '/'.")
    p.add_argument("--sin-locales", action="store_true", help="Desactivar búsqueda previa en libros*.json locales.")
    p.add_argument("--incluir-exp", action="store_true", default=True, help="Incluir también signaturas EXP (activado por defecto).")
    p.add_argument("--excluir-exp", dest="incluir_exp", action="store_false", help="Omitir signaturas que empiecen por EXP.")
    p.add_argument("--reintentar-fallidos", action="store_true", help="Volver a intentar los registros guardados en no_encontrados.json.")
    p.add_argument("--google-api-key", default="", help="Clave opcional de Google Books API (o variable GOOGLE_BOOKS_API_KEY).")
    p.add_argument("--guardar-html-error", action="store_true", help="Guardar HTML de fallos en la subcarpeta debug_html/.")
    p.add_argument("--ver-navegador", action="store_true", help="Mostrar navegador si se usa --motor playwright.")
    return p


def main() -> None:
    parser = construir_parser_cli()
    args = parser.parse_args()
    asyncio.run(ejecutar_proceso(args))


if __name__ == "__main__":
    main()