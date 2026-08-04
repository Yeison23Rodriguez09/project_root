"""Indicadores y features. Funciones puras y causales.

Importar este paquete puebla `FEATURES`. Los modulos de familia se importan
aqui por su efecto de registro y no porque se usen sus nombres: sin esta
importacion el catalogo quedaria vacio y discovery no encontraria nada que
componer, sin que ningun error lo delatara.
"""

from __future__ import annotations

from app.research.features import momentum, trend, volatility
from app.research.features.momentum import roc, rsi
from app.research.features.registry import FEATURES
from app.research.features.trend import ema, sma
from app.research.features.volatility import atr, stdev, true_range

#: Familias declaradas. Discovery las usa para evitar componer tres bloques de
#: la misma, que aportan redundancia disfrazada de diversificacion.
FAMILIES: tuple[str, ...] = ("trend", "momentum", "volatility")

__all__ = [
    "FAMILIES",
    "FEATURES",
    "atr",
    "ema",
    "momentum",
    "roc",
    "rsi",
    "sma",
    "stdev",
    "trend",
    "true_range",
    "volatility",
]
