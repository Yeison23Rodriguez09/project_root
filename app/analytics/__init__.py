"""Medir, atribuir y reportar. Nunca influir en lo que se mide.

    Trade[] + equity -> PerformanceMetrics

La prohibicion es la parte que fija la frontera: analytics NO influye en la
ejecucion. Un modulo de medida que pudiera alterar lo medido -recortar una
serie, descartar una operacion incomoda- convertiria cada informe posterior en
una afirmacion sobre si mismo.

Tampoco juzga. "Cuanto de este resultado puede explicarse por azar" es la
pregunta de `validation`, y "merece entrar en produccion" la de `promotion`.
Aqui solo se responde COMO gano.

El objeto que produce vive en `domain.value_objects.metrics` porque es un
contrato entre capas -backtest lo produce, walk-forward lo agrega, discovery lo
ordena, promocion lo evalua-; lo que vive aqui es el calculo (ADR-0012).
"""

from __future__ import annotations

from app.analytics.performance import performance_metrics

__all__ = ["performance_metrics"]
