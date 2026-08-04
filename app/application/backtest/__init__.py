"""Caso de uso: simulacion historica determinista.

    Parquet -> DatasetRepository -> Strategy -> BacktestEngine -> Risk
            -> ExecutionSimulator -> Analytics -> ArtifactStore

Es el primer recorrido ejecutable de la plataforma de extremo a extremo, y por
eso importa mas como PRUEBA DE LA ARQUITECTURA que como funcionalidad: si la
cadena completa se compone sin tocar un contrato, el diseno se sostiene; si
hubiera que mover una capa para que encajara, no.

Aqui vive la secuencia; las reglas viven en los motores. Ver `service.py` para
el flujo y `runner.py` para la composicion.
"""

from __future__ import annotations

from app.application.backtest.request import BacktestRequest
from app.application.backtest.response import BacktestReport
from app.application.backtest.runner import FILL_MODELS, build_backtest_service
from app.application.backtest.service import BacktestApplicationService

__all__ = [
    "FILL_MODELS",
    "BacktestApplicationService",
    "BacktestReport",
    "BacktestRequest",
    "build_backtest_service",
]
