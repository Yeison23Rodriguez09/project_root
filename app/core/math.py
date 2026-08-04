"""Transformaciones numericas puras.

Alcance deliberadamente estrecho: operaciones **sin memoria temporal**. Toda
funcion aqui es un mapa elemento a elemento o una reduccion sobre un array
completo, sin ventanas deslizantes.

Las ventanas moviles viven en `features/primitives.py`, no aqui. La frontera no
es estetica: una operacion con ventana puede introducir look-ahead si se
implementa mal, y aislarla en un unico modulo permite someterla entera al test
de causalidad. Lo que hay en este fichero no puede mirar al futuro porque no
mira a ningun vecino.

Todas las funciones:
  * aceptan y devuelven `float64`;
  * propagan `NaN` en lugar de rellenarlo;
  * no mutan la entrada;
  * no dependen de estado global.
"""

from __future__ import annotations

import numpy as np

from app.core.types import FloatArray

#: Tolerancia por defecto para comparar denominadores contra cero. Se elige
#: relativa a la precision de float64 y no un numero magico como 1e-8.
EPSILON: float = float(np.finfo(np.float64).eps)


def safe_divide(
    numerator: FloatArray | float,
    denominator: FloatArray | float,
    *,
    fill: float = np.nan,
    epsilon: float = EPSILON,
) -> FloatArray:
    """Division que convierte el cero en `fill` en lugar de en infinito.

    Un infinito propagado por un ratio contamina medias, sumas y hashes, y
    reaparece kilometros mas abajo como un resultado absurdo sin origen
    identificable. Se corta en la fuente.

    El valor por defecto es `NaN` y no cero de forma intencional: cero es un
    resultado legitimo y confundirlo con "indefinido" es exactamente el tipo de
    error que produce senales fantasma.
    """
    num = np.asarray(numerator, dtype=np.float64)
    den = np.asarray(denominator, dtype=np.float64)
    out = np.full(np.broadcast(num, den).shape, fill, dtype=np.float64)
    valid = np.abs(den) > epsilon
    np.divide(num, den, out=out, where=valid)
    return out


def zscore(
    values: FloatArray, *, mean: float | None = None, std: float | None = None
) -> FloatArray:
    """Estandariza usando media y desviacion **explicitas**.

    Los parametros son obligatorios en la practica para cualquier uso en
    investigacion: calcular la media sobre toda la serie y aplicarla a cada
    barra es look-ahead de manual, porque la barra 10 quedaria normalizada con
    informacion de la barra 10.000.

    Cuando no se pasan, se calculan sobre el array completo. Ese modo solo es
    legitimo para normalizar un conjunto ya cerrado (los resultados de un fold
    terminado, una poblacion de discovery), nunca una serie de precios sobre la
    que se vaya a operar.
    """
    array = np.asarray(values, dtype=np.float64)
    mu = float(np.nanmean(array)) if mean is None else mean
    sigma = float(np.nanstd(array)) if std is None else std
    return safe_divide(array - mu, sigma)


def minmax_scale(values: FloatArray, *, low: float, high: float) -> FloatArray:
    """Escala a [0, 1] con cotas explicitas y recorte fuera de rango.

    Se usa para convertir una magnitud arbitraria en `strength` de senal, que
    el dominio exige en [0, 1]. Las cotas se declaran, no se infieren.
    """
    if high <= low:
        raise ValueError(f"Cotas invalidas para minmax_scale: low={low} high={high}")
    array = np.asarray(values, dtype=np.float64)
    return np.clip((array - low) / (high - low), 0.0, 1.0)


def clip_to_unit(values: FloatArray) -> FloatArray:
    """Recorta a [0, 1] preservando `NaN`."""
    return np.clip(np.asarray(values, dtype=np.float64), 0.0, 1.0)


def sign_int8(values: FloatArray, *, threshold: float = 0.0) -> np.ndarray:
    """Signo con banda muerta, listo para usarse como direccion de senal.

    La banda muerta evita que ruido numerico alrededor de cero se traduzca en
    alternancia de direccion barra a barra, que es una fuente clasica de
    sobreoperativa invisible en backtest y ruinosa en costes reales.
    """
    array = np.asarray(values, dtype=np.float64)
    out = np.zeros(array.shape, dtype=np.int8)
    out[array > threshold] = 1
    out[array < -threshold] = -1
    return out


def nan_prefix(length: int, warmup: int) -> FloatArray:
    """Array de `length` con `warmup` posiciones a `NaN` y el resto a cero.

    Utilidad de construccion para indicadores: fija el prefijo no calculable de
    forma uniforme, de modo que ningun indicador invente su propia convencion.
    """
    out = np.zeros(length, dtype=np.float64)
    out[: min(max(warmup, 0), length)] = np.nan
    return out


def first_valid_index(values: FloatArray) -> int:
    """Indice del primer valor no `NaN`, o `len(values)` si son todos `NaN`.

    Es la forma canonica de medir el calentamiento **real** de una feature ya
    calculada, frente al calentamiento **declarado** por su registro. Que ambos
    coincidan se comprueba en los tests: una discrepancia significa que el
    walk-forward esta descartando de mas o, peor, de menos.
    """
    array = np.asarray(values, dtype=np.float64)
    valid = np.flatnonzero(~np.isnan(array))
    return int(valid[0]) if valid.size else int(array.size)


def drawdown_curve(equity: FloatArray) -> FloatArray:
    """Caida relativa respecto al maximo previo, como fraccion positiva.

    Se define aqui y no en `analytics` porque es una transformacion pura de una
    serie y la usan varios consumidores (metricas, limites de riesgo en vivo y
    validacion de robustez). Duplicarla en tres sitios garantizaria que las
    tres versiones acabaran discrepando.
    """
    values = np.asarray(equity, dtype=np.float64)
    peak = np.maximum.accumulate(values)
    return -safe_divide(values - peak, peak, fill=0.0)


def annualization_factor(bars_per_year: float) -> float:
    """Factor para anualizar una desviacion tipica por barra."""
    if bars_per_year <= 0:
        raise ValueError(f"bars_per_year debe ser positivo: {bars_per_year}")
    return float(np.sqrt(bars_per_year))


__all__ = [
    "EPSILON",
    "annualization_factor",
    "clip_to_unit",
    "drawdown_curve",
    "first_valid_index",
    "minmax_scale",
    "nan_prefix",
    "safe_divide",
    "sign_int8",
    "zscore",
]
