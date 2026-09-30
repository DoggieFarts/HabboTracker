"""
Recolector de Mercadillo para GitHub Actions (versión con todos los furnis).

No necesitas elegir furnis: el recolector descarga el catálogo completo del hotel,
revisa cada semana todos los furnis (una séptima parte por día) y sigue a diario
los que tienen ventas en los últimos 30 días. avisos.json solo sirve para que
GitHub te avise cuando un furni llega al precio que quieres.

Uso:
    python recolector.py --sitio sitio             corrida diaria normal
    python recolector.py --sitio sitio --completo  revisa el catálogo entero de una vez

Solo usa la biblioteca estándar de Python.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).parent
AVISOS = ROOT / "avisos.json"

HOTELS = {
    "habbo.es": "https://www.habbo.es",
    "habbo.com": "https://www.habbo.com",
    "habbo.com.br": "https://www.habbo.com.br",
    "habbo.de": "https://www.habbo.de",
    "habbo.fr": "https://www.habbo.fr",
    "habbo.it": "https://www.habbo.it",
    "habbo.nl": "https://www.habbo.nl",
    "habbo.fi": "https://www.habbo.fi",
    "habbo.com.tr": "https://www.habbo.com.tr",
}
KINDS = {"piso": "room", "pared": "wall", "room": "room", "wall": "wall"}
TZ = ZoneInfo("America/Mexico_City")
BATCH_SIZE = 20
PAUSE = 1.0                  # segundos entre peticiones, para no saturar la API
SCAN_DAYS = 7                # el catálogo completo se revisa en esta cantidad de días
CATALOG_MAX_AGE_DAYS = 7
KEEP_DAYS = 365              # historial que se conserva por furni
TIME_BUDGET_MIN = 45         # al pasar este tiempo se guarda lo avanzado y se sigue en la próxima corrida
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/140.0 Safari/537.36 Mercadillo/2.0")


# ---------------------------------------------------------------------------
# utilidades
# ---------------------------------------------------------------------------

def request(url: str, payload: dict | None = None, headers: dict | None = None, timeout: int = 30):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    req.add_header("User-Agent", USER_AGENT)
    req.add_header("Accept", "application/json")
    if data:
        req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
        return json.loads(body) if body else None


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")


def to_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def file_name(classname: str) -> str:
    """rare_fan*4 -> rare_fan~4 (el asterisco no es buena idea en rutas ni URLs)."""
    return re.sub(r"[^A-Za-z0-9_.\-~]", "-", classname.replace("*", "~"))


# ---------------------------------------------------------------------------
# catálogo
# ---------------------------------------------------------------------------

def refresh_catalog(site: Path, hotel: str) -> list[list[str]]:
    """Devuelve [nombre, classname, room|wall] de todo el hotel. Se descarga una vez por semana."""
    path = site / "catalogo.json"
    current = load_json(path, {})
    if (current.get("hotel") == hotel and current.get("updated") and current.get("furnis") and
            date.today() - date.fromisoformat(current["updated"]) < timedelta(days=CATALOG_MAX_AGE_DAYS)):
        return current["furnis"]
    try:
        raw = request(f"{HOTELS[hotel]}/gamedata/furnidata_json/1", timeout=90)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
        print(f"No se pudo descargar el catálogo de furnis: {e}")
        return current.get("furnis", []) if current.get("hotel") == hotel else []
    furnis, seen = [], set()
    for key, kind in (("roomitemtypes", "room"), ("wallitemtypes", "wall")):
        for f in (raw.get(key) or {}).get("furnitype", []):
            cls = f.get("classname")
            if cls and (kind, cls) not in seen:
                seen.add((kind, cls))
                furnis.append([(f.get("name") or "").strip() or cls, cls, kind])
    furnis.sort(key=lambda x: (x[2], x[1]))
    save_json(path, {"hotel": hotel, "updated": date.today().isoformat(), "furnis": furnis})
    print(f"Catálogo actualizado: {len(furnis):,} furnis.")
    return furnis


# ---------------------------------------------------------------------------
# historial por furni
# ---------------------------------------------------------------------------

def merge_history(site: Path, kind: str, cls: str, stats: dict, today: date) -> dict:
    """Mezcla lo que devolvió la API con lo guardado. Formato por día: [promedio, vendidos, ofertas, más_baja]."""
    path = site / "h" / kind / f"{file_name(cls)}.json"
    days: dict[str, list] = load_json(path, {}).get("d", {})
    stats_day = date.fromisoformat(stats["statsDate"])
    for h in stats.get("history") or []:
        day = (stats_day + timedelta(days=int(h["dayOffset"]))).isoformat()
        old = days.get(day, [None, 0, None, None])
        days[day] = [to_int(h.get("averagePrice")), to_int(h.get("totalSoldItems")) or 0,
                     to_int(h.get("totalOpenOffers")), old[3]]
    current = to_int(stats.get("currentPrice"))
    if current:
        old = days.get(today.isoformat(), [None, 0, None, None])
        old[3] = min(old[3] or current, current)
        days[today.isoformat()] = old
    cutoff = (today - timedelta(days=KEEP_DAYS)).isoformat()
    days = {d: v for d, v in sorted(days.items()) if d >= cutoff}
    save_json(path, {"d": days})
    return days


def summarize(days: dict[str, list]) -> tuple[int, list[int], int | None]:
    """Vendidos en 30 días, minigráfica y promedio simple, tomando como referencia el último día con ventas."""
    with_avg = [d for d, v in days.items() if v[0]]
    if not with_avg:
        return 0, [], None
    last = date.fromisoformat(max(with_avg))
    since = (last - timedelta(days=29)).isoformat()
    recent = [v for d, v in sorted(days.items()) if d >= since]
    sold = sum(v[1] or 0 for v in recent)
    spark = [v[0] for v in recent if v[0]]
    avg = round(sum(spark) / len(spark)) if spark else None
    return sold, spark, avg


# ---------------------------------------------------------------------------
# corrida
# ---------------------------------------------------------------------------

def run(site: Path, full_scan: bool) -> int:
    config = load_json(AVISOS, {})
    hotel = config.get("hotel", "habbo.es")
    if hotel not in HOTELS:
        print(f"Hotel desconocido en avisos.json: {hotel}. Opciones: {', '.join(HOTELS)}")
        return 1
    site.mkdir(parents=True, exist_ok=True)
    (site / ".nojekyll").touch()

    state = load_json(site / "estado.json", {})
    index = load_json(site / "indice.json", {})
    if state.get("hotel") and state["hotel"] != hotel:
        print(f"Cambiaste de hotel a {hotel}: empieza un historial nuevo.")
        state, index = {}, {}
    items: dict[str, dict] = index.get("items", {})

    catalog = refresh_catalog(site, hotel)
    names = {f"{k}:{c}": n for n, c, k in catalog}

    targets: dict[str, int] = {}
    for a in config.get("avisos", []):
        kind = KINDS.get(str(a.get("tipo", "piso")).lower())
        cls, price = str(a.get("classname", "")).strip(), to_int(a.get("precio"))
        if kind and cls and price:
            targets[f"{kind}:{cls}"] = price

    # qué consultar hoy: avisos + furnis activos + una rebanada del catálogo
    all_keys = [f"{k}:{c}" for _, c, k in catalog]
    cursor = int(state.get("cursor", 0))
    full_mode = bool(full_scan or not items or state.get("partial"))
    if full_mode:
        cursor = cursor if state.get("partial") else 0  # si la anterior quedó a medias, sigue donde iba
        scan = all_keys[cursor:] + all_keys[:cursor]
        next_cursor = 0
    else:
        size = math.ceil(len(all_keys) / SCAN_DAYS) if all_keys else 0
        cursor = cursor % max(1, len(all_keys))
        scan = (all_keys[cursor:] + all_keys[:cursor])[:size]
        next_cursor = (cursor + size) % max(1, len(all_keys))
    priority = list(dict.fromkeys(list(targets) + list(items)))
    seen = set(priority)
    queue = priority + [k for k in scan if k not in seen]
    started = time.monotonic()
    print(f"Consultando {len(queue):,} furnis ({len(items):,} activos, {len(scan):,} del catálogo, "
          f"{len(targets)} con aviso). Tiempo estimado: {math.ceil(len(queue) / BATCH_SIZE * (PAUSE + 0.6) / 60)} min.")

    base = HOTELS[hotel]
    today = datetime.now(TZ).date()
    done, found_total, stopped = 0, 0, False
    for start in range(0, len(queue), BATCH_SIZE):
        if time.monotonic() - started > TIME_BUDGET_MIN * 60:
            print(f"Se alcanzó el límite de {TIME_BUDGET_MIN:g} min; se guarda lo avanzado y la próxima corrida sigue.")
            stopped = True
            break
        chunk = queue[start:start + BATCH_SIZE]
        payload = {"roomItems": [{"item": k[5:]} for k in chunk if k.startswith("room:")],
                   "wallItems": [{"item": k[5:]} for k in chunk if k.startswith("wall:")]}
        result = None
        for attempt in range(3):
            try:
                result = request(f"{base}/api/public/marketplace/stats/batch", payload)
                break
            except urllib.error.HTTPError as e:
                if e.code == 403 and done == 0:
                    print(f"{hotel} respondió 403: su protección contra bots está bloqueando a GitHub.")
                    return 1
                if e.code in (429, 500, 502, 503, 504) and attempt < 2:
                    wait = 30 * (attempt + 1)
                    print(f"{hotel} pidió ir más lento (error {e.code}); espero {wait} s.")
                    time.sleep(wait)
                    continue
                print(f"Error {e.code} de {hotel}; se guarda lo que ya se tenía.")
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
                if attempt < 2:
                    time.sleep(15)
                    continue
                print(f"Fallo de conexión ({e}); se guarda lo que ya se tenía.")
            break
        if result is None:
            stopped = True
            break

        found = {f"room:{d.get('item')}": d for d in result.get("roomItemData") or []}
        found.update({f"wall:{d.get('item')}": d for d in result.get("wallItemData") or []})
        for key in chunk:
            stats = found.get(key)
            if not stats or not stats.get("statsDate"):
                continue
            has_trades = any(to_int(h.get("totalSoldItems")) for h in stats.get("history") or [])
            current = to_int(stats.get("currentPrice"))
            if not (has_trades or current or key in targets or key in items):
                continue  # nunca se ha vendido: no vale la pena guardarlo
            kind, cls = key.split(":", 1)
            days = merge_history(site, kind, cls, stats, today)
            sold30, spark, simple_avg = summarize(days)
            if sold30 == 0 and not current and key not in targets:
                items.pop(key, None)
                continue
            found_total += 1
            items[key] = {"n": names.get(key, cls), "c": cls, "t": kind, "p": current,
                          "a": to_int(stats.get("averagePrice")) or simple_avg, "s": sold30,
                          "o": to_int(stats.get("currentOpenOffers")), "sp": spark}
        done += len(chunk)
        if start + BATCH_SIZE < len(queue):
            time.sleep(PAUSE)
        if done // 1000 > (done - len(chunk)) // 1000:
            mins = (time.monotonic() - started) / 60
            print(f"  {done:,} de {len(queue):,}  ({mins:.0f} min, {found_total:,} con ventas)")

    for key in targets:  # que los avisos sin datos aún aparezcan en la página
        items.setdefault(key, {"n": names.get(key, key.split(":", 1)[1]), "c": key.split(":", 1)[1],
                               "t": key.split(":", 1)[0], "p": None, "a": None, "s": 0, "o": None, "sp": []})

    save_json(site / "indice.json", {
        "hotel": hotel, "fee_pct": config.get("comision_pct", 1),
        "updated": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "targets": targets, "catalog_size": len(catalog), "items": items,
    })
    scanned = max(0, done - len(priority))
    if stopped and all_keys:
        resume = (cursor + scanned) % len(all_keys)
    else:
        resume = next_cursor
    swept = (int(state.get("swept", 0)) if state.get("partial") else 0) + scanned
    partial = bool(stopped and full_mode and swept < len(all_keys))
    state.update({"hotel": hotel, "cursor": resume, "partial": partial, "swept": swept if partial else 0,
                  "last_run": datetime.now(timezone.utc).isoformat(timespec="seconds")})
    save_json(site / "estado.json", state)
    print(f"Listo: {done:,} consultados, {found_total:,} con datos, {len(items):,} furnis en la página.")

    hits = [dict(v, key=k, target=targets[k]) for k, v in items.items()
            if k in targets and v.get("p") and v["p"] <= targets[k]]
    open_alert_issues(hits)
    return 0


def open_alert_issues(hits: list[dict]) -> None:
    token, repo = os.environ.get("GITHUB_TOKEN"), os.environ.get("GITHUB_REPOSITORY")
    if not hits or not token or not repo:
        return
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    api = f"https://api.github.com/repos/{repo}/issues"
    try:
        open_titles = {i["title"] for i in request(f"{api}?state=open&per_page=100", headers=headers)}
    except urllib.error.URLError as e:
        print(f"No se pudieron revisar los avisos abiertos: {e}")
        return
    for h in hits:
        title = f"[Aviso] {h['n']} llegó a tu precio"
        if title in open_titles:
            continue
        body = (f"La oferta más baja de **{h['n']}** (`{h['c']}`) es de **{h['p']:,}** créditos y tu aviso "
                f"está en {h['target']:,}.\n\nCierra este issue cuando lo revises; se abrirá otro si vuelve a "
                "bajar de tu precio.")
        try:
            request(api, {"title": title, "body": body}, headers=headers)
            print(f"Aviso creado: {title}")
        except urllib.error.URLError as err:
            print(f"No se pudo crear el aviso de {h['n']}: {err}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sitio", default="sitio", help="carpeta donde se guardan los datos y la página")
    parser.add_argument("--completo", action="store_true", help="revisar todo el catálogo en esta corrida")
    args = parser.parse_args()
    return run(Path(args.sitio), args.completo)


if __name__ == "__main__":
    sys.exit(main())
