"""Proveedor de variables de entorno con prefijo `QP_`.

Es la unica capa que puede cambiar entre dos ejecuciones sin que nada quede
escrito en el repositorio, y por eso su procedencia se registra siempre: una
corrida que difiere de otra por una variable exportada en una terminal es
imposible de diagnosticar si la traza no la nombra.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from typing import Any

from app.config.providers.base import coerce
from app.core.config.provenance import Origin, Priority
from app.core.config.resolver import ConfigLayer

#: Prefijo de las variables de entorno que la plataforma reconoce.
ENV_PREFIX = "QP_"

#: Separador de niveles en variables de entorno. Doble guion bajo porque el
#: simple forma parte de los nombres de clave (`max_drawdown_pct`), y usarlo
#: como separador haria ambigua toda clave con guion bajo.
ENV_LEVEL_SEPARATOR = "__"


class EnvironmentProvider:
    """Lee variables de entorno con prefijo.

    Traduccion: `QP_RISK__MAX_DRAWDOWN_PCT=0.2` se convierte en
    `risk.max_drawdown_pct = 0.2`.

    El doble guion bajo separa niveles y el simple se conserva, porque casi
    todas las claves del sistema son `snake_case` y usar el simple como
    separador haria imposible expresar `max_drawdown_pct`.
    """

    def __init__(
        self,
        priority: Priority = Priority.ENVIRONMENT,
        *,
        prefix: str = ENV_PREFIX,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self._priority = priority
        self._prefix = prefix
        # El entorno se inyecta para poder probar la traduccion sin manipular el
        # proceso, que es estado global compartido entre tests.
        self._environ = environ if environ is not None else os.environ

    def load(self) -> ConfigLayer | None:
        found: dict[str, Any] = {}
        for name, raw in self._environ.items():
            if not name.startswith(self._prefix):
                continue
            tail = name[len(self._prefix) :]
            key = ".".join(part.lower() for part in tail.split(ENV_LEVEL_SEPARATOR))
            if not key:
                continue
            found[key] = coerce(raw)
        if not found:
            return None
        return ConfigLayer(
            origin=Origin(priority=self._priority, locator=f"entorno {self._prefix}*"),
            values=found,
        )


__all__ = ["ENV_LEVEL_SEPARATOR", "ENV_PREFIX", "EnvironmentProvider"]
