"""Optimiza imágenes y archivos JSON del catálogo sin perder compatibilidad."""

import json
import os
import re
import unicodedata
from PIL import Image

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def normalizar_para_comparacion(texto):
    """Replica normalizarParaComparacion de Grados/grado3.html."""
    if not texto:
        return []
    limpio = re.sub(r"<[^>]*>?", "", str(texto))
    limpio = "".join(
        ch
        for ch in unicodedata.normalize("NFD", limpio.lower())
        if unicodedata.category(ch) != "Mn"
    )
    limpio = re.sub(r"[^a-z0-9\s]", " ", limpio)
    return [p for p in limpio.split(" ") if p.strip()]


def extraer_campos_libro(item):
    """Soporta tanto objetos JSON como arrays compactos [autor, titulo, fecha, signatura]."""
    if isinstance(item, list):
        autor = str(item[0] if len(item) > 0 else "").strip()
        titulo = str(item[1] if len(item) > 1 else "").strip()
        fecha = str(item[2] if len(item) > 2 else "").strip()
        signatura = str(item[3] if len(item) > 3 else "").strip()
        return autor, titulo, fecha, signatura
    autor = str(item.get("autor") or item.get("Autor") or item.get("AUTOR") or "").strip()
    titulo = str(item.get("titulo") or item.get("Titulo") or item.get("TITULO") or "").strip()
    fecha = str(item.get("fecha") or item.get("Fecha") or item.get("FECHA") or "").strip()
    signatura = str(item.get("signatura") or item.get("Signatura") or item.get("SIGNATURA") or "").strip()
    return autor, titulo, fecha, signatura


def optimizar_imagenes():
    """Convierte banners PNG pesados a WebP y recomprime los JPG existentes."""
    conversiones = [
        ("sala.png", "sala.webp", 1600, 82),
        (os.path.join("imagenes", "buobh.png"), os.path.join("imagenes", "buobh.webp"), 1600, 82),
    ]
    for src_rel, dst_rel, max_w, quality in conversiones:
        src = os.path.join(ROOT, src_rel)
        dst = os.path.join(ROOT, dst_rel)
        if not os.path.exists(src):
            continue
        with Image.open(src) as im:
            w, h = im.size
            if w > max_w:
                new_h = max(1, int(h * (max_w / w)))
                im = im.resize((max_w, new_h), Image.Resampling.LANCZOS)
            im.save(dst, "WEBP", quality=quality, method=6)
        print(f"Imagen convertida: {src_rel} ({os.path.getsize(src)} B) -> {dst_rel} ({os.path.getsize(dst)} B)")
        os.remove(src)

    jpgs = [
        (os.path.join("imagenes", "libros.jpg"), 1200, 82),
        (os.path.join("imagenes", "tiempo.jpg"), 1054, 82),
        (os.path.join("Grados", "libro.jpg"), 1195, 82),
    ]
    for rel, max_w, quality in jpgs:
        path = os.path.join(ROOT, rel)
        if not os.path.exists(path):
            continue
        before = os.path.getsize(path)
        with Image.open(path) as im:
            if im.mode != "RGB":
                im = im.convert("RGB")
            w, h = im.size
            if w > max_w:
                new_h = max(1, int(h * (max_w / w)))
                im = im.resize((max_w, new_h), Image.Resampling.LANCZOS)
            im.save(path, "JPEG", quality=quality, optimize=True, progressive=True)
        after = os.path.getsize(path)
        print(f"JPG optimizado: {rel} ({before} B -> {after} B)")


def pre_cruzar_grado3(libros_brutos):
    """Precalcula disponibilidad en sala para Grados/grado3.json."""
    grado_path = os.path.join(ROOT, "Grados", "grado3.json")
    if not os.path.exists(grado_path):
        return

    with open(grado_path, "r", encoding="utf-8") as fh:
        grado_data = json.load(fh)

    by_full = {}
    by_9 = {}
    for item in libros_brutos:
        _, titulo, _, signatura = extraer_campos_libro(item)
        words = normalizar_para_comparacion(titulo)
        if not words:
            continue
        full_k = " ".join(words)
        by_full.setdefault(full_k, []).append((signatura, titulo))
        if len(words) > 9:
            k9 = " ".join(words[:9])
            by_9.setdefault(k9, []).append((signatura, titulo))

    for entry in grado_data:
        obra_titulo = entry.get("Obra/Título", "")
        words = normalizar_para_comparacion(obra_titulo)
        if not words:
            entry["disponibilidad"] = []
            continue
        matches = by_9.get(" ".join(words[:9]), []) if len(words) > 9 else by_full.get(" ".join(words), [])
        mapa = {}
        for sig, tit in matches:
            key = f"{sig}|{tit}"
            if key in mapa:
                mapa[key]["ejemplares"] += 1
            else:
                mapa[key] = {"signatura": sig, "titulo": tit, "ejemplares": 1}
        entry["disponibilidad"] = list(mapa.values())

    before = os.path.getsize(grado_path)
    with open(grado_path, "w", encoding="utf-8") as fh:
        json.dump(grado_data, fh, ensure_ascii=False, separators=(",", ":"))
    after = os.path.getsize(grado_path)
    print(f"Grados/grado3.json pre-cruzado y minificado: {before} B -> {after} B")


def optimizar_libros_principales():
    """Compacta libros1..3.json en arrays [autor, titulo, fecha, signatura] sin EXP ni duplicados."""
    rutas = ["libros1.json", "libros2.json", "libros3.json"]
    libros_brutos = []
    before_total = 0
    for rel in rutas:
        p = os.path.join(ROOT, rel)
        if os.path.exists(p):
            before_total += os.path.getsize(p)
            with open(p, "r", encoding="utf-8") as fh:
                libros_brutos.extend(json.load(fh))

    pre_cruzar_grado3(libros_brutos)

    compactos = []
    vistos = set()
    for item in libros_brutos:
        autor, titulo, fecha, sig = extraer_campos_libro(item)
        if sig.upper().startswith("EXP"):
            continue
        key = (sig, titulo)
        if key not in vistos:
            vistos.add(key)
            compactos.append([autor, titulo, fecha, sig])

    chunk_size = (len(compactos) + len(rutas) - 1) // len(rutas)
    after_total = 0
    for idx, rel in enumerate(rutas):
        trozo = compactos[idx * chunk_size : (idx + 1) * chunk_size]
        p = os.path.join(ROOT, rel)
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(trozo, fh, ensure_ascii=False, separators=(",", ":"))
        after_total += os.path.getsize(p)

    print(f"libros1..3.json compactados: {before_total/1024/1024:.2f} MiB -> {after_total/1024/1024:.2f} MiB ({len(compactos)} registros)")


def optimizar_grados_libros():
    """Deduplica y compacta Grados/libros_1..2.json en [signatura, titulo]."""
    rutas = [os.path.join("Grados", "libros_1.json"), os.path.join("Grados", "libros_2.json")]
    brutos = []
    before_total = 0
    for rel in rutas:
        p = os.path.join(ROOT, rel)
        if os.path.exists(p):
            before_total += os.path.getsize(p)
            with open(p, "r", encoding="utf-8") as fh:
                brutos.extend(json.load(fh))

    compactos = []
    vistos = set()
    for item in brutos:
        if isinstance(item, list):
            sig = str(item[0] if len(item) > 0 else "").strip()
            tit = str(item[1] if len(item) > 1 else "").strip()
        else:
            sig = str(item.get("signatura") or "").strip()
            tit = str(item.get("titulo") or "").strip()
        key = (sig, tit)
        if key not in vistos:
            vistos.add(key)
            compactos.append([sig, tit])

    chunk_size = (len(compactos) + len(rutas) - 1) // len(rutas)
    after_total = 0
    for idx, rel in enumerate(rutas):
        trozo = compactos[idx * chunk_size : (idx + 1) * chunk_size]
        p = os.path.join(ROOT, rel)
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(trozo, fh, ensure_ascii=False, separators=(",", ":"))
        after_total += os.path.getsize(p)

    print(f"Grados/libros_1..2.json compactados: {before_total/1024/1024:.2f} MiB -> {after_total/1024/1024:.2f} MiB ({len(compactos)} registros)")


def minificar_auxiliares_y_manifest():
    """Minifica JSON pequeños y genera manifest.json para carga paralela sin 404."""
    for rel in ["signaturas.json", "todassignaturas.json", "plano.json", os.path.join("Grados", "guiadocente.json")]:
        p = os.path.join(ROOT, rel)
        if not os.path.exists(p):
            continue
        before = os.path.getsize(p)
        with open(p, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
        print(f"Minificado {rel}: {before} B -> {os.path.getsize(p)} B")

    manifest_path = os.path.join(ROOT, "manifest.json")
    manifest = {
        "libros": ["libros1.json", "libros2.json", "libros3.json"],
        "gradosLibros": ["Grados/libros_1.json", "Grados/libros_2.json"],
    }
    with open(manifest_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, separators=(",", ":"))
    print("Creado manifest.json")


if __name__ == "__main__":
    optimizar_imagenes()
    optimizar_libros_principales()
    optimizar_grados_libros()
    minificar_auxiliares_y_manifest()
