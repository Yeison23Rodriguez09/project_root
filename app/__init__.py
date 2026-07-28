"""Capas de la arquitectura limpia. Todo el codigo importable cuelga de aqui.

    core            primitivas, sin dependencias internas
    domain          entidades, objetos de valor y servicios de dominio
    events          bus de eventos en proceso; mecanismo puro, sin I/O
    shared          puertos (Protocol) entre el dominio y el mundo
    research        features, signals, strategies, backtest, data
    portfolio       riesgo y dimensionamiento con vision de cartera
    execution       maquina de estados de ordenes, validacion y ruteo
    analytics       calculo de metricas y reporting
    walkforward     particionado en folds, evaluacion y agregacion
    validation      pruebas estadisticas
    optimization    busquedas
    discovery       generacion, mutacion y recombinacion de arquitecturas
    promotion       gobierno del ciclo de vida del zoo
    config          proveedores de configuracion; unico lector de ficheros
    broker          adaptadores de broker
    storage         zoo canonico y repositorio de artefactos
    monitoring      metricas de runtime, auditoria y health checks
    application     casos de uso; un paquete por caso
    container       raiz de composicion, inyeccion y ciclo de vida
    paper           motor sobre feed en tiempo real, sin ejecucion real
    live            motor de ejecucion real
    interfaces      puntos de entrada (CLI, API)

La regla de dependencia apunta siempre hacia dentro. La lista completa de quien
puede importar a quien es `configs/architecture.toml`, no este docstring:
`docs/ARCHITECTURE.md` es su vista generada y `tests/test_architecture.py` la
verifica sobre el AST.
"""

from __future__ import annotations

__version__ = "1.0.0"

__all__ = ["__version__"]
