"""Contrato comun de los proveedores y conversion de texto a valor.

Vive aparte de cualquier proveedor concreto para que anadir uno nuevo -parquet,
HTTP, un gestor de secretos- no obligue a importar el lector de TOML.
"""

from __future__ import annotations

import tomllib
from typing import Any, Protocol, runtime_checkable

from app.core.config.resolver import ConfigLayer


@runtime_checkable
class ConfigProvider(Protocol):
    """Fuente de configuracion.

    Devolver `None` significa "esta fuente no aporta nada", que es distinto de
    devolver una capa vacia. La segunda dice "consulte y no habia valores"; la
    primera, "no habia nada que consultar". La traza registra ambas por separado
    porque un fichero ausente suele ser una ruta mal escrita.
    """

    def load(self) -> ConfigLayer | None: ...


def coerce(raw: str) -> Any:
    """Convierte un texto al tipo que representa, usando el parser de TOML.

    Reutilizar TOML en lugar de escribir un conversor propio da gratis la
    sintaxis correcta para enteros, flotantes, booleanos, fechas y listas, y
    garantiza que `QP_RISK__MAX=0.02` y `max = 0.02` en un fichero produzcan
    exactamente el mismo valor. Un conversor a mano acabaria divergiendo en
    algun borde -notacion cientifica, `true` frente a `True`- y entonces el
    mismo ajuste daria resultados distintos segun por donde entrase.

    Si no es TOML valido se devuelve la cadena tal cual, que es el
    comportamiento esperado para rutas y nombres.
    """
    try:
        return tomllib.loads(f"__v__ = {raw}")["__v__"]
    except tomllib.TOMLDecodeError:
        return raw


__all__ = ["ConfigProvider", "coerce"]
