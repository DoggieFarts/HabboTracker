"""
Mercadillo en vivo: corre sin parar en una máquina virtual y mantiene los precios del
Mercadillo lo más frescos que Habbo permite.

  * Toma la lista de furnis activos de la página publicada (indice.json), sin acceso al repo.
  * Consulta primero los más vendidos (se refrescan cada par de minutos) y va rotando el resto.
  * Ajusta su ritmo solo cuando Habbo pide ir más despacio (error 429).
  * Cada 20 segundos escribe vivo.json, que un servidor web (Caddy) entrega por HTTPS.

Solo usa la biblioteca estándar de Python. La configuración viene de /etc/mercadillo.env:
  SITE_URL   dirección de la página, por ejemplo https://usuario.github.io/mercadillo-habbo
  SALIDA     archivo a escribir (por defecto /var/lib/mercadillo/vivo.json)
  TOP        cuántos de los más vendidos se refrescan seguido (por defecto 500)
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
import threading

HOTELS = {
        "habbo.es": "https://www.habbo.es", "habbo.com": "https://www.habbo.com",
        "habbo.com.br": "https://www.habbo.com.br", "habbo.de": "https://www.habbo.de",
    "habbo.fr": "https://www.habbo.fr", "habbo.it": "https://www.habbo.it",
    "habbo.nl": "https://www.habbo.nl", "habbo.fi": "https://www.habbo.fi",
    "habbo.com.tr": "https://www.habbo.com.tr",
}
SITE_URL = os.environ.get("SITE_URL", "").rstrip("/")
HOTEL = os.environ.get("HOTEL", "habbo.es")      # qué hotel seguir
OUT = Path(os.environ.get("SALIDA", f"/var/lib/mercadillo/vivo-{HOTEL}.json"))
TOP = int(os.environ.get("TOP", "500"))
BATCH = 20                 # máximo que acepta Habbo por consulta
BASE_PAUSE = 3.0
MAX_PAUSE = 20.0
INDEX_EVERY = 30 * 60      # recargar la lista de furnis cada media hora
WRITE_EVERY = 20           # escribir vivo.json cada 20 segundos
KEEP_SECONDS = 3 * 3600    # en vivo.json solo van precios de las últimas 3 horas
USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/140.0 Safari/537.36 Mercadillo-en-vivo/1.0")

running = True


def stop(*_):
    global running
    running = False


signal.signal(signal.SIGTERM, stop)
signal.signal(signal.SIGINT, stop)


def log(msg: str) -> None:
    print(f"{datetime.now(timezone.utc):%Y-%m-%d %H:%M:%S}  {msg}", flush=True)


def http_json(url: str, payload: dict | None = None, timeout: int = 30):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method="POST" if data else "GET")
    req.add_header("User-Agent", USER_AGENT)
    req.add_header("Accept", "application/json")
    if data:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def retry_after(e: urllib.error.HTTPError) -> int | None:
    try:
        return max(1, min(600, int(e.headers.get("Retry-After"))))
    except (TypeError, ValueError, AttributeError):
        return None


class Live:
    _index_lock = threading.Lock()

    def _fetch_index(self) -> None:
        """Descarga la lista de furnis en un hilo aparte y actualiza los índices."""
        try:
            # cada hotel vive en su subcarpeta: /{hotel}/indice.json
            index = http_json(f"{SITE_URL}/{HOTEL}/indice.json?t={int(time.time())}", timeout=60)
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            log(f"No se pudo leer la lista de furnis ({e}); sigo con la anterior.")
            self.index_at = time.time() - INDEX_EVERY + 300  # reintentar en 5 minutos
            return
        self.hotel = index.get("hotel", self.hotel)
        if self.hotel not in HOTELS:
            log(f"Hotel desconocido en indice.json: {self.hotel}; mantengo {self.hotel} solo si es válido.")
            self.hotel = next(iter(HOTELS))
        elif self.hotel != HOTEL:
            # el índice de esta subcarpeta pertenece a otro hotel: mejor no mezclar precios
            log(f"indice.json dice {self.hotel} pero esta instancia sigue {HOTEL}; uso {HOTEL}.")
            self.hotel = HOTEL
        items = index.get("items", {})
        ranked = sorted((k for k, v in items.items() if v.get("s")), key=lambda k: -(items[k].get("s") or 0))
        self.top, self.rest = ranked[:TOP], ranked[TOP:]
        self.i_top %= max(1, len(self.top))
        self.i_rest %= max(1, len(self.rest))
        self.index_at = time.time()
        log(f"Lista cargada: {len(self.top):,} más vendidos y {len(self.rest):,} más en rotación ({self.hotel}).")

    def __init__(self):
        self.hotel = "habbo.es"
        self.top: list[str] = []
        self.rest: list[str] = []
        self.i_top = self.i_rest = 0
        self.slot = 0
        self.prices: dict[str, list] = {}   # clave -> [oferta más baja, ofertas abiertas, hora unix]
        self.pause = BASE_PAUSE
        self.calm = 0
        self.index_at = 0.0
        self.written_at = 0.0
        self.top_started = time.time()
        self.top_cycle = None
        self.limited = 0
        self.load_previous()

    def load_previous(self) -> None:
        """Al reiniciar, conserva lo que ya estaba en vivo.json."""
        try:
            old = json.loads(OUT.read_text(encoding="utf-8"))
            base = old.get("t", 0)
            for k, (p, o, age) in old.get("items", {}).items():
                self.prices[k] = [p, o, base - age]
            log(f"Recuperé {len(self.prices):,} precios del archivo anterior.")
        except (FileNotFoundError, json.JSONDecodeError, ValueError, TypeError):
            pass

    def load_index(self) -> None:
        """Lanza la descarga del índice en segundo plano para no bloquear el bucle de consultas."""
        if not self._index_lock.acquire(blocking=False):
            return
        def worker() -> None:
            try:
                self._fetch_index()
            finally:
                self._index_lock.release()
        threading.Thread(target=worker, daemon=True).start()

    def next_batch(self) -> list[str]:
        """Dos consultas de los más vendidos por cada una del resto."""
        self.slot += 1
        use_rest = self.rest and (self.slot % 3 == 0 or not self.top)
        if use_rest:
            chunk = self.rest[self.i_rest:self.i_rest + BATCH]
            self.i_rest = (self.i_rest + BATCH) % len(self.rest)
            return chunk
        chunk = self.top[self.i_top:self.i_top + BATCH]
        self.i_top += BATCH
        if self.i_top >= len(self.top):
            self.i_top = 0
            now = time.time()
            self.top_cycle = round(now - self.top_started)
            self.top_started = now
        return chunk

    def query(self, keys: list[str]) -> None:
        payload = {"roomItems": [{"item": k[5:]} for k in keys if k.startswith("room:")],
                   "wallItems": [{"item": k[5:]} for k in keys if k.startswith("wall:")]}
        try:
            data = http_json(f"{HOTELS[self.hotel]}/api/public/marketplace/stats/batch", payload)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = retry_after(e) or 30
                self.pause, self.calm = min(MAX_PAUSE, self.pause * 1.6 + 0.5), 0
                self.limited += 1
                log(f"Habbo pidió ir más lento; espero {wait} s y dejo {self.pause:.1f} s entre consultas.")
                self.sleep(wait)
            else:
                log(f"Habbo respondió {e.code}; espero 60 s.")
                self.sleep(60)
            return
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            log(f"Sin respuesta de Habbo ({type(e).__name__}); espero 30 s.")
            self.sleep(30)
            return
        now = int(time.time())
        for kind, field in (("room", "roomItemData"), ("wall", "wallItemData")):
            for d in data.get(field) or []:
                try:
                    price = int(d.get("currentPrice") or 0) or None
                    offers = int(d["currentOpenOffers"]) if d.get("currentOpenOffers") is not None else None
                except (TypeError, ValueError):
                    continue
                self.prices[f"{kind}:{d.get('item')}"] = [price, offers, now]
        self.calm += 1
        if self.calm >= 60 and self.pause > BASE_PAUSE:  # un buen rato sin quejas: se acelera un poco
            self.pause, self.calm = max(BASE_PAUSE, self.pause * 0.85), 0

    def write(self) -> None:
        now = int(time.time())
        fresh = {k: [p, o, now - t] for k, (p, o, t) in self.prices.items() if now - t <= KEEP_SECONDS}
        self.prices = {k: v for k, v in self.prices.items() if now - v[2] <= KEEP_SECONDS}
        body = {"updated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "t": now,
                "hotel": self.hotel, "pause": round(self.pause, 1), "top_cycle_s": self.top_cycle,
                "items": fresh}
        OUT.parent.mkdir(parents=True, exist_ok=True)
        tmp = OUT.with_suffix(".tmp")
        tmp.write_text(json.dumps(body, separators=(",", ":")), encoding="utf-8")
        tmp.replace(OUT)  # reemplazo atómico: nunca se sirve un archivo a medias
        self.written_at = time.time()

    def sleep(self, seconds: float) -> None:
        end = time.time() + seconds
        while running and time.time() < end:
            time.sleep(min(1.0, end - time.time()))

    def run(self) -> None:
        if not SITE_URL:
            log("Falta SITE_URL en /etc/mercadillo.env.")
            sys.exit(1)
        log(f"Iniciando con la página {SITE_URL}.")
        last_report = time.time()
        while running:
            if time.time() - self.index_at > INDEX_EVERY:
                self.load_index()
            if not (self.top or self.rest):
                self.sleep(60)
                continue
            chunk = self.next_batch()
            started = time.time()
            self.query(chunk)
            self.last_keys = chunk
            if time.time() - self.written_at > WRITE_EVERY:
                self.write()
            if time.time() - last_report > 600:
                last_report = time.time()
                log(f"En vivo: {len(self.prices):,} precios frescos, pausa {self.pause:.1f} s, "
                    f"vuelta de los más vendidos: {self.top_cycle or '?'} s, avisos de Habbo: {self.limited}.")
            self.sleep(max(0.0, self.pause - (time.time() - started)))
        self.write()
        log("Detenido.")


if __name__ == "__main__":
    Live().run()
