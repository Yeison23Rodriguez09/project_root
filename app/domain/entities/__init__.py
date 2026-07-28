"""Entidades: identidad propia que sobrevive al cambio de atributos.

Una orden sigue siendo la misma orden cuando pasa de `SUBMITTED` a `FILLED`.
Un candidato sigue siendo el mismo candidato cuando pasa de `CANDIDATE` a
`REJECTED`. Esa continuidad de identidad es lo que las distingue de los objetos
de valor.

Inmutabilidad y entidad no son incompatibles: cada transicion produce una
instancia nueva que acumula su historia. Asi el recorrido completo queda
registrado por construccion, sin depender de que alguien recordara escribir un
log en cada paso.

Pendientes de traslado desde `app/domain/*.py` (movimiento mecanico):

* `bars.py`  `Bars`
* `order.py` `Order`, `OrderIntent`, `OrderState`, `OrderType`
* `trade.py` `Position`, `Trade`, `ExitReason`

Pendientes de construccion:

* `portfolio.py` agregado de posiciones y equity, base del riesgo por cartera
* `candidate.py` `StrategySpec` + evidencia + estado de ciclo de vida
"""
