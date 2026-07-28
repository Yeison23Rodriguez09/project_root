"""Objetos de valor: su identidad es su contenido.

Dos objetos de valor con los mismos atributos son el mismo objeto. De ahi que
`StrategySpec` viva aqui: su identificador es el hash de su composicion, no un
UUID asignado. Esa propiedad es la que permite deduplicar exactamente el
espacio de busqueda de discovery.

Contenido actual:

* `money.py`      `Money`, `Ratio`
* `time_range.py` `TimeRange`, `Fold`

Pendientes de traslado desde `app/domain/*.py` (movimiento mecanico, sin
cambios de contenido):

* `instrument.py`    `Instrument`, `CostModel`
* `signal.py`        `SignalOutput`, `ReasonCode`
* `metrics.py`       `PerformanceMetrics`
* `strategy_spec.py` `StrategySpec`, `BlockSpec`, `CombineMode`
"""
