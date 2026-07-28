"""Proveedores de maxima prioridad: linea de comandos e inyeccion en memoria.

Comparten fichero porque comparten naturaleza: ninguno de los dos lee del
sistema de ficheros y ambos representan una decision explicita de quien invoca,
tomada por encima de todo lo configurado. Los dos aparecen en la traza con un
localizador que lo deja claro, para que nadie confunda una inyeccion puntual con
un valor que alguien escribio en un fichero.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from app.config.providers.base import coerce
from app.core.config.provenance import Origin, Priority
from app.core.config.resolver import ConfigLayer
from app.core.exceptions import ConfigValidationError


class CommandLineProvider:
    """Lee asignaciones explicitas de la linea de comandos.

    Formato: `--set clave.anidada=valor`, repetible. Se exige la forma explicita
    en lugar de inventar un flag por clave porque el conjunto de claves lo define
    el esquema y crece: un flag por clave obligaria a regenerar la CLI cada vez
    que se anade un parametro, y acabaria desincronizada.
    """

    def __init__(
        self,
        assignments: Sequence[str],
        priority: Priority = Priority.COMMAND_LINE,
    ) -> None:
        self._assignments = tuple(assignments)
        self._priority = priority

    def load(self) -> ConfigLayer | None:
        if not self._assignments:
            return None
        found: dict[str, Any] = {}
        for item in self._assignments:
            if "=" not in item:
                raise ConfigValidationError(
                    "Asignacion de linea de comandos sin '='",
                    assignment=item,
                    expected="clave.anidada=valor",
                )
            key, raw = item.split("=", 1)
            key = key.strip()
            if not key:
                raise ConfigValidationError("Asignacion sin clave", assignment=item)
            found[key] = coerce(raw.strip())
        return ConfigLayer(
            origin=Origin(priority=self._priority, locator="--set"),
            values=found,
        )


class MappingProvider:
    """Inyecta un mapa en memoria.

    Es la via por la que discovery fija los parametros de un candidato y por la
    que un test fija un escenario, sin escribir ficheros temporales. Prioridad
    `RUNTIME_OVERRIDE`: gana sobre todo lo demas, y la traza lo deja claro para
    que nadie confunda una inyeccion con un valor configurado.
    """

    def __init__(
        self,
        values: Mapping[str, Any],
        priority: Priority = Priority.RUNTIME_OVERRIDE,
        *,
        locator: str = "inyeccion en memoria",
    ) -> None:
        self._values = dict(values)
        self._priority = priority
        self._locator = locator

    def load(self) -> ConfigLayer | None:
        if not self._values:
            return None
        return ConfigLayer.from_mapping(self._priority, self._locator, self._values)


__all__ = ["CommandLineProvider", "MappingProvider"]
