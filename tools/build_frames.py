"""Arma los tramos del recorrido como secuencias de fotogramas para pintar en <canvas>.

Cada tramo une varias tomas (videos o fotos con un movimiento lento de cámara)
con transiciones suaves y se exporta en dos versiones:
  p = celular vertical (608x1080)
  d = pantalla horizontal (1280x720): el video vertical al centro sobre un fondo
      desenfocado de sí mismo
y en dos capas por versión:
  lo = todo el tramo a media resolución en un solo paquete (llega enseguida)
  hi = paquetes de 24 fotogramas a resolución completa (se piden según dónde
       va el usuario)
Un paquete es la concatenación de los WebP; frames/manifest.json guarda los
tamaños para cortarlos en el navegador.

Uso:  py tools/build_frames.py          (desde la raíz del repo)
Requiere ffmpeg (por defecto el de imageio-ffmpeg).
"""
import json, os, shutil, subprocess, sys, tempfile, glob

FPS = 16
XF = 0.6           # duración de cada transición, en segundos
PACK = 24
Q_HI, Q_LO = 60, 50

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
#   ("f", archivo, segundos, movimiento) = foto de img/; movimiento: in | down | right | pan (fotos anchas)
# Cuando lleguen los videos de Flow, cada foto se cambia por su video (ver PLAN-FLOW.md).
TRAMOS = {
    "s1": [("f", "aerea.jpg", 4.0, "pan"), ("f", "jardin.jpg", 3.2, "pan"), ("f", "piscina-arcos.jpg", 3.6, "pan")],
    "s2": [("f", "arco-piscina.jpg", 3.6, "in"), ("f", "piscina.jpg", 3.2, "down"),
           ("f", "habitacion-1.jpg", 3.2, "in"), ("f", "habitacion-2.jpg", 3.2, "pan"), ("f", "habitacion-3.jpg", 3.0, "right")],
    "s3": [("f", "camarones.jpg", 4.0, "in")],
}
# Transición al entrar a cada toma (la primera no tiene)
TRANS = {"s1": ["fade", "smoothleft"], "s2": ["fade", "zoomin", "fade", "smoothleft"], "s3": []}


def run(args):
    r = subprocess.run([FF, "-y", "-loglevel", "error"] + args)
    if r.returncode:
        sys.exit("ffmpeg falló: " + " ".join(args))


def toma(spec, out):
    """Normaliza una toma a 720x1280, FPS fotogramas por segundo, sin audio."""
    base = "scale=720:1280:force_original_aspect_ratio=increase,crop=720:1280,setsar=1"
    if spec[0] == "v":
        run(["-i", os.path.join(SRC, spec[1]), "-an", "-vf", f"{base},fps={FPS}",
             "-c:v", "libx264", "-crf", "10", "-pix_fmt", "yuv420p", out])
        return
    _, name, secs, mov = spec
    n = int(round(secs * FPS))
    if mov == "pan":
        # Foto ancha: la cámara la recorre de izquierda a derecha (a 2x para que el paso sea suave)
        vf = (f"scale=-2:2560,crop=1440:2560:x='(iw-1440)*min(t/{secs},1)':y=0,"
              f"scale=720:1280,fps={FPS},setsar=1")
        run(["-loop", "1", "-framerate", str(FPS), "-i", os.path.join(IMG, name), "-vf", vf,
             "-frames:v", str(n), "-c:v", "libx264", "-crf", "10", "-pix_fmt", "yuv420p", out])
        return
    # zoompan trabaja sobre una imagen grande para que el movimiento no tiemble
    z = {"in": f"1+0.16*on/{n}", "down": f"1.18-0.12*on/{n}", "right": "1.14"}[mov]
    x = {"in": "iw/2-(iw/zoom/2)", "down": "iw/2-(iw/zoom/2)", "right": f"(iw-iw/zoom)*on/{n}"}[mov]
    y = {"in": "ih/2-(ih/zoom/2)", "down": f"(ih-ih/zoom)*(0.25+0.5*on/{n})", "right": "ih/2-(ih/zoom/2)"}[mov]
    vf = (f"scale=2160:3840:force_original_aspect_ratio=increase,crop=2160:3840,"
          f"zoompan=z='{z}':x='{x}':y='{y}':d={n}:s=720x1280:fps={FPS},setsar=1")
    run(["-loop", "1", "-i", os.path.join(IMG, name), "-vf", vf, "-frames:v", str(n),
         "-c:v", "libx264", "-crf", "10", "-pix_fmt", "yuv420p", out])


def duracion(path):
    r = subprocess.run([FF, "-i", path], capture_output=True, text=True)
    for line in r.stderr.splitlines():
        if "Duration:" in line:
            h, m, s = line.split("Duration:")[1].split(",")[0].strip().split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)
    return 0


def tramo(key, tmp):
    partes = []
    for i, spec in enumerate(TRAMOS[key]):
        p = os.path.join(tmp, f"{key}-{i}.mp4")
        toma(spec, p)
        partes.append(p)
    master = os.path.join(tmp, f"{key}.mp4")
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
                "-c:v", "libx264", "-crf", "10", "-pix_fmt", "yuv420p", master])
    return master


def exportar(master, vf, carpeta, q):
    os.makedirs(carpeta, exist_ok=True)
    run(["-i", master, "-vf", vf, "-c:v", "libwebp", "-quality", str(q),
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


TIERS = {
    # nombre: (ancho, alto, filtro hi, filtro lo)
    "p": (608, 1080, "scale=608:1080", "scale=304:540"),
    "d": (1280, 720,
          "split[a][b];[a]scale=1280:720:force_original_aspect_ratio=increase,crop=1280:720,"
          "gblur=sigma=28,eq=brightness=-0.16:saturation=1.1[bg];[b]scale=-2:720[fg];"
          "[bg][fg]overlay=(W-w)/2:0",
          "split[a][b];[a]scale=640:360:force_original_aspect_ratio=increase,crop=640:360,"
          "gblur=sigma=14,eq=brightness=-0.16:saturation=1.1[bg];[b]scale=-2:360[fg];"
          "[bg][fg]overlay=(W-w)/2:0"),
}


def main():
    if os.path.isdir(OUT):
        shutil.rmtree(OUT)
    os.makedirs(OUT)
    manifest = {}
    tmp = tempfile.mkdtemp()
    try:
        for key in TRAMOS:
            master = tramo(key, tmp)
            for t, (w, h, vf_hi, vf_lo) in TIERS.items():
                d = os.path.join(OUT, t)
                os.makedirs(d, exist_ok=True)
                hi = exportar(master, vf_hi, os.path.join(tmp, f"{key}{t}hi"), Q_HI)
                lo = exportar(master, vf_lo, os.path.join(tmp, f"{key}{t}lo"), Q_LO)
                n = min(len(hi), len(lo))
                lo_pack = empacar(lo[:n], d, f"{key}-lo", n)[0]
                lo_pack["w"], lo_pack["h"] = w // 2, h // 2
                manifest.setdefault(t, {})[key] = {
                    "n": n, "w": w, "h": h,
                    "lo": lo_pack,
                    "hi": empacar(hi[:n], d, f"{key}-hi", PACK),
                }
                tot = sum(lo_pack["len"]) + sum(sum(p["len"]) for p in manifest[t][key]["hi"])
                print(f"{key} {t}: {n} fotogramas, {tot/1e6:.1f} MB")
            # Póster del primer fotograma para la carga inicial
            run(["-i", master, "-frames:v", "1", "-vf", "scale=608:1080", "-q:v", "5",
                 os.path.join(OUT, f"{key}-poster.jpg")])
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    json.dump(manifest, open(os.path.join(OUT, "manifest.json"), "w"), separators=(",", ":"))


if __name__ == "__main__":
    main()
