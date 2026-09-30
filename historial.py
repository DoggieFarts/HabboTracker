"""
Rellena el historial de cada furni con hasta 2 años de ventas desde habboapi.site.

La API oficial de Habbo solo da 30 días. habboapi.site guarda mucho más, así que
este script pide una vez el historial completo de cada furni activo, empezando por
los que más se venden, y lo mezcla con lo que ya tenemos. Lo oficial nunca se
sobrescribe: solo se agregan los días que faltan.

habboapi.site permite 30 consultas por minuto sin clave, así que se respeta ese
ritmo y se trabaja por tandas: cada corrida avanza lo que alcance en el tiempo
indicado y la siguiente continúa.

Uso: python historial.py --sitio sitio --minutos 20
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.parse
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from recolector import KEEP_DAYS, file_name, load_json, request, save_json, to_int

API = "https://habboapi.site/api/market/history"
PAUSE = 2.2                 # 30 consultas por minuto como máximo, con margen
HOTEL_CODES = {"habbo.es": "es", "habbo.com": "com", "habbo.com.br": "br", "habbo.de": "de", "habbo.fr": "fr",
               "habbo.it": "it", "habbo.nl": "nl", "habbo.fi": "fi", "habbo.com.tr": "tr"}
TYPES = {"room": "roomItem", "wall": "wallItem"}


def fetch(cls: str, hotel_code: str):
    query = urllib.parse.urlencode({"classname": cls, "hotel": hotel_code, "days": "all"})
    return request(f"{API}?{query}", timeout=40)


def merge(site: Path, kind: str, cls: str, history: list) -> int:
    """Agrega los días que faltan. Formato local por día: [promedio, vendidos, ofertas, más_baja]."""
    path = site / "h" / kind / f"{file_name(cls)}.json"
    days: dict[str, list] = load_json(path, {}).get("d", {})
    cutoff = (date.today() - timedelta(days=KEEP_DAYS)).isoformat()
    added = 0
    for row in history:
        if not isinstance(row, list) or len(row) < 5:
            continue
        avg, sold, _credits, offers, ts = row[:5]
        try:
            day = datetime.fromtimestamp(int(ts), tz=timezone.utc).date().isoformat()
        except (TypeError, ValueError, OSError):
            continue
        if day < cutoff:
            continue
        current = days.get(day)
        if current and current[0]:
            continue  # ya lo tenemos de la API oficial
        low = current[3] if current else None
        days[day] = [to_int(avg), to_int(sold) or 0, to_int(offers), low]
        added += 1
    if added:
        save_json(path, {"d": dict(sorted(days.items()))})
    return added


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sitio", default="sitio")
    parser.add_argument("--minutos", type=float, default=20)
    args = parser.parse_args()
    site = Path(args.sitio)

    index = load_json(site / "indice.json", {})
    hotel = index.get("hotel", "habbo.es")
    code = HOTEL_CODES.get(hotel)
    if not code:
        print(f"habboapi.site no tiene datos de {hotel}.")
        return 0
    state_path = site / "historial_estado.json"
    state = load_json(state_path, {})
    if state.get("hotel") != hotel:
        state = {"hotel": hotel, "done": {}}
    done: dict[str, str] = state["done"]

    pending = sorted(((k, v) for k, v in index.get("items", {}).items() if k not in done and v.get("s")),
                     key=lambda kv: -kv[1]["s"])
    total_active = sum(1 for v in index.get("items", {}).values() if v.get("s"))
    print(f"Historial largo: {len(done):,} furnis listos, {len(pending):,} pendientes. "
          f"Esta corrida avanza hasta {args.minutos:g} min (unos {int(args.minutos * 60 / (PAUSE + 0.5)):,} furnis).")

    started, processed, days_added, errors = time.monotonic(), 0, 0, 0
    for key, item in pending:
        if time.monotonic() - started > args.minutos * 60:
            break
        kind, cls = key.split(":", 1)
        try:
            data = fetch(cls, code)
            errors = 0
        except urllib.error.HTTPError as e:
            if e.code == 429:
                print("habboapi.site pidió ir más lento; espero 60 s.")
                time.sleep(60)
                continue
            if e.code == 404:
                data = []
            else:
                errors += 1
                print(f"habboapi.site respondió {e.code} con {cls}.")
                if errors >= 5:
                    print("Demasiados errores seguidos; se sigue en la próxima corrida.")
                    break
                time.sleep(10)
                continue
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            errors += 1
            print(f"Sin respuesta de habboapi.site con {cls} ({type(e).__name__}).")
            if errors >= 5:
                print("Demasiados errores seguidos; se sigue en la próxima corrida.")
                break
            time.sleep(10)
            continue

        # el buscador de habboapi.site trata * y _ como comodines: nos quedamos solo con el furni exacto
        match = next((d for d in data or [] if d.get("ClassName") == cls and d.get("FurniType") == TYPES[kind]), None)
        history = ((match or {}).get("marketData") or {}).get("history") or []
        days_added += merge(site, kind, cls, history)
        done[key] = date.today().isoformat()
        processed += 1
        if processed % 50 == 0:  # unos 2 minutos entre mensajes
            save_json(state_path, state)  # por si la corrida se corta, no se repite lo ya hecho
            print(f"  {processed:,} furnis, {days_added:,} días agregados ({(time.monotonic() - started) / 60:.0f} min)")
        time.sleep(PAUSE)

    save_json(state_path, state)
    print(f"Listo: {processed:,} furnis con historial nuevo ({days_added:,} días agregados). "
          f"Van {len(done):,} de {total_active:,}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
