"""Detecta y extrae títulos probablemente truncados de los archivos libros<N>.json.

Soporta tanto el formato compacto [Autor, titulo, fecha, signatura] como
el formato de objetos {"Autor", "titulo", "fecha", "signatura"}.
"""

from __future__ import annotations

import argparse
import re
from collections import Counter
from pathlib import Path

from comun import (
    escribir_json_atomico,
    extraer_campos,
    iterar_registros_json,
    localizar_archivos_libros,
    registro_a_dict,
)

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


def analizar_truncamiento(
    titulo: str,
    min_len: int = 55,
    limite_corte: int = 60,
    incluir_exactos_limite: bool = True,
) -> str | None:
    """Devuelve el motivo si el título parece truncado, o None si parece completo."""
    titulo_limpio = titulo.strip()
    if len(titulo_limpio) < min_len:
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
    if incluir_exactos_limite and len(titulo_limpio) >= limite_corte:
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

    escribir_json_atomico(archivo_salida, registros_inacabados, compacto=False)

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
        help="No marca automáticamente todos los títulos de >= limite-corte; usa solo heurísticas lingüísticas.",
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