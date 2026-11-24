"""Utilidades compartidas para detectar y sustituir títulos truncados."""

from __future__ import annotations

import json
import os
import re
import tempfile
import unicodedata
from pathlib import Path
from typing import Any, Iterable

PATRON_LIBROS = re.compile(r"^libros\d+\.json$", re.IGNORECASE)

CLAVES_AUTOR = ("Autor", "autor", "AUTOR")
CLAVES_TITULO = ("titulo", "Titulo", "TITULO", "título", "Título")
CLAVES_FECHA = ("fecha", "Fecha", "FECHA")
CLAVES_SIGNATURA = ("signatura", "Signatura", "SIGNATURA")


def _primera_clave(registro: dict[str, Any], claves: tuple[str, ...]) -> tuple[str | None, str]:
    """Devuelve (nombre_clave_encontrada, valor_str) para un diccionario."""
    for clave in claves:
        if clave in registro and registro[clave] is not None:
            return clave, str(registro[clave]).strip()
    return None, ""


def extraer_campos(registro: Any) -> tuple[str, str, str, str]:
    """Extrae (autor, titulo, fecha, signatura) tanto de listas compactas como de dicts.

    Formatos soportados:
      - Lista compacta: [autor, titulo, fecha, signatura]
      - Objeto JSON: {"Autor": ..., "titulo": ..., "fecha": ..., "signatura": ...}
    """
    if isinstance(registro, list):
        autor = str(registro[0]).strip() if len(registro) > 0 and registro[0] is not None else ""
        titulo = str(registro[1]).strip() if len(registro) > 1 and registro[1] is not None else ""
        fecha = str(registro[2]).strip() if len(registro) > 2 and registro[2] is not None else ""
        signatura = str(registro[3]).strip() if len(registro) > 3 and registro[3] is not None else ""
        return autor, titulo, fecha, signatura

    if isinstance(registro, dict):
        _, autor = _primera_clave(registro, CLAVES_AUTOR)
        _, titulo = _primera_clave(registro, CLAVES_TITULO)
        _, fecha = _primera_clave(registro, CLAVES_FECHA)
        _, signatura = _primera_clave(registro, CLAVES_SIGNATURA)
        return autor, titulo, fecha, signatura

    raise TypeError(f"Formato de registro no soportado: {type(registro).__name__}")


def asignar_titulo(registro: Any, nuevo_titulo: str) -> None:
    """Actualiza el título in-place respetando el formato original (lista o dict)."""
    if isinstance(registro, list):
        if len(registro) < 2:
            raise ValueError("El registro en formato lista no tiene posición para el título.")
        registro[1] = nuevo_titulo
        return

    if isinstance(registro, dict):
        clave, _ = _primera_clave(registro, CLAVES_TITULO)
        registro[clave or "titulo"] = nuevo_titulo
        return

    raise TypeError(f"Formato de registro no soportado: {type(registro).__name__}")


def registro_a_dict(registro: Any) -> dict[str, str]:
    """Convierte cualquier registro soportado a un diccionario homogéneo."""
    autor, titulo, fecha, signatura = extraer_campos(registro)
    return {
        "Autor": autor,
        "titulo": titulo,
        "fecha": fecha,
        "signatura": signatura,
    }


def normalizar_texto(texto: str) -> str:
    """Normaliza texto para comparaciones robustas (sin tildes, minúsculas, alfanumérico)."""
    if not texto:
        return ""
    sin_tildes = "".join(
        ch
        for ch in unicodedata.normalize("NFD", str(texto).lower())
        if unicodedata.category(ch) != "Mn"
    )
    solo_alfanum = re.sub(r"[^a-z0-9\s]", " ", sin_tildes)
    return re.sub(r"\s+", " ", solo_alfanum).strip()


def normalizar_compacto(texto: str) -> str:
    """Normalización sin espacios ni puntuación para tolerar guiones/apóstrofos eliminados."""
    return normalizar_texto(texto).replace(" ", "")


def localizar_archivos_libros(directorio: Path | None = None) -> tuple[Path, list[Path]]:
    """Localiza libros1.json, libros2.json, ... en el directorio indicado o en el padre.

    Si no se especifica directorio:
      1. Busca en el directorio actual (cwd).
      2. Si no hay coincidencias, busca en el directorio padre del script (raíz del repo).
    """
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


def _listar_libros(base: Path) -> list[Path]:
    if not base.is_dir():
        return []

    def clave_orden(p: Path) -> tuple[int, str]:
        m = re.search(r"(\d+)", p.name)
        return (int(m.group(1)) if m else 0, p.name.lower())

    archivos = [
        p for p in base.iterdir() if p.is_file() and PATRON_LIBROS.match(p.name)
    ]
    return sorted(archivos, key=clave_orden)


def es_formato_compacto(datos: list[Any]) -> bool:
    """Devuelve True si el JSON usa formato compacto de listas [autor, titulo, fecha, sig]."""
    return bool(datos) and isinstance(datos[0], list)


def escribir_json_atomico(
    ruta: Path,
    datos: Any,
    compacto: bool = False,
) -> None:
    """Escribe un archivo JSON de forma atómica para evitar corrupción en caso de error."""
    ruta = Path(ruta)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    fd, ruta_tmp = tempfile.mkstemp(
        prefix=f".{ruta.name}.",
        suffix=".tmp",
        dir=str(ruta.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            if compacto:
                json.dump(datos, fh, ensure_ascii=False, separators=(",", ":"))
            else:
                json.dump(datos, fh, ensure_ascii=False, indent=4)
        os.replace(ruta_tmp, ruta)
    except Exception:
        if os.path.exists(ruta_tmp):
            os.remove(ruta_tmp)
        raise


def iterar_registros_json(ruta: Path) -> Iterable[Any]:
    """Carga y devuelve la lista de registros de un archivo JSON."""
    with open(ruta, "r", encoding="utf-8") as fh:
        datos = json.load(fh)
    if not isinstance(datos, list):
        raise ValueError(f"Se esperaba una lista JSON en {ruta}, se obtuvo {type(datos).__name__}.")
    return datos
