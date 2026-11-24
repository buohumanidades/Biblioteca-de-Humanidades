"""Sustituye títulos truncados en los archivos libros<N>.json a partir de final.json.

Características de seguridad:
  - Soporta formato compacto [Autor, titulo, fecha, signatura] y formato objeto JSON.
  - Preserva exactamente el formato y compresión original de cada archivo destino.
  - Evita colisiones en signaturas compartidas (HG, HA, LDP, HR, etc.) combinando
    signatura + fecha + autor y verificando que el título nuevo continúe el truncado.
  - Permite simular cambios (--dry-run) y crea copia de seguridad automática antes de escribir.
"""

from __future__ import annotations

import argparse
import shutil
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from comun import (
    asignar_titulo,
    es_formato_compacto,
    escribir_json_atomico,
    extraer_campos,
    iterar_registros_json,
    localizar_archivos_libros,
    normalizar_compacto,
    normalizar_texto,
)


def es_continuacion_valida(titulo_actual: str, titulo_nuevo: str) -> bool:
    """Comprueba si titulo_nuevo es una versión completa/corregida de titulo_actual.

    Compara sin tildes, puntuación ni espacios para tolerar diferencias como ':' o '-'
    eliminados previamente en los JSON del catálogo.
    """
    actual_c = normalizar_compacto(titulo_actual)
    nuevo_c = normalizar_compacto(titulo_nuevo)

    if not actual_c or not nuevo_c:
        return False

    if nuevo_c.startswith(actual_c):
        return True

    # Tolerancia de hasta 2 caracteres finales si el título es largo (ej. ruido al final)
    if len(actual_c) >= 25 and len(nuevo_c) >= len(actual_c) and nuevo_c.startswith(actual_c[:-2]):
        return True

    return False


class IndiceTitulos:
    """Índice jerárquico y seguro de títulos completos procedentes de final.json."""

    def __init__(self) -> None:
        self.por_clave_completa: dict[tuple[str, str, str], list[str]] = defaultdict(list)
        self.por_sig_autor: dict[tuple[str, str], list[str]] = defaultdict(list)
        self.por_signatura: dict[str, list[str]] = defaultdict(list)
        self.total_cargados = 0
        self.omitidos = 0

    @staticmethod
    def _agregar_unico(lista: list[str], valor: str) -> None:
        if valor not in lista:
            lista.append(valor)

    def cargar(self, ruta_final: Path) -> None:
        datos_final = iterar_registros_json(ruta_final)
        for registro in datos_final:
            autor, titulo, fecha, signatura = extraer_campos(registro)
            if not signatura or not titulo:
                self.omitidos += 1
                continue

            sig_k = signatura.upper()
            autor_k = normalizar_texto(autor)
            fecha_k = fecha.strip()

            if fecha_k and autor_k:
                self._agregar_unico(self.por_clave_completa[(sig_k, fecha_k, autor_k)], titulo)
            if autor_k:
                self._agregar_unico(self.por_sig_autor[(sig_k, autor_k)], titulo)
            self._agregar_unico(self.por_signatura[sig_k], titulo)
            self.total_cargados += 1

    def buscar_titulo(
        self,
        autor: str,
        titulo_actual: str,
        fecha: str,
        signatura: str,
        verificar_prefijo: bool = True,
    ) -> tuple[str | None, str]:
        """Devuelve (nuevo_titulo | None, estado).

        Posibles estados:
          - "ok": coincidencia única y validada.
          - "sin_coincidencia": no existe entrada en final.json.
          - "sin_cambios": el título en final.json es idéntico al actual.
          - "descartado_prefijo": había candidato por clave, pero no coincide con el inicio del título.
          - "ambiguo": varios candidatos distintos coinciden y no se puede decidir con seguridad.
        """
        sig_k = signatura.upper()
        autor_k = normalizar_texto(autor)
        fecha_k = fecha.strip()

        # Orden de precisión: (signatura, fecha, autor) -> (signatura, autor) -> signatura
        niveles: list[list[str]] = []
        if fecha_k and autor_k and (sig_k, fecha_k, autor_k) in self.por_clave_completa:
            niveles.append(self.por_clave_completa[(sig_k, fecha_k, autor_k)])
        if autor_k and (sig_k, autor_k) in self.por_sig_autor:
            niveles.append(self.por_sig_autor[(sig_k, autor_k)])
        if not sig_k.startswith("HG") and sig_k in self.por_signatura:
            niveles.append(self.por_signatura[sig_k])

        if not niveles:
            return None, "sin_coincidencia"

        hubo_descarte_prefijo = False

        for candidatos in niveles:
            if verificar_prefijo:
                validos = [c for c in candidatos if es_continuacion_valida(titulo_actual, c)]
                if not validos:
                    hubo_descarte_prefijo = True
                    continue
            else:
                validos = candidatos

            if len(validos) == 1:
                elegido = validos[0]
                if elegido.strip() == titulo_actual.strip():
                    return None, "sin_cambios"
                return elegido, "ok"

            # Si hay varios candidatos válidos, comprobar si todos coinciden tras strip
            unicos = list(dict.fromkeys(c.strip() for c in validos))
            if len(unicos) == 1:
                elegido = unicos[0]
                if elegido == titulo_actual.strip():
                    return None, "sin_cambios"
                return elegido, "ok"

            return None, "ambiguo"

        if hubo_descarte_prefijo:
            return None, "descartado_prefijo"
        return None, "sin_coincidencia"


def crear_backup(archivos: list[Path], carpeta_base: Path) -> Path:
    """Copia los archivos originales a backups/<timestamp>/ antes de modificarlos."""
    marca = datetime.now().strftime("%Y%m%d_%H%M%S")
    destino = carpeta_base / "backups" / marca
    destino.mkdir(parents=True, exist_ok=True)
    for archivo in archivos:
        shutil.copy2(archivo, destino / archivo.name)
    return destino


def sustituir_titulos(
    ruta_final: Path,
    directorio: Path | None = None,
    verificar_prefijo: bool = True,
    dry_run: bool = False,
    hacer_backup: bool = True,
    muestras_log: int = 10,
) -> dict[str, int]:
    """Ejecuta la sustitución controlada de títulos en todos los libros<N>.json."""
    if not ruta_final.exists():
        raise FileNotFoundError(
            f"No se encontró el fichero de títulos completos: {ruta_final.resolve()}"
        )

    base_dir, archivos_destino = localizar_archivos_libros(directorio)
    if not archivos_destino:
        raise FileNotFoundError(
            f"No se encontraron archivos 'libros<N>.json' en {base_dir}. "
            "Usa --dir para indicar la carpeta donde están."
        )

    indice = IndiceTitulos()
    indice.cargar(ruta_final)

    print("=== SUSTITUCIÓN DE TÍTULOS ===")
    print(f"Origen (final.json): {ruta_final.resolve()} ({indice.total_cargados} registros válidos)")
    print(f"Carpeta destino    : {base_dir}")
    print(f"Modo simulación    : {'SÍ (no se modificará ningún archivo)' if dry_run else 'NO'}")
    print(f"Verificar prefijo  : {'SÍ' if verificar_prefijo else 'NO'}")

    if not dry_run and hacer_backup:
        ruta_backup = crear_backup(archivos_destino, base_dir)
        print(f"Copia de seguridad : {ruta_backup}")

    totales = {
        "procesados": 0,
        "sustituidos": 0,
        "sin_cambios": 0,
        "descartados_prefijo": 0,
        "ambiguos": 0,
        "sin_signatura": 0,
    }
    advertencias: list[str] = []
    ejemplos: list[tuple[str, str, str]] = []

    for archivo in archivos_destino:
        c_proc = 0
        c_sust = 0
        c_igual = 0
        c_desc = 0
        c_amb = 0

        try:
            datos_destino: list[Any] = list(iterar_registros_json(archivo))
            compacto = es_formato_compacto(datos_destino)

            for registro in datos_destino:
                c_proc += 1
                autor, titulo_actual, fecha, signatura = extraer_campos(registro)

                if not signatura:
                    totales["sin_signatura"] += 1
                    advertencias.append(
                        f"ADVERTENCIA [{archivo.name}]: registro sin signatura omitido (título: '{titulo_actual[:40]}')."
                    )
                    continue

                nuevo_titulo, estado = indice.buscar_titulo(
                    autor=autor,
                    titulo_actual=titulo_actual,
                    fecha=fecha,
                    signatura=signatura,
                    verificar_prefijo=verificar_prefijo,
                )

                if estado == "ok" and nuevo_titulo is not None:
                    if len(ejemplos) < muestras_log:
                        ejemplos.append((signatura, titulo_actual, nuevo_titulo))
                    asignar_titulo(registro, nuevo_titulo)
                    c_sust += 1
                elif estado == "sin_cambios":
                    c_igual += 1
                elif estado == "descartado_prefijo":
                    c_desc += 1
                    advertencias.append(
                        f"DESCARTADO POR PREFIJO [{archivo.name}] ({signatura}): '{titulo_actual}'"
                    )
                elif estado == "ambiguo":
                    c_amb += 1
                    advertencias.append(
                        f"AMBIGUO [{archivo.name}] ({signatura} | {autor} | {fecha}): '{titulo_actual}'"
                    )

            if not dry_run and c_sust > 0:
                escribir_json_atomico(archivo, datos_destino, compacto=compacto)

            totales["procesados"] += c_proc
            totales["sustituidos"] += c_sust
            totales["sin_cambios"] += c_igual
            totales["descartados_prefijo"] += c_desc
            totales["ambiguos"] += c_amb

            print(
                f"[{archivo.name}] Procesados: {c_proc} | Sustituidos: {c_sust} "
                f"| Iguales: {c_igual} | Descartados: {c_desc} | Ambiguos: {c_amb}"
            )

        except Exception as exc:
            advertencias.append(f"ERROR: Fallo al procesar {archivo.name}. Detalle: {exc}")

    print("\n=== RESUMEN GLOBAL ===")
    print(f"Total procesados         : {totales['procesados']}")
    print(f"Total sustituidos        : {totales['sustituidos']}")
    print(f"Ya estaban actualizados  : {totales['sin_cambios']}")
    print(f"Descartados por prefijo  : {totales['descartados_prefijo']}")
    print(f"Omitidos por ambigüedad  : {totales['ambiguos']}")
    print(f"Omitidos sin signatura   : {totales['sin_signatura']}")

    if ejemplos:
        print(f"\n=== MUESTRA DE CAMBIOS (primeros {len(ejemplos)}) ===")
        for sig, antes, despues in ejemplos:
            print(f"  [{sig}]")
            print(f"    ANTES  : {antes}")
            print(f"    DESPUÉS: {despues}")

    if advertencias:
        limite = 30
        print(f"\n=== ADVERTENCIAS Y ERRORES ({len(advertencias)}) ===")
        for msg in advertencias[:limite]:
            print(f"  - {msg}")
        if len(advertencias) > limite:
            print(f"  ... y {len(advertencias) - limite} advertencias más.")

    return totales


def construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Sustituye títulos truncados en libros<N>.json usando final.json de forma segura."
    )
    parser.add_argument(
        "--final",
        "-f",
        type=Path,
        default=Path("final.json"),
        help="Ruta al archivo JSON con los títulos completos (por defecto: final.json).",
    )
    parser.add_argument(
        "--dir",
        type=Path,
        default=None,
        help="Carpeta donde se encuentran los libros<N>.json (por defecto: actual o raíz del proyecto).",
    )
    parser.add_argument(
        "--dry-run",
        "--simular",
        action="store_true",
        dest="dry_run",
        help="Simula la sustitución y muestra el informe sin modificar ningún archivo.",
    )
    parser.add_argument(
        "--sin-backup",
        action="store_true",
        help="No crea copia de seguridad en backups/ antes de sobrescribir los archivos.",
    )
    parser.add_argument(
        "--sin-verificar-prefijo",
        action="store_true",
        help="Desactiva la comprobación de que el título nuevo continúe el título truncado.",
    )
    parser.add_argument(
        "--muestras",
        type=int,
        default=10,
        help="Número de ejemplos de cambios a mostrar en pantalla (por defecto: 10).",
    )
    return parser


def main() -> None:
    args = construir_parser().parse_args()
    sustituir_titulos(
        ruta_final=args.final,
        directorio=args.dir,
        verificar_prefijo=not args.sin_verificar_prefijo,
        dry_run=args.dry_run,
        hacer_backup=not args.sin_backup,
        muestras_log=args.muestras,
    )


if __name__ == "__main__":
    main()