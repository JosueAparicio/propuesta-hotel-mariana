"""Arma los tramos del recorrido como secuencias de fotogramas para pintar en <canvas>.

Cada tramo une varias tomas (videos o fotos con movimiento de cámara) con
transiciones suaves. Se arma dos veces, una por formato de pantalla:
  p = celular vertical   (maestro 1080x1920, se exporta a 864x1536)
  d = pantalla horizontal (maestro 1920x1080, se exporta a 1920x1080)
Si una foto o un video no tiene la forma de la pantalla (una panorámica en el
celular, una foto vertical en la computadora), se muestra completo y nítido
sobre un fondo desenfocado de sí mismo, en vez de agrandarlo hasta llenar.

Cada formato tiene dos capas:
  lo = todo el tramo a media resolución en un solo paquete (llega enseguida)
  hi = paquetes de 24 fotogramas a resolución completa
Un paquete es la concatenación de los WebP; frames/manifest.json guarda los
tamaños para cortarlos en el navegador.

Uso:  py tools/build_frames.py          (desde la raíz del repo)
Requiere ffmpeg (por defecto el de imageio-ffmpeg) y Pillow.
"""
import json, os, shutil, subprocess, sys, tempfile, glob
from PIL import Image, ImageFilter, ImageEnhance

FPS = 16
XF = 0.6           # duración de cada transición, en segundos
PACK = 24
FIT_RATIO = 1.5    # si la forma de la toma y la de la pantalla difieren más que esto, se usa "encuadre completo"

try:
    import imageio_ffmpeg
    FF = imageio_ffmpeg.get_ffmpeg_exe()
except ImportError:
    FF = "ffmpeg"

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "video-src")
IMG = os.path.join(ROOT, "img")
OUT = os.path.join(ROOT, "frames")

# Tomas de cada tramo, en orden.
#   ("v", archivo)                      = video de video-src/
#   ("f", archivo, segundos, movimiento) = foto de img/; movimiento: in | out | down | right
# Cuando lleguen los videos de Flow, cada foto se cambia por su video (ver PLAN-FLOW.md).
TRAMOS = {
    "s1": [("f", "aerea.jpg", 4.0, "in"), ("f", "jardin.jpg", 3.2, "right"), ("f", "piscina-arcos.jpg", 3.6, "in")],
    "s2": [("f", "arco-piscina.jpg", 3.6, "in"), ("f", "piscina.jpg", 3.2, "down"),
           ("f", "habitacion-1.jpg", 3.2, "in"), ("f", "habitacion-2.jpg", 3.2, "right"), ("f", "habitacion-3.jpg", 3.0, "out")],
    "s3": [("f", "camarones.jpg", 4.0, "in")],
}
# Transición al entrar a cada toma (la primera no tiene)
TRANS = {"s1": ["fade", "smoothleft"], "s2": ["fade", "zoomin", "fade", "smoothleft"], "s3": []}

# formato: (ancho maestro, alto maestro, ancho exportado, alto exportado, calidad hi, calidad lo)
TIERS = {"p": (1080, 1920, 864, 1536, 72, 60), "d": (1920, 1080, 1920, 1080, 70, 58)}


def run(args):
    r = subprocess.run([FF, "-y", "-loglevel", "error"] + args)
    if r.returncode:
        sys.exit("ffmpeg falló: " + " ".join(args))


def lienzo(path, W, H, tmp):
    """Devuelve una imagen 2W x 2H lista para el movimiento de cámara: la foto
    llenando la pantalla si tiene su forma, o completa sobre su propio fondo
    desenfocado si no la tiene."""
    im = Image.open(path).convert("RGB")
    w, h = im.size
    big = (2 * W, 2 * H)
    ar_img, ar_scr = w / h, W / H
    out = os.path.join(tmp, os.path.basename(path) + f".{W}x{H}.png")
    if max(ar_img, ar_scr) / min(ar_img, ar_scr) <= FIT_RATIO:
        s = max(big[0] / w, big[1] / h)
        im = im.resize((round(w * s), round(h * s)), Image.LANCZOS)
        l, t = (im.width - big[0]) // 2, (im.height - big[1]) // 2
        im.crop((l, t, l + big[0], t + big[1])).save(out)
        return out
    s = max(big[0] / w, big[1] / h)
    bg = im.resize((round(w * s), round(h * s)), Image.LANCZOS)
    l, t = (bg.width - big[0]) // 2, (bg.height - big[1]) // 2
    bg = bg.crop((l, t, l + big[0], t + big[1])).filter(ImageFilter.GaussianBlur(60))
    bg = ImageEnhance.Brightness(bg).enhance(0.55)
    # La foto completa, centrada sobre su propio fondo desenfocado
    fit = min(big[0] * 0.94 / w, big[1] * 0.94 / h)
    fg = im.resize((round(w * fit), round(h * fit)), Image.LANCZOS)
    # En pantalla vertical la foto va arriba (centro al 36 %), así el texto de abajo queda sobre el fondo
    cy = 0.36 if H > W else 0.5
    top = max(0, min(big[1] - fg.height, round(big[1] * cy - fg.height / 2)))
    bg.paste(fg, ((big[0] - fg.width) // 2, top))
    bg.save(out)
    return out


def toma(spec, out, W, H, tmp):
    if spec[0] == "v":
        ar_v = 9 / 16
        if max(ar_v, W / H) / min(ar_v, W / H) <= FIT_RATIO:
            vf = f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS},setsar=1"
        else:
            vf = (f"split[a][b];[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                  f"gblur=sigma=30,eq=brightness=-0.18[bg];[b]scale=-2:{H}[fg];[bg][fg]overlay=(W-w)/2:0,"
                  f"fps={FPS},setsar=1")
        run(["-i", os.path.join(SRC, spec[1]), "-an", "-filter_complex", vf,
             "-c:v", "libx264", "-crf", "12", "-pix_fmt", "yuv420p", out])
        return
    _, name, secs, mov = spec
    n = int(round(secs * FPS))
    src = lienzo(os.path.join(IMG, name), W, H, tmp)
    # zoompan sobre el lienzo de 2x para que el movimiento no tiemble
    z = {"in": f"1+0.12*on/{n}", "out": f"1.12-0.12*on/{n}", "down": f"1.14-0.08*on/{n}", "right": "1.12"}[mov]
    x = {"in": "iw/2-(iw/zoom/2)", "out": "iw/2-(iw/zoom/2)", "down": "iw/2-(iw/zoom/2)",
         "right": f"(iw-iw/zoom)*on/{n}"}[mov]
    y = {"in": "ih*0.4-(ih/zoom*0.4)", "out": "ih*0.4-(ih/zoom*0.4)", "down": f"(ih-ih/zoom)*(0.2+0.6*on/{n})",
         "right": "ih/2-(ih/zoom/2)"}[mov]
    vf = f"zoompan=z='{z}':x='{x}':y='{y}':d={n}:s={W}x{H}:fps={FPS},setsar=1"
    run(["-i", src, "-vf", vf, "-frames:v", str(n), "-c:v", "libx264", "-crf", "12", "-pix_fmt", "yuv420p", out])


def duracion(path):
    r = subprocess.run([FF, "-i", path], capture_output=True, text=True)
    for line in r.stderr.splitlines():
        if "Duration:" in line:
            h, m, s = line.split("Duration:")[1].split(",")[0].strip().split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)
    return 0


def tramo(key, W, H, tmp):
    partes = []
    for i, spec in enumerate(TRAMOS[key]):
        p = os.path.join(tmp, f"{key}-{W}-{i}.mp4")
        toma(spec, p, W, H, tmp)
        partes.append(p)
    master = os.path.join(tmp, f"{key}-{W}.mp4")
    if len(partes) == 1:
        shutil.copy(partes[0], master)
        return master
    args, chain, acc, prev = [], [], 0.0, "0:v"
    for p in partes:
        args += ["-i", p]
    for i in range(1, len(partes)):
        acc += duracion(partes[i - 1]) - XF
        lab = f"x{i}"
        chain.append(f"[{prev}][{i}:v]xfade=transition={TRANS[key][i-1]}:duration={XF}:offset={acc:.3f}[{lab}]")
        prev = lab
    run(args + ["-filter_complex", ";".join(chain), "-map", f"[{prev}]",
                "-c:v", "libx264", "-crf", "12", "-pix_fmt", "yuv420p", master])
    return master


def exportar(master, w, h, carpeta, q):
    os.makedirs(carpeta, exist_ok=True)
    run(["-i", master, "-vf", f"scale={w}:{h}:flags=lanczos", "-c:v", "libwebp", "-quality", str(q),
         "-compression_level", "6", os.path.join(carpeta, "%04d.webp")])
    return sorted(glob.glob(os.path.join(carpeta, "*.webp")))


def empacar(files, dest_dir, prefix, size):
    packs = []
    for k in range(0, len(files), size):
        chunk = files[k:k + size]
        name = f"{prefix}-{k // size:02d}.bin"
        lens = []
        with open(os.path.join(dest_dir, name), "wb") as fh:
            for f in chunk:
                b = open(f, "rb").read()
                fh.write(b)
                lens.append(len(b))
        packs.append({"file": name, "idx": list(range(k, k + len(chunk))), "len": lens})
    return packs


def main():
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    os.makedirs(OUT)
    manifest = {}
    tmp = tempfile.mkdtemp()
    try:
        for key in TRAMOS:
            for t, (MW, MH, w, h, qh, ql) in TIERS.items():
                master = tramo(key, MW, MH, tmp)
                d = os.path.join(OUT, t)
                os.makedirs(d, exist_ok=True)
                hi = exportar(master, w, h, os.path.join(tmp, f"{key}{t}hi"), qh)
                lo = exportar(master, w // 2, h // 2, os.path.join(tmp, f"{key}{t}lo"), ql)
                n = min(len(hi), len(lo))
                lo_pack = empacar(lo[:n], d, f"{key}-lo", n)[0]
                lo_pack["w"], lo_pack["h"] = w // 2, h // 2
                manifest.setdefault(t, {})[key] = {"n": n, "w": w, "h": h, "lo": lo_pack,
                                                   "hi": empacar(hi[:n], d, f"{key}-hi", PACK)}
                tot = sum(lo_pack["len"]) + sum(sum(p["len"]) for p in manifest[t][key]["hi"])
                print(f"{key} {t}: {n} fotogramas, {tot/1e6:.1f} MB")
                # Póster del primer fotograma para la carga inicial
                run(["-i", master, "-frames:v", "1", "-vf", f"scale={w}:{h}", "-q:v", "4",
                     os.path.join(OUT, f"{key}-poster-{t}.jpg")])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    json.dump(manifest, open(os.path.join(OUT, "manifest.json"), "w"), separators=(",", ":"))


if __name__ == "__main__":
    main()
