"""Detecta y extrae títulos probablemente truncados de los archivos libros<N>.json.

Archivo autónomo (sin dependencias externas):
  - Soporta tanto el formato compacto [Autor, titulo, fecha, signatura] como
    el formato de objetos {"Autor", "titulo", "fecha", "signatura"}.
  - Localiza automáticamente libros1.json, libros2.json, libros3.json... en la
    carpeta actual o en la carpeta raíz del proyecto.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

PATRON_LIBROS = re.compile(r"^libros\d+\.json$", re.IGNORECASE)

CLAVES_AUTOR = ("Autor", "autor", "AUTOR")
CLAVES_TITULO = ("titulo", "Titulo", "TITULO", "título", "Título")
CLAVES_FECHA = ("fecha", "Fecha", "FECHA")
CLAVES_SIGNATURA = ("signatura", "Signatura", "SIGNATURA")

# Artículos, preposiciones, conjunciones y determinantes que rara vez cierran un título
PALABRAS_COLGANTES = {
    # Español
    "el", "la", "los", "las", "un", "una", "unos", "unas",
    "de", "del", "en", "por", "para", "con", "sin", "sobre", "entre",
    "hacia", "hasta", "desde", "según", "segun", "ante", "bajo", "contra",
    "a", "al", "y", "e", "o", "u", "ni", "que", "como", "cuando", "donde",
    "su", "sus", "mi", "mis", "tu", "tus", "este", "esta", "estos", "estas",
    "ese", "esa", "esos", "esas", "aquel", "aquella",
    # Inglés
    "the", "an", "and", "or", "of", "in", "on", "at", "to", "for", "from",
    "with", "without", "by", "into", "onto", "upon", "about", "through",
    "between", "under", "over", "its", "their", "our", "your", "his", "her",
    # Francés
    "le", "les", "du", "des", "au", "aux", "et", "ou", "dans", "sur", "sous",
    "par", "pour", "avec", "sans", "chez", "vers", "entre", "une", "son", "sa", "ses",
    # Alemán
    "der", "die", "das", "den", "dem", "ein", "eine", "einen", "einem", "einer",
    "und", "oder", "von", "vom", "mit", "für", "fur", "aus", "bei", "nach",
    "über", "uber", "unter", "durch", "zum", "zur", "im", "am", "als", "bis",
    # Italiano / Catalán / Portugués
    "il", "lo", "gli", "di", "da", "dal", "dalla", "dalle", "dello", "della",
    "delle", "degli", "nel", "nella", "nelle", "negli", "sul", "sulla", "per",
    "tra", "fra", "che", "ed", "els", "amb", "pel", "pels", "dos", "das", "nos", "nas",
}

# Numerales romanos cortos válidos al final de un título (ej. "Siglo XX", "Tomo IV")
ROMANOS_CORTOS = {"i", "ii", "iv", "v", "vi", "ix", "x", "xi", "xv", "xx"}

# Palabras cortas completas que pueden cerrar legítimamente un título
FINALES_CORTOS_VALIDOS = {
    "no", "sí", "si", "ya", "fe", "yo", "tú", "tu", "él", "tv", "cd", "pc",
    "me", "te", "se", "us", "up", "go", "ok", "uk", "eu", "un",
}

PATRON_SOLO_DIGITOS = re.compile(r"^\d+$")
PATRON_PUNTUACION_ABIERTA = re.compile(r"[-:/(,;«¿¡]$")


def _primera_clave(registro: dict[str, Any], claves: tuple[str, ...]) -> str:
    for clave in claves:
        if clave in registro and registro[clave] is not None:
            return str(registro[clave]).strip()
    return ""


def extraer_campos(registro: Any) -> tuple[str, str, str, str]:
    """Extrae (autor, titulo, fecha, signatura) tanto de listas compactas como de dicts."""
    if isinstance(registro, list):
        autor = str(registro[0]).strip() if len(registro) > 0 and registro[0] is not None else ""
        titulo = str(registro[1]).strip() if len(registro) > 1 and registro[1] is not None else ""
        fecha = str(registro[2]).strip() if len(registro) > 2 and registro[2] is not None else ""
        signatura = str(registro[3]).strip() if len(registro) > 3 and registro[3] is not None else ""
        return autor, titulo, fecha, signatura

    if isinstance(registro, dict):
        autor = _primera_clave(registro, CLAVES_AUTOR)
        titulo = _primera_clave(registro, CLAVES_TITULO)
        fecha = _primera_clave(registro, CLAVES_FECHA)
        signatura = _primera_clave(registro, CLAVES_SIGNATURA)
        return autor, titulo, fecha, signatura

    raise TypeError(f"Formato de registro no soportado: {type(registro).__name__}")


def registro_a_dict(registro: Any) -> dict[str, str]:
    autor, titulo, fecha, signatura = extraer_campos(registro)
    return {
        "Autor": autor,
        "titulo": titulo,
        "fecha": fecha,
        "signatura": signatura,
    }


def _listar_libros(base: Path) -> list[Path]:
    if not base.is_dir():
        return []

    def clave_orden(p: Path) -> tuple[int, str]:
        m = re.search(r"(\d+)", p.name)
        return (int(m.group(1)) if m else 0, p.name.lower())

    archivos = [p for p in base.iterdir() if p.is_file() and PATRON_LIBROS.match(p.name)]
    return sorted(archivos, key=clave_orden)


def localizar_archivos_libros(directorio: Path | None = None) -> tuple[Path, list[Path]]:
    """Localiza libros1.json, libros2.json, ... en el directorio indicado, cwd o raíz del repo."""
    if directorio is not None:
        base = Path(directorio).resolve()
        return base, _listar_libros(base)

    cwd = Path.cwd().resolve()
    encontrados = _listar_libros(cwd)
    if encontrados:
        return cwd, encontrados

    raiz_repo = Path(__file__).resolve().parent.parent
    encontrados = _listar_libros(raiz_repo)
    if encontrados:
        return raiz_repo, encontrados

    return cwd, []


def iterar_registros_json(ruta: Path) -> Iterable[Any]:
    with open(ruta, "r", encoding="utf-8") as fh:
        datos = json.load(fh)
    if not isinstance(datos, list):
        raise ValueError(f"Se esperaba una lista JSON en {ruta}, se obtuvo {type(datos).__name__}.")
    return datos


def escribir_json_atomico(ruta: Path, datos: Any) -> None:
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    fd, ruta_tmp = tempfile.mkstemp(
        prefix=f".{ruta.name}.",
        suffix=".tmp",
        dir=str(ruta.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(datos, fh, ensure_ascii=False, indent=4)
        os.replace(ruta_tmp, ruta)
    except Exception:
        if os.path.exists(ruta_tmp):
            os.remove(ruta_tmp)
        raise


def analizar_truncamiento(
    titulo: str,
    min_len: int = 55,
    limite_corte: int = 60,
    incluir_exactos_limite: bool = True,
) -> str | None:
    """Devuelve el motivo si el título parece truncado, o None si parece completo.

    Solo evalúa títulos cuya longitud esté entre min_len y limite_corte (por defecto 55..60).
    Los títulos con más de limite_corte caracteres ya están completos y se ignoran.
    """
    titulo_limpio = titulo.strip()
    longitud = len(titulo_limpio)
    if longitud < min_len or longitud > limite_corte:
        return None

    # 1. Puntuación final abierta o paréntesis sin cerrar
    if PATRON_PUNTUACION_ABIERTA.search(titulo_limpio):
        return "puntuacion_abierta"
    if titulo_limpio.count("(") > titulo_limpio.count(")"):
        return "parentesis_sin_cerrar"

    sin_comillas = titulo_limpio.replace('"', "").replace("'", "").strip()
    palabras = sin_comillas.split()
    if not palabras:
        return None

    ultima_original = palabras[-1]
    ultima = ultima_original.rstrip(".,;:!?").lower()
    if not ultima:
        return None

    # 2. Termina en palabra colgante (artículo, preposición, conjunción...)
    if ultima in PALABRAS_COLGANTES:
        return "palabra_colgante"

    # 3. Termina en fragmento de 1 o 2 letras (descartando números y romanos válidos)
    if (
        len(ultima) <= 2
        and not PATRON_SOLO_DIGITOS.match(ultima)
        and ultima not in ROMANOS_CORTOS
        and ultima not in FINALES_CORTOS_VALIDOS
    ):
        return "fragmento_corto"

    # 4. Alcanza exactamente el límite de truncamiento del catálogo (60 caracteres)
    if incluir_exactos_limite and longitud == limite_corte:
        return "limite_caracteres"

    return None


def detectar_truncados(
    directorio: Path | None = None,
    archivo_salida: Path | None = None,
    min_len: int = 55,
    limite_corte: int = 60,
    incluir_exactos_limite: bool = True,
    sin_duplicados: bool = False,
    incluir_motivo: bool = False,
) -> list[dict[str, str]]:
    """Recorre todos los libros<N>.json y genera el listado de títulos truncados."""
    base_dir, archivos_origen = localizar_archivos_libros(directorio)
    if not archivos_origen:
        raise FileNotFoundError(
            f"No se encontraron archivos 'libros<N>.json' en {base_dir}. "
            "Usa --dir para indicar la carpeta donde están."
        )

    if archivo_salida is None:
        archivo_salida = Path.cwd() / "truncados.json"

    registros_inacabados: list[dict[str, str]] = []
    vistos: set[tuple[str, str, str, str]] = set()
    motivos = Counter()
    total_revisados = 0

    for archivo in archivos_origen:
        try:
            datos = iterar_registros_json(archivo)
        except Exception as exc:
            print(f"[ERROR] No se pudo leer {archivo.name}: {exc}")
            continue

        for registro in datos:
            total_revisados += 1
            autor, titulo, fecha, signatura = extraer_campos(registro)
            motivo = analizar_truncamiento(
                titulo,
                min_len=min_len,
                limite_corte=limite_corte,
                incluir_exactos_limite=incluir_exactos_limite,
            )
            if not motivo:
                continue

            clave_dedup = (autor, titulo, fecha, signatura)
            if sin_duplicados and clave_dedup in vistos:
                continue
            vistos.add(clave_dedup)

            item = registro_a_dict(registro)
            if incluir_motivo:
                item["motivo_truncado"] = motivo

            registros_inacabados.append(item)
            motivos[motivo] += 1

    escribir_json_atomico(archivo_salida, registros_inacabados)

    print("=== DETECCIÓN DE TÍTULOS TRUNCADOS ===")
    print(f"Carpeta analizada : {base_dir}")
    print(f"Archivos leídos   : {', '.join(a.name for a in archivos_origen)}")
    print(f"Registros totales : {total_revisados}")
    print(f"Truncados hallados: {len(registros_inacabados)}")
    for motivo, cantidad in motivos.most_common():
        print(f"  - {motivo}: {cantidad}")
    print(f"Archivo generado  : {archivo_salida}")

    return registros_inacabados


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Detecta títulos truncados en archivos libros<N>.json y genera truncados.json."
    )
    parser.add_argument(
        "--dir",
        type=Path,
        default=None,
        help="Carpeta donde se encuentran los libros<N>.json (por defecto: actual o raíz del proyecto).",
    )
    parser.add_argument(
        "--salida",
        "-o",
        type=Path,
        default=Path("truncados.json"),
        help="Ruta del archivo JSON de salida (por defecto: truncados.json).",
    )
    parser.add_argument(
        "--min-len",
        type=int,
        default=55,
        help="Longitud mínima del título para evaluarlo (por defecto: 55).",
    )
    parser.add_argument(
        "--limite-corte",
        type=int,
        default=60,
        help="Longitud del límite de truncamiento del catálogo (por defecto: 60).",
    )
    parser.add_argument(
        "--solo-heuristica",
        action="store_true",
        help="No marca automáticamente todos los títulos de exactamente limite-corte caracteres; usa solo heurísticas lingüísticas.",
    )
    parser.add_argument(
        "--sin-duplicados",
        action="store_true",
        help="Evita incluir registros repetidos exactamente iguales en truncados.json.",
    )
    parser.add_argument(
        "--incluir-motivo",
        action="store_true",
        help="Añade el campo 'motivo_truncado' a cada registro de salida para facilitar la revisión.",
    )
    return parser


def main() -> None:
    args = construir_parser().parse_args()
    detectar_truncados(
        directorio=args.dir,
        archivo_salida=args.salida,
        min_len=args.min_len,
        limite_corte=args.limite_corte,
        incluir_exactos_limite=not args.solo_heuristica,
        sin_duplicados=args.sin_duplicados,
        incluir_motivo=args.incluir_motivo,
    )


if __name__ == "__main__":
    main()
