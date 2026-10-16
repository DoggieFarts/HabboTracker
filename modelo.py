"""
Análisis de mercado que complementa al radar (prediccion.py):

  * Índice del mercado: cómo se mueve el precio típico de todo el Mercadillo día a día.
  * Retador XGBoost: un modelo de árboles que ordena los furnis de cada semana con ~20 señales, entrenado
    semana a semana sin mirar el futuro. Compite contra las señales lineales del radar en las mismas
    semanas de prueba; el radar usa el que mejor le gane al mercado.
  * Pronóstico por furni: rango probable del precio dentro de 14 días (percentiles 10, 25, 50, 75 y 90)
    y la probabilidad de ganar si compras hoy y vendes entonces, ya con comisión.

Si xgboost no está instalado, todo lo que lo necesita se omite y el radar sigue con sus señales lineales.
"""

from __future__ import annotations

import math

import numpy as np

try:
    import xgboost as xgb
except ImportError:  # el radar funciona igual sin el modelo de árboles
    xgb = None

QUANTILES = [0.1, 0.25, 0.5, 0.75, 0.9]
RETRAIN_EVERY = 4        # semanas entre reentrenamientos del retador en la prueba
SEED = 7

# Las señales extra que ve el modelo de árboles, además de las del radar.
EXTRA = ["ultimo", "impulso7", "impulso30", "inventario", "liquidez", "nivel", "volatil90",
         "vs_max_anual", "vs_min_anual", "antiguedad", "otro_hotel"]
# Solo para el pronóstico: cómo viene el mercado en general (igual para todos los furnis en una fecha).
REGIME = ["mercado7", "mercado30"]

# Árboles poco profundos y con muchos datos por hoja: la ganancia a 14 días es muy ruidosa y un modelo
# más libre se aprende el ruido.
RANK_PARAMS = {"objective": "reg:squarederror", "max_depth": 3, "eta": 0.05, "subsample": 0.8,
               "colsample_bytree": 0.8, "min_child_weight": 50, "lambda": 5.0, "tree_method": "hist", "seed": SEED}
RANK_ROUNDS = 200
QUANT_PARAMS = {"objective": "reg:quantileerror", "quantile_alpha": QUANTILES, "max_depth": 4, "eta": 0.05,
                "subsample": 0.8, "colsample_bytree": 0.8, "min_child_weight": 50, "lambda": 5.0,
                "tree_method": "hist", "seed": SEED}
QUANT_ROUNDS = 300


def available() -> bool:
    return xgb is not None


# ---------------------------------------------------------------------------
# índice del mercado
# ---------------------------------------------------------------------------

def rolling_median7(P: np.ndarray) -> np.ndarray:
    """Mediana de los últimos 7 días de cada furni, para cada día (nan donde no hubo ventas)."""
    out = np.full(P.shape, np.nan, dtype=np.float32)
    for t in range(6, P.shape[1]):
        out[:, t] = np.nanmedian(P[:, t - 6:t + 1], axis=1)
    return out


def trimmed_mean(v: np.ndarray, cut: float = 0.1) -> float:
    v = np.sort(v)
    k = int(len(v) * cut)
    return float(np.mean(v[k:len(v) - k]))


def market_series(M7: np.ndarray, S: np.ndarray) -> np.ndarray:
    """Nivel del mercado por día (en logaritmo): qué tan caro está el furni típico contra su propio precio de
    los dos años. Promedio recortado (sin el 10% más alto ni el 10% más bajo) en vez de mediana: muchos furnis
    están justo en su precio típico y la mediana se quedaba clavada en cero. Solo cuentan furnis con ventas
    frecuentes."""
    liquid = S.sum(axis=1) >= 120          # ~1 venta cada 6 días en dos años
    M = M7[liquid]
    with np.errstate(divide="ignore", invalid="ignore"):
        rel = np.log(M / np.nanmedian(M, axis=1, keepdims=True))
    T = M.shape[1]
    logidx = np.zeros(T, dtype=np.float64)
    for t in range(7, T):
        vals = rel[:, t][np.isfinite(rel[:, t])]
        logidx[t] = trimmed_mean(vals) if len(vals) >= 30 else logidx[t - 1]
    return logidx


def market_summary(M7: np.ndarray, logidx: np.ndarray, start, today: int) -> dict:
    """Lo que muestra la página: la serie (base 100) y cuánto cambió en 7, 30 y 90 días."""
    from datetime import timedelta
    first = 7
    values = np.exp(logidx[first:today + 1] - logidx[first]) * 100
    change = lambda d: float(math.exp(logidx[today] - logidx[today - d]) - 1)  # noqa: E731
    med30 = np.nanmedian(M7[:, today - 29:today + 1], axis=1)
    now = M7[:, today]
    ok = np.isfinite(now) & np.isfinite(med30)
    return {
        "from": (start + timedelta(days=first)).isoformat(),
        "values": [round(float(v), 2) for v in values],
        "change7": round(change(7), 4), "change30": round(change(30), 4), "change90": round(change(90), 4),
        # amplitud: qué parte de los furnis está arriba de su mediana del último mes
        "breadth": round(float(np.mean(now[ok] > med30[ok])), 3) if ok.sum() else None,
    }


def regime_at(logidx: np.ndarray, t: int) -> dict:
    return {"mercado7": float(logidx[t] - logidx[t - 7]), "mercado30": float(logidx[t] - logidx[t - 30])}


# ---------------------------------------------------------------------------
# señales extra
# ---------------------------------------------------------------------------

def first_seen(P: np.ndarray) -> np.ndarray:
    has = ~np.isnan(P)
    return np.where(has.any(axis=1), np.argmax(has, axis=1), P.shape[1]).astype(np.float32)


def extra_features(P, raw: dict, t: int, first: np.ndarray, other_med30) -> dict:
    """Más señales, todas con datos hasta el día t."""
    med7, med30 = raw["med7"], raw["med30"]
    prev_week = np.nanmedian(P[:, t - 13:t - 6], axis=1)
    month_ago = np.nanmedian(P[:, t - 36:t - 29], axis=1)
    year = P[:, max(0, t - 364):t + 1]
    with np.errstate(divide="ignore", invalid="ignore"):
        return {
            "ultimo": raw["last"] / med7 - 1,
            "impulso7": med7 / prev_week - 1,
            "impulso30": med7 / month_ago - 1,
            "inventario": np.log((raw["off7"] + 1) / (raw["sold7"] / 7 + 1)),   # días de oferta acumulada
            "liquidez": np.log1p(raw["sold30"]),
            "nivel": np.log(med30),
            "volatil90": np.nanstd(np.log(P[:, t - 89:t + 1]), axis=1),
            "vs_max_anual": med7 / np.nanmax(year, axis=1) - 1,
            "vs_min_anual": med7 / np.nanmin(year, axis=1) - 1,
            "antiguedad": np.log1p(np.maximum(0, t - first)),
            "otro_hotel": (np.log(med30 / other_med30(t)) if other_med30 is not None
                           else np.full(P.shape[0], np.nan, dtype=np.float32)),
        }


def rank_keep_nan(x: np.ndarray) -> np.ndarray:
    """Rango entre -1 y 1 de los valores finitos; los que faltan se quedan como nan (xgboost los maneja)."""
    out = np.full(len(x), np.nan, dtype=np.float32)
    ok = np.isfinite(x)
    n = int(ok.sum())
    if n > 1:
        r = np.empty(n)
        r[np.argsort(x[ok], kind="mergesort")] = np.arange(n)
        out[ok] = r / (n - 1) * 2 - 1
    return out


def rank_matrix(feats: dict, names: list, mask: np.ndarray) -> np.ndarray:
    return np.column_stack([rank_keep_nan(np.asarray(feats[k], dtype=np.float64)[mask]) for k in names])


# ---------------------------------------------------------------------------
# retador: ordenar los furnis de cada semana
# ---------------------------------------------------------------------------

def walk_forward_scores(rows: list, half: int, horizon: int) -> dict:
    """Puntaje del modelo de árboles para cada semana de prueba, entrenado solo con semanas anteriores ya
    terminadas (con un hueco de horizon + 3 días para que la respuesta no se filtre)."""
    scores, model, trained_at = {}, None, None
    for i in range(half, len(rows)):
        t = rows[i]["t"]
        if model is None or i - trained_at >= RETRAIN_EVERY:
            prior = [j for j in range(i) if rows[j]["t"] + horizon + 3 <= t]
            if len(prior) < 8:
                continue
            model = fit_ranker([rows[j] for j in prior])
            trained_at = i
        scores[i] = model.predict(xgb.DMatrix(rows[i]["X"]))
    return scores


def fit_ranker(rows: list):
    X = np.vstack([r["X"] for r in rows])
    y = np.concatenate([r["y"] for r in rows])     # rango de la ganancia dentro de su semana
    return xgb.train(RANK_PARAMS, xgb.DMatrix(X, label=y), num_boost_round=RANK_ROUNDS)


def explain(model, X: np.ndarray, names: list) -> np.ndarray:
    """Aporte de cada señal al puntaje de cada furni (valores SHAP de xgboost)."""
    contrib = model.predict(xgb.DMatrix(X), pred_contribs=True)
    return contrib[:, :-1]


# ---------------------------------------------------------------------------
# pronóstico: rango del precio dentro de 14 días
# ---------------------------------------------------------------------------

def fit_quantiles(X: np.ndarray, y: np.ndarray):
    return xgb.train(QUANT_PARAMS, xgb.DMatrix(X, label=y), num_boost_round=QUANT_ROUNDS)


def predict_quantiles(model, X: np.ndarray) -> np.ndarray:
    q = model.predict(xgb.DMatrix(X)).reshape(len(X), len(QUANTILES))
    return np.sort(q, axis=1)   # los percentiles no se pueden cruzar


def prob_above(q: np.ndarray, threshold) -> np.ndarray:
    """P(y > threshold) interpolando entre los percentiles predichos; en las colas se acota a 5% y 95%.
    Es el mismo cálculo que hace la página con el precio actual."""
    levels = np.array(QUANTILES)
    th = np.broadcast_to(np.asarray(threshold, dtype=np.float64), (len(q),))
    out = np.empty(len(q))
    for i, row in enumerate(q):
        if th[i] <= row[0]:
            out[i] = 0.95
        elif th[i] >= row[-1]:
            out[i] = 0.05
        else:
            out[i] = 1 - float(np.interp(th[i], row, levels))
    return out


def quantile_report(q: np.ndarray, y: np.ndarray, threshold) -> dict:
    """Qué tan bien calibrado salió el pronóstico en semanas que el modelo no vio."""
    inside = (y >= q[:, 0]) & (y <= q[:, -1])
    p = prob_above(q, threshold)
    win = y > threshold
    hi = p >= 0.6
    return {
        "coverage80": round(float(np.mean(inside)), 3),          # debería rondar 0.80
        "median_hit": round(float(np.mean(y <= q[:, 2])), 3),     # debería rondar 0.50
        "brier": round(float(np.mean((p - win) ** 2)), 4),
        "brier_base": round(float(np.mean((np.mean(win) - win) ** 2)), 4),
        "confident_n": int(hi.sum()),
        "confident_win": round(float(np.mean(win[hi])), 3) if hi.any() else None,
        "rows": int(len(y)),
    }
