"""Emision de eventos estructurados. Implementa `EventSinkPort`.

Es el unico lugar del sistema que escribe observabilidad al exterior. La regla
4.4 -"los logs son eventos estructurados en JSON, nunca `print()`"- se cumple
aqui y solo aqui: `configs/conventions.toml` prohibe `print` en todo `app/`, y
`tests/test_conventions.py::test_no_import_star_and_no_print` lo verifica.

Por que JSON y no texto: un log que solo se lee con los ojos no permite
construir el embudo de descarte ni responder "por que no entro" sobre 200.000
barras. La pregunta que este proyecto necesita contestar no es "que paso" sino
"cuantas veces paso cada cosa y en que contexto", y eso exige agregacion.

Por que `orjson` y no `json`: serializa numeros de numpy y fechas sin
conversores a mano, y la ruta caliente -un evento por barra en discovery- pesa.
Es dependencia declarada en `pyproject.toml` desde el primer dia.

Por que `structlog` y no `logging`: el contexto se acumula por enlace
(`bind`), que es exactamente la semantica que pide `EventSinkPort`. Con
`logging` habria que reconstruir el contexto en cada llamada o usar variables
globales de hilo, que es estado oculto y lo prohibe P6.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from typing import Any, Self, TextIO

import orjson
import structlog

from app.core.types import Severity

#: Nivel por defecto de un evento que no declara el suyo. `INFO` y no `WARNING`
#: porque un sink que avisa de todo es un sink que nadie lee.
DEFAULT_SEVERITY = Severity.INFO


def _serialize(value: Any, default: Any) -> str:
    """Serializador de structlog respaldado por orjson.

    `orjson.dumps` devuelve `bytes` y structlog espera `str`; la conversion se
    hace aqui en lugar de en cada llamada. `default` es el gancho que structlog
    aporta para los tipos que no conoce, y se propaga tal cual: interceptarlo
    convertiria un fallo de serializacion en una cadena silenciosa, y un evento
    que no se puede grabar debe notarse al emitirlo, no al auditarlo.
    """
    return orjson.dumps(value, default=default, option=orjson.OPT_SORT_KEYS).decode("utf-8")


class StructlogEventSink:
    """Destino de eventos estructurados en JSON, con contexto acumulable.

    Implementa `app.shared.ports.EventSinkPort`.

    `bind` devuelve un sink NUEVO en lugar de mutar el actual. La diferencia
    importa: si mutara, dos motores que compartieran el sink por inyeccion se
    contaminarian el contexto entre si -el `symbol` de uno apareceria en los
    eventos del otro- y el problema solo se veria al leer los logs, cuando ya no
    hay forma de saber cual era el correcto.

    El flujo de salida se inyecta. Sin eso, comprobar que se emite lo que se
    dice que se emite exigiria capturar `stdout` del proceso, que es estado
    global compartido entre tests.
    """

    __slots__ = ("_context", "_logger", "_stream")

    def __init__(
        self,
        *,
        stream: TextIO | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> None:
        self._context: dict[str, Any] = dict(context or {})
        # Se conserva el flujo para poder propagarlo en `bind`. Sin guardarlo, un
        # sink enlazado escribiria a stdout aunque el original tuviera destino
        # inyectado, y el desvio solo se notaria al buscar en los logs eventos
        # que se emitieron a otro sitio.
        self._stream: TextIO | None = stream
        self._logger = structlog.wrap_logger(
            structlog.PrintLogger(file=stream if stream is not None else sys.stdout),
            processors=[
                structlog.processors.add_log_level,
                structlog.processors.TimeStamper(fmt="iso", utc=True),
                structlog.processors.JSONRenderer(serializer=_serialize),
            ],
        )

    def emit(self, event: str, **fields: Any) -> None:
        """Escribe un evento con el contexto enlazado mas los campos dados.

        El contexto va primero para que un campo explicito de la llamada pueda
        sobreescribirlo. Es el orden que espera quien escribe la llamada: lo mas
        cercano gana.
        """
        self._logger.msg(event, **{**self._context, **fields})

    def bind(self, **fields: Any) -> Self:
        """Sink nuevo con contexto fijo anadido (`run_id`, `symbol`, ...)."""
        return type(self)(stream=self._stream, context={**self._context, **fields})

    @property
    def context(self) -> Mapping[str, Any]:
        """Contexto enlazado. Se expone para diagnostico y para los tests."""
        return dict(self._context)

    def __repr__(self) -> str:
        keys = ",".join(sorted(self._context)) or "-"
        return f"StructlogEventSink(context={keys})"


class NullEventSink:
    """Sink que descarta todo. Para modos donde la observabilidad estorba.

    No es un doble de test: es un adaptador de produccion con una politica
    -"aqui no se emite"- y se registra en el contenedor como cualquier otro. Lo
    usa el modo `ci`, donde miles de eventos por corrida convertirian la salida
    del pipeline en ruido sin que nadie los lea nunca.

    Existe para que el codigo llamante NUNCA tenga que preguntar si hay sink. Un
    `if sink is not None` repartido por los motores es la via por la que la
    observabilidad acaba siendo opcional y, por tanto, ausente donde importa.
    """

    __slots__ = ("_context",)

    def __init__(self, *, context: Mapping[str, Any] | None = None) -> None:
        self._context: dict[str, Any] = dict(context or {})

    def emit(self, event: str, **fields: Any) -> None:
        del event, fields

    def bind(self, **fields: Any) -> Self:
        return type(self)(context={**self._context, **fields})

    @property
    def context(self) -> Mapping[str, Any]:
        return dict(self._context)

    def __repr__(self) -> str:
        return "NullEventSink()"


__all__ = ["DEFAULT_SEVERITY", "NullEventSink", "StructlogEventSink"]
