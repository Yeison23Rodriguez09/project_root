"""Simulacion historica barra a barra.

    Bars -> FeatureFrame -> CompiledStrategy -> llenado -> trades -> equity

El motor no contiene ninguna regla de gestion de posiciones: donde va el stop,
cuando se considera tocado y como se cierra en un `Trade` con sus costes vive en
`domain.services.positions`, que es codigo compartido con la ejecucion real.
Aqui solo esta el bucle sobre la historia y el marcado a mercado.
"""

from __future__ import annotations

from app.research.backtest.engine import (
    BacktestEngine,
    BacktestResult,
    as_port_result,
)

__all__ = ["BacktestEngine", "BacktestResult", "as_port_result"]
