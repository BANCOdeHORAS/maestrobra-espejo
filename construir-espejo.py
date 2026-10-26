#!/usr/bin/env python3
"""
MaestrObra · ESPEJO EN GITHUB PAGES (26-10-2026, B2)

Lo ejecuta GitHub (Actions) cada media hora. Copia la web publicada en Cloudflare a GitHub Pages
(app.mandobra.es), que va por otra red (Fastly) y no cae cuando LaLiga hace bloquear Cloudflare en España.
GitHub está fuera de España, así que puede leer la web aunque aquí esté bloqueada.

  1) Lee /espejo-lista.json de la web publicada (la escribe PUBLICAR.bat): versión y archivos con su huella.
  2) Baja cada archivo y comprueba su huella.
  3) Cambia en las páginas las librerías de Cloudflare / jsDelivr por copias en /lib/vendor/ (como espejo.py).
  4) Deja la web lista en ./sitio para que GitHub la publique, con el CNAME del dominio.

Variables: ORIGEN (web o carpeta deploy local para probar), DOMINIO (app.mandobra.es), SALIDA (sitio).
  python construir-espejo.py hace-falta   -> escribe hace_falta=si|no en $GITHUB_OUTPUT (versión distinta)
  python construir-espejo.py              -> construye ./sitio
"""
import hashlib, json, os, re, shutil, sys, time, urllib.parse, urllib.request
from pathlib import Path

ORIGEN = os.environ.get("ORIGEN", "https://mandobra.salvador-4f1.workers.dev").rstrip("/")
DOMINIO = os.environ.get("DOMINIO", "app.mandobra.es")
SALIDA = Path(os.environ.get("SALIDA", "sitio"))
LOCAL = not ORIGEN.startswith("http")

VENDOR = {  # lo que hay en la web -> copia local en el espejo
    "https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2.116.0": "/lib/vendor/supabase-js-2.116.0.js",
    "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js": "/lib/vendor/pdf.worker.min.js",
    "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js": "/lib/vendor/pdf.min.js",
    "https://cdnjs.cloudflare.com/ajax/libs/xlsx/0.18.5/xlsx.full.min.js": "/lib/vendor/xlsx.full.min.js",
}
VENDOR_BAJAR = {}  # se bajan de la misma dirección que usa la web: así el navegador recibe exactamente lo mismo
SOLO_CLOUDFLARE = {"_headers", "_redirects", "_routes.json", "wrangler.toml"}
CAMBIOS_RE = [  # enlaces fijos a la web de Cloudflare que en el espejo apuntan al propio espejo
    (re.compile(r'(=\s*)"https://mandobra\.salvador-4f1\.workers\.dev"\s*;'), r'\1(location.protocol==="file:"?"https://mandobra.salvador-4f1.workers.dev":location.origin);'),
    (re.compile(r':"https://mandobra\.salvador-4f1\.workers\.dev/'), r':location.origin+"/'),
]


def bajar(url, intentos=4):
    for i in range(intentos):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "MaestrObra-espejo", "Cache-Control": "no-cache"})
            with urllib.request.urlopen(req, timeout=90) as r:
                return r.read()
        except Exception as e:
            if i == intentos - 1:
                raise SystemExit(f"NO SE PUEDE BAJAR {url}: {e}")
            time.sleep(3 * (i + 1))


def leer(rel):
    if LOCAL:
        return (Path(ORIGEN) / rel).read_bytes()
    return bajar(ORIGEN + "/" + urllib.parse.quote(rel) + ("?z=%d" % time.time() if rel.endswith(".json") else ""))


def version_de(url):
    try:
        return json.loads(bajar(url + "/version.json?z=%d" % time.time(), 1).decode("utf-8")).get("version")
    except BaseException:
        return None


def hace_falta():
    web = json.loads(leer("version.json")).get("version")
    esp = version_de("https://" + DOMINIO)
    si = (web != esp) or os.environ.get("FORZAR") == "1"
    print(f"Web publicada: {web} · espejo: {esp} -> {'hay que copiar' if si else 'ya esta al dia'}")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"hace_falta={'si' if si else 'no'}\nversion={web}\n")


def construir():
    lista = json.loads(leer("espejo-lista.json"))
    if SALIDA.exists():
        shutil.rmtree(SALIDA)
    SALIDA.mkdir(parents=True)
    total = 0
    for f in lista["files"]:
        if f["p"] in SOLO_CLOUDFLARE:  # ajustes de Cloudflare: la web no los sirve y en Pages no valen
            continue
        datos = leer(f["p"])
        if hashlib.sha1(datos).hexdigest() != f["h"]:
            raise SystemExit(f"Huella distinta en {f['p']}: la web ha cambiado a mitad de copia. Se repite en la siguiente vuelta.")
        dst = SALIDA / f["p"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(datos)
        total += len(datos)
    (SALIDA / "lib" / "vendor").mkdir(parents=True, exist_ok=True)
    for web, local in VENDOR.items():
        if os.environ.get("SIN_VENDOR") != "1":  # solo para probar sin red
            (SALIDA / local.lstrip("/")).write_bytes(bajar(VENDOR_BAJAR.get(local, web)))
    paginas, restos = 0, []
    for f in SALIDA.rglob("*.html"):
        t = f.read_text(encoding="utf-8", errors="surrogateescape")
        t2 = t
        for a, b in VENDOR.items():
            t2 = t2.replace(a, b)
        for a, b in CAMBIOS_RE:
            t2 = a.sub(b, t2)
        if t2 != t:
            f.write_text(t2, encoding="utf-8", errors="surrogateescape")
            paginas += 1
        r = re.findall(r"https://(?:cdn\.jsdelivr\.net|cdnjs\.cloudflare\.com|unpkg\.com)/[^\"' )]+", t2)
        if r:
            restos.append(f.relative_to(SALIDA).as_posix() + ": " + ", ".join(sorted(set(r))[:3]))
    (SALIDA / "CNAME").write_text(DOMINIO + "\n", encoding="utf-8")
    (SALIDA / ".nojekyll").write_text("", encoding="utf-8")
    v = json.loads((SALIDA / "version.json").read_text(encoding="utf-8")).get("version")
    if v != lista.get("version"):
        raise SystemExit(f"version.json dice {v} y la lista {lista.get('version')}: se repite en la siguiente vuelta.")
    print(f"Espejo listo: {len(lista['files']) - sum(1 for x in lista['files'] if x['p'] in SOLO_CLOUDFLARE)} archivos ({total // 1024} KB), {paginas} páginas con las librerías en local · versión {v}")
    if restos:
        print("OJO, aún dependen de Cloudflare/jsDelivr: " + " | ".join(restos))


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "hace-falta":
        hace_falta()
    else:
        construir()
