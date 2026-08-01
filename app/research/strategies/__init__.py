"""Materializacion de un StrategySpec en bloques ejecutables.

    StrategySpec  ->  compile_strategy()  ->  CompiledStrategy.evaluate(frame)

La descripcion y el ejecutable son cosas distintas y viven en sitios distintos.
`StrategySpec` es del dominio: describe, se serializa, viaja en artefactos y
tiene identidad por hash de contenido. `CompiledStrategy` es de research: no se
serializa, no tiene identidad propia -hereda la del spec- y solo sabe evaluar.

Discovery genera descripciones. Backtest y, mas adelante, el motor en vivo
ejecutan compiladas. Ninguno de los dos necesita conocer al otro.
"""

from __future__ import annotations

from app.research.strategies.combine import combine
from app.research.strategies.compiler import (
    CompiledStrategy,
    StrategyDecision,
    compile_strategy,
)

__all__ = ["CompiledStrategy", "StrategyDecision", "combine", "compile_strategy"]
