"""
Radar de Mercadillo: señales que históricamente anticipan subidas de precio.

Cómo funciona:
  1. Arma, para cada furni, su serie diaria de precio, ventas y ofertas abiertas con el historial
     guardado (hasta 2 años).
  2. Calcula cinco señales que solo usan información del pasado de cada fecha:
       - oferta:    si las ofertas abiertas bajaron (menos piezas disponibles)
       - demanda:   si se vende más que en las semanas anteriores
       - impulso:   precio de la última semana contra su mediana de 30 días
       - rebote:    precio de la última semana contra su mediana de 90 días (caídas que suelen corregirse)
       - estabilidad: qué tan poco varía su precio (menos riesgo)
  3. Prueba contra el pasado (sin mirar el futuro): con la primera mitad de las fechas aprende
     cuánto pesa cada señal; con la segunda mitad simula comprar cada semana los 10 furnis con mejor
     puntaje y venderlos 14 días después, ya con comisión, y lo compara contra el mercado en general.
  4. Solo si esa prueba le gana al mercado, publica recomendaciones para hoy.

Salida: sitio/prediccion.json, que muestra la pestaña Radar.

Uso: python prediccion.py --sitio sitio
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import warnings
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np

HORIZON = 14          # días entre la compra y la venta en la prueba
STEP = 7              # una fecha de prueba por semana
LOOKBACK = 90         # días de historia que necesita cada señal
TOP_N = 10            # cuántos furnis "compra" la prueba en cada fecha
PICKS_TODAY = 30      # cuántas recomendaciones se publican
MIN_SOLD_30 = 30      # al menos una venta al día en promedio
MIN_PRICE = 10        # sin furnis de pocos créditos, donde un crédito cambia todo
MIN_PRICE_DAYS = 10   # días con precio en el último mes, para que las señales sean confiables
MAX_DAYS = 460        # días de historia que se usan (poco más de un año de prueba)
SIGNALS = ["oferta", "demanda", "impulso", "rebote", "estabilidad"]

warnings.filterwarnings("ignore", category=RuntimeWarning)  # medianas de ventanas sin datos


def load_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def file_name(classname: str) -> str:
    import re
    return re.sub(r"[^A-Za-z0-9_.\-~]", "-", classname.replace("*", "~"))


# ---------------------------------------------------------------------------
# datos
# ---------------------------------------------------------------------------

def build_matrices(site: Path, items: dict, end: date):
    """Matrices furni × día: precio promedio (nan sin ventas), vendidos y ofertas abiertas."""
    start = end - timedelta(days=MAX_DAYS - 1)
    keys, prices, sold, offers = [], [], [], []
    for key, item in items.items():
        if not item.get("s"):
            continue
        kind, cls = key.split(":", 1)
        days = load_json(site / "h" / kind / f"{file_name(cls)}.json", {}).get("d", {})
        if len(days) < 30:
            continue
        p = np.full(MAX_DAYS, np.nan, dtype=np.float32)
        s = np.zeros(MAX_DAYS, dtype=np.float32)
        o = np.full(MAX_DAYS, np.nan, dtype=np.float32)
        for d, row in days.items():
            try:
                i = (date.fromisoformat(d) - start).days
            except ValueError:
                continue
            if 0 <= i < MAX_DAYS:
                if row[0]:
                    p[i] = row[0]
                s[i] = row[1] or 0
                if row[2] is not None:
                    o[i] = row[2]
        keys.append(key)
        prices.append(p)
        sold.append(s)
        offers.append(o)
    if not keys:
        return [], None, None, None, start
    return keys, np.vstack(prices), np.vstack(sold), np.vstack(offers), start


def features_at(P, S, O, t: int):
    """Señales en el día t usando solo datos hasta t (inclusive)."""
    med7 = np.nanmedian(P[:, t - 6:t + 1], axis=1)
    med30 = np.nanmedian(P[:, t - 29:t + 1], axis=1)
    med90 = np.nanmedian(P[:, t - 89:t + 1], axis=1)
    sold7 = S[:, t - 6:t + 1].sum(axis=1)
    sold_prev = S[:, t - 27:t - 6].sum(axis=1) / 3          # promedio semanal de las 3 semanas previas
    sold30 = S[:, t - 29:t + 1].sum(axis=1)
    off7 = np.nanmean(O[:, t - 6:t + 1], axis=1)
    off_prev = np.nanmean(O[:, t - 27:t - 6], axis=1)
    price_days = np.sum(~np.isnan(P[:, t - 29:t + 1]), axis=1)
    logp = np.log(P[:, t - 29:t + 1])
    vol = np.nanstd(logp, axis=1)

    feats = {
        "oferta": -(off7 / off_prev - 1),                     # positivo = menos ofertas que antes
        "demanda": np.log((sold7 + 1) / (sold_prev + 1)),
        "impulso": med7 / med30 - 1,
        "rebote": -(med7 / med90 - 1),                        # positivo = está abajo de su nivel de 90 días
        "estabilidad": -vol,
    }
    # precio de compra realista: el último precio observado, no un promedio atrasado
    W = P[:, t - 6:t + 1]
    valid = ~np.isnan(W)
    last_i = W.shape[1] - 1 - np.argmax(valid[:, ::-1], axis=1)
    last = np.where(valid.any(axis=1), W[np.arange(W.shape[0]), last_i], np.nan)
    raw = {"last": last, "med7": med7, "med30": med30, "med90": med90, "sold7": sold7, "sold_prev": sold_prev,
           "sold30": sold30, "off7": off7, "off_prev": off_prev, "vol": vol}
    universe = (sold30 >= MIN_SOLD_30) & (med30 >= MIN_PRICE) & (price_days >= MIN_PRICE_DAYS) & ~np.isnan(last)
    for f in feats.values():
        universe &= np.isfinite(f)
    return feats, raw, universe


def forward_return(P, t: int, entry, fee: float):
    """Ganancia neta si compras al último precio y vendes a la mediana de los días alrededor de t + HORIZON."""
    exit_ = np.nanmedian(P[:, t + HORIZON - 3:t + HORIZON + 4], axis=1)
    return exit_ * (1 - fee) / entry - 1


def zrank(x: np.ndarray) -> np.ndarray:
    """Rango normalizado entre -1 y 1: robusto a valores extremos."""
    order = np.argsort(np.argsort(x))
    return (order / max(1, len(x) - 1)) * 2 - 1


def spearman(a: np.ndarray, b: np.ndarray) -> float:
    if len(a) < 20:
        return float("nan")
    ra, rb = zrank(a), zrank(b)
    return float(np.corrcoef(ra, rb)[0, 1])


# ---------------------------------------------------------------------------
# prueba contra el pasado
# ---------------------------------------------------------------------------

def backtest(P, S, O, fee: float, today_index: int):
    dates = list(range(LOOKBACK - 1, today_index - HORIZON - 3, STEP))
    rows = []  # por fecha: señales (z), retorno futuro, universo
    for t in dates:
        feats, raw, uni = features_at(P, S, O, t)
        fwd = forward_return(P, t, raw["last"], fee)
        mask = uni & np.isfinite(fwd)
        if mask.sum() < 50:
            continue
        z = {k: zrank(v[mask]) for k, v in feats.items()}
        rows.append({"t": t, "z": z, "fwd": fwd[mask]})
    if len(rows) < 10:
        return None

    ic = {k: [spearman(r["z"][k], r["fwd"]) for r in rows] for k in SIGNALS}
    half = len(rows) // 2
    train, test = rows[:half], rows[half:]

    def weights_from(sample) -> dict:
        w = {}
        for k in SIGNALS:
            vals = [spearman(r["z"][k], r["fwd"]) for r in sample]
            vals = [v for v in vals if np.isfinite(v)]
            w[k] = float(np.mean(vals)) if vals else 0.0
        return w

    w_train = weights_from(train)
    picks_ret, market_ret, hits, beats, excess = [], [], [], 0, []
    score_bins: list[tuple[float, float]] = []
    for r in test:
        score = sum(w_train[k] * r["z"][k] for k in SIGNALS)
        top = np.argsort(-score)[:TOP_N]
        # medianas y no promedios: con promedios, los furnis más volátiles parecen mejores por pura varianza
        pr, mk = float(np.median(r["fwd"][top])), float(np.median(r["fwd"]))
        picks_ret.append(pr)
        market_ret.append(mk)
        hits.append(float(np.mean(r["fwd"][top] > 0)))
        beats += pr > mk
        excess.append(pr - mk)
        score_bins.extend(zip(zrank(score).tolist(), r["fwd"].tolist()))

    # qué retorno tuvo cada décimo del puntaje en la prueba: sirve para estimar lo esperado hoy
    score_bins.sort()
    deciles = []
    n = len(score_bins)
    for i in range(10):
        chunk = score_bins[i * n // 10:(i + 1) * n // 10]
        deciles.append(float(np.median([f for _, f in chunk])) if chunk else 0.0)

    first = rows[0]["t"]
    last = rows[-1]["t"]
    return {
        "ic": {k: float(np.nanmean(v)) for k, v in ic.items()},
        "weights_train": w_train,
        "weights_all": weights_from(rows),
        "test_dates": len(test),
        "picks_ret": float(np.mean(picks_ret)),
        "market_ret": float(np.mean(market_ret)),
        "hit_rate": float(np.mean(hits)),
        "beat_share": beats / len(test),
        # qué tan distinto de cero es el exceso semanal: más de 2 es difícil que sea suerte
        "t_stat": float(np.mean(excess) / (np.std(excess, ddof=1) / math.sqrt(len(excess)))) if len(excess) > 2 and np.std(excess) > 0 else 0.0,
        "deciles": deciles,
        "first_t": first, "test_first_t": test[0]["t"], "last_t": last,
    }


# ---------------------------------------------------------------------------
# recomendaciones de hoy
# ---------------------------------------------------------------------------

def reasons(raw: dict, i: int) -> list[str]:
    out = []
    off7, off_prev = raw["off7"][i], raw["off_prev"][i]
    if np.isfinite(off7) and np.isfinite(off_prev) and off_prev > 0 and off7 < off_prev * 0.85:
        out.append(f"Las ofertas abiertas bajaron {round((1 - off7 / off_prev) * 100)}% contra las semanas anteriores.")
    s7, sp = raw["sold7"][i], raw["sold_prev"][i]
    if sp > 0 and s7 > sp * 1.2:
        out.append(f"Se vendió {round((s7 / sp - 1) * 100)}% más que en una semana normal del último mes.")
    m7, m30, m90 = raw["med7"][i], raw["med30"][i], raw["med90"][i]
    if m30 and m7 > m30 * 1.04:
        out.append(f"Viene subiendo: {round((m7 / m30 - 1) * 100)}% arriba de su mediana de 30 días.")
    if m90 and m7 < m90 * 0.92:
        out.append(f"Está {round((1 - m7 / m90) * 100)}% abajo de su nivel de los últimos 3 meses.")
    if raw["vol"][i] < 0.08:
        out.append("Su precio es muy estable, así que el riesgo es bajo.")
    return out or ["Varias señales pequeñas a favor al mismo tiempo."]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sitio", default="sitio")
    args = parser.parse_args()
    site = Path(args.sitio)

    index = load_json(site / "indice.json", {})
    items = index.get("items", {})
    fee = float(index.get("fee_pct", 1)) / 100
    end = datetime.now(timezone.utc).date()
    keys, P, S, O, start = build_matrices(site, items, end)
    out_path = site / "prediccion.json"
    base = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "horizon_days": HORIZON,
            "top_n": TOP_N, "fee_pct": fee * 100}
    if not keys:
        out_path.write_text(json.dumps({**base, "enabled": False, "reason": "Todavía no hay historial suficiente."}))
        print("Sin historial suficiente.")
        return 0
    today = MAX_DAYS - 1
    print(f"Radar: {len(keys):,} furnis con historial. Probando señales…", flush=True)

    bt = backtest(P, S, O, fee, today)
    if not bt:
        out_path.write_text(json.dumps({**base, "enabled": False,
                                        "reason": "Todavía no hay suficientes semanas de historial para probar las señales."}))
        print("Muy pocas fechas para probar.")
        return 0

    excess = bt["picks_ret"] - bt["market_ret"]
    # se publica solo si le gana al mercado con claridad: en promedio, casi todas las semanas y sin ser suerte
    enabled = excess > 0.01 and bt["beat_share"] >= 0.6 and bt["t_stat"] >= 2 and bt["hit_rate"] > 0.5
    day = lambda t: (start + timedelta(days=int(t))).isoformat()  # noqa: E731
    summary = {
        "enabled": enabled,
        "picks_ret": round(bt["picks_ret"], 4), "market_ret": round(bt["market_ret"], 4),
        "hit_rate": round(bt["hit_rate"], 3),
        "beat_share": round(bt["beat_share"], 3), "test_weeks": bt["test_dates"], "t_stat": round(bt["t_stat"], 2),
        "test_from": day(bt["test_first_t"]), "test_to": day(bt["last_t"] + HORIZON),
        "train_from": day(bt["first_t"]),
        "signals": {k: round(v, 3) for k, v in bt["ic"].items()},
        "weights": {k: round(v, 3) for k, v in bt["weights_all"].items()},
    }
    print(f"Prueba: señales {bt['picks_ret']:+.1%} vs mercado {bt['market_ret']:+.1%} en {HORIZON} días (típico por semana), "
          f"aciertos {bt['hit_rate']:.0%}, le gana al mercado {bt['beat_share']:.0%} de las semanas "
          f"({bt['test_dates']} semanas, confianza t={bt['t_stat']:.1f}). "
          f"{'Se publican recomendaciones.' if enabled else 'No le gana al mercado con claridad: no se publican.'}")
    print("Fuerza de cada señal (correlación con la subida futura):",
          ", ".join(f"{k} {v:+.3f}" for k, v in bt["ic"].items()))

    picks = []
    if enabled:
        feats, raw, uni = features_at(P, S, O, today)
        launched = {k for k, v in items.items() if v.get("f") and
                    (end - date.fromisoformat(v["f"])).days < 30}
        mask = uni & np.array([k not in launched for k in keys])
        idx = np.where(mask)[0]
        if len(idx):
            z = {k: zrank(v[idx]) for k, v in feats.items()}
            score = sum(bt["weights_all"][k] * z[k] for k in SIGNALS)
            ranks = zrank(score)
            order = np.argsort(-score)[:PICKS_TODAY]
            for j in order:
                i = idx[j]
                decile = min(9, int((ranks[j] + 1) / 2 * 10))
                picks.append({"k": keys[i], "score": round(float(ranks[j]), 3),
                              "expected": round(bt["deciles"][decile], 4),
                              "entry": int(round(float(raw["last"][i]))),
                              "why": reasons(raw, i)})
    result = {**base, **summary, "picks": picks}
    out_path.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"Listo: {len(picks)} recomendaciones.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
