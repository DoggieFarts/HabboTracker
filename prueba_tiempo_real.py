"""
¿La API del Mercadillo está en tiempo real o en caché?

Toma los 20 furnis que más se venden y los consulta una vez por minuto durante
15 minutos. En furnis que se venden decenas de veces al día, la oferta más baja o
la cantidad de ofertas abiertas debería cambiar varias veces en ese lapso si la API
está en vivo. También revisa los encabezados de caché de la respuesta.

Son solo 16 consultas en total, muy por debajo del ritmo del recolector.

Uso: python prueba_tiempo_real.py --sitio sitio
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from recolector import HOTELS, USER_AGENT, load_json

MINUTES = 15
INTERVAL = 60
ITEMS = 20
CACHE_HEADERS = ("Cache-Control", "Age", "Expires", "Last-Modified", "ETag", "CF-Cache-Status", "X-Cache")


def fetch(url: str, payload: dict):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST")
    req.add_header("User-Agent", USER_AGENT)
    req.add_header("Accept", "application/json")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=30) as resp:
        headers = {h: resp.headers.get(h) for h in CACHE_HEADERS if resp.headers.get(h)}
        return json.loads(resp.read().decode("utf-8")), headers


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sitio", default="sitio")
    args = parser.parse_args()

    index = load_json(Path(args.sitio) / "indice.json", {})
    hotel = index.get("hotel", "habbo.es")
    top = sorted(((k, v) for k, v in index.get("items", {}).items() if v.get("s")), key=lambda kv: -kv[1]["s"])[:ITEMS]
    if len(top) < ITEMS:
        print("No hay suficientes furnis con ventas guardados. Corre primero el recolector.")
        return 1
    payload = {"roomItems": [{"item": k[5:]} for k, _ in top if k.startswith("room:")],
               "wallItems": [{"item": k[5:]} for k, _ in top if k.startswith("wall:")]}
    url = f"{HOTELS[hotel]}/api/public/marketplace/stats/batch"
    names = {k: v.get("n", k) for k, v in top}
    per_day = {k: v["s"] / 30 for k, v in top}

    print(f"Consultando {ITEMS} furnis muy vendidos de {hotel} cada {INTERVAL} s durante {MINUTES} min.")
    print("Venden entre " f"{min(per_day.values()):.0f} y {max(per_day.values()):.0f} piezas al día.\n")

    samples: dict[str, list[tuple]] = {k: [] for k, _ in top}
    header_log = []
    rounds = MINUTES * 60 // INTERVAL + 1
    for i in range(rounds):
        t0 = time.monotonic()
        stamp = datetime.now(timezone.utc).strftime("%H:%M:%S")
        try:
            data, headers = fetch(url, payload)
        except urllib.error.HTTPError as e:
            print(f"{stamp}  error {e.code}; se reintenta en la siguiente vuelta.")
            time.sleep(INTERVAL)
            continue
        except (urllib.error.URLError, TimeoutError) as e:
            print(f"{stamp}  sin respuesta ({type(e).__name__}).")
            time.sleep(INTERVAL)
            continue
        header_log.append(headers)
        by_key = {f"room:{d.get('item')}": d for d in data.get("roomItemData") or []}
        by_key.update({f"wall:{d.get('item')}": d for d in data.get("wallItemData") or []})
        changed = 0
        for key in samples:
            d = by_key.get(key) or {}
            snap = (d.get("currentPrice"), d.get("currentOpenOffers"), d.get("statsDate"))
            if samples[key] and samples[key][-1] != snap:
                changed += 1
            samples[key].append(snap)
        print(f"{stamp}  vuelta {i + 1:>2} de {rounds}: {changed} de {ITEMS} furnis cambiaron desde la vuelta anterior")
        if i < rounds - 1:
            time.sleep(max(0, INTERVAL - (time.monotonic() - t0)))

    print("\nCambios por furni en los 15 minutos (oferta más baja / ofertas abiertas)")
    total_price = total_offers = 0
    for key, snaps in samples.items():
        price_changes = sum(1 for a, b in zip(snaps, snaps[1:]) if a[0] != b[0])
        offer_changes = sum(1 for a, b in zip(snaps, snaps[1:]) if a[1] != b[1])
        total_price += price_changes
        total_offers += offer_changes
        first, last = snaps[0] if snaps else (None, None, None), snaps[-1] if snaps else (None, None, None)
        print(f"  {names[key][:30]:<30} {price_changes:>2} / {offer_changes:>2}   "
              f"({first[0]} -> {last[0]} créditos, {per_day[key]:.0f} ventas al día)")

    print("\nEncabezados de caché de Habbo:")
    seen = {json.dumps(h, sort_keys=True) for h in header_log}
    if not header_log or seen == {"{}"}:
        print("  ninguno")
    for h in sorted(seen):
        print(f"  {h}")

    print("\nConclusión:")
    if total_price + total_offers == 0:
        print("  Nada cambió en 15 minutos, ni en los furnis que más se venden: la API casi seguro está en caché.")
        print("  Un vigía en tiempo real no serviría.")
    elif total_price + total_offers < ITEMS:
        print(f"  Hubo pocos cambios ({total_price} de precio, {total_offers} de ofertas). La API parece actualizarse,")
        print("  pero con retraso o en bloques. Un vigía cada 10 a 15 minutos podría servir; cada minuto, no.")
    else:
        print(f"  Hubo muchos cambios ({total_price} de precio, {total_offers} de ofertas): la API está en tiempo real")
        print("  o casi. Vale la pena armar el vigía con alertas.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
