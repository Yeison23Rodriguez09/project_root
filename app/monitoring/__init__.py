"""Metricas de runtime, auditoria y health checks.

Capa infraestructura (nivel 4). Es donde viven los consumidores del bus que SI
tocan el mundo -escriben a stdout, a disco o a un colector- y por eso no pueden
estar en `events`, que es un mecanismo puro de capa `core`.

Ningun motor importa este paquete. Un motor emite por `EventSinkPort`, declarado
en `shared`, y `container` decide que adaptador lo implementa. Esa inversion es
lo que permite que la misma linea de codigo escriba JSON estructurado en
produccion y no haga nada en un test, sin que el motor sepa cual de las dos
cosas esta ocurriendo.

Modulos:

* `sink.py`          `StructlogEventSink`: EventSinkPort sobre structlog + orjson
* `run_context.py`   `RunContext`: identidad y procedencia de una corrida
* `runtime_metrics.py`  `RuntimeMetrics`: suscriptor del bus que agrega el trafico

`monitoring` depende de `events` por ADR-0008. La arista apunta hacia abajo
-capa 4 a capa 0- y existe para que los suscriptores puedan tipar el `Event` que
reciben en lugar de aceptar un objeto sin forma.
"""
