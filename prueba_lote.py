"""
Prueba cuántos furnis acepta la API del Mercadillo en una sola consulta.

Pide lotes de 20, 50, 100 y 200 furnis que ya sabemos que tienen ventas y revisa,
para cada tamaño, si Habbo responde bien, cuánto tarda y si devuelve todos los
furnis pedidos o recorta la lista sin avisar. Hace pocas consultas y con pausas
largas para no molestar a Habbo.

Uso: python prueba_lote.py --sitio sitio
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
from pathlib import Path

from recolector import HOTELS, load_json, request

SIZES = [20, 50, 100, 200]
REPEATS = 2          # consultas por tamaño
PAUSE = 20           # segundos entre consultas
PAUSE_AFTER_429 = 90


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sitio", default="sitio")
    args = parser.parse_args()

    index = load_json(Path(args.sitio) / "indice.json", {})
    hotel = index.get("hotel") or load_json(Path("avisos.json"), {}).get("hotel", "habbo.es")
    # los furnis con más ventas: sabemos que la API debe devolver datos de todos
    active = sorted(((k, v) for k, v in index.get("items", {}).items() if v.get("s")),
                    key=lambda kv: -kv[1]["s"])
    keys = [k for k, _ in active]
    if len(keys) < max(SIZES):
        print(f"Solo hay {len(keys)} furnis con ventas guardados; se necesitan {max(SIZES)}. "
              "Corre primero el recolector.")
        return 1

    url = f"{HOTELS[hotel]}/api/public/marketplace/stats/batch"
    results = []
    offset = 0
    print(f"Probando en {hotel} con furnis que ya tienen ventas.\n")
    for size in SIZES:
        for rep in range(REPEATS):
            chunk = keys[offset:offset + size]
            offset = (offset + size) % (len(keys) - max(SIZES))
            payload = {"roomItems": [{"item": k[5:]} for k in chunk if k.startswith("room:")],
                       "wallItems": [{"item": k[5:]} for k in chunk if k.startswith("wall:")]}
            t0 = time.monotonic()
            try:
                data = request(url, payload, timeout=60)
                took = time.monotonic() - t0
                got = (data.get("roomItemData") or []) + (data.get("wallItemData") or [])
                with_data = sum(1 for d in got if d.get("statsDate"))
                status = "ok"
            except urllib.error.HTTPError as e:
                took, got, with_data, status = time.monotonic() - t0, [], 0, f"error {e.code}"
            except (urllib.error.URLError, TimeoutError) as e:
                took, got, with_data, status = time.monotonic() - t0, [], 0, f"sin respuesta ({type(e).__name__})"
            results.append((size, status, len(got), with_data, took))
            print(f"Lote de {size:>3}: {status:<22} devolvió {len(got):>3} de {size:>3}, "
                  f"{with_data:>3} con datos, {took:5.1f} s")
            time.sleep(PAUSE_AFTER_429 if "429" in status else PAUSE)

    print("\nResumen")
    best = 20
    for size in SIZES:
        rows = [r for r in results if r[0] == size]
        ok = [r for r in rows if r[1] == "ok"]
        complete = [r for r in ok if r[2] == size and r[3] >= 0.9 * size]
        avg = sum(r[4] for r in ok) / len(ok) if ok else 0
        if len(complete) == len(rows):
            verdict = "funciona"
            best = size
        elif ok and not complete:
            verdict = "responde pero recorta la lista"
        else:
            verdict = "falla"
        print(f"  {size:>3} por consulta: {verdict}, {len(ok)} de {len(rows)} bien, {avg:.1f} s en promedio")
    print(f"\nTamaño más grande que funcionó completo: {best}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
