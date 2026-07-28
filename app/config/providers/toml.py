"""Proveedor de ficheros TOML. Formato oficial de la plataforma.

Es el oficial porque `tomllib` esta en la biblioteca estandar y por tanto no
puede faltar en ningun entorno. Un formato de configuracion que dependiese de
una dependencia opcional convertiria "el sistema no arranca" en un problema de
instalacion, que es lo ultimo que se quiere diagnosticar en produccion.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from app.core.config.provenance import Priority
from app.core.config.resolver import ConfigLayer
from app.core.exceptions import ConfigNotFound, ConfigValidationError


class TomlFileProvider:
    """Lee un fichero TOML.

    Attributes:
        required: Si es `True`, la ausencia del fichero es un error. Por defecto
            es `False` porque la mayoria de capas son opcionales: no tener
            `configs/local.toml` es lo normal, no un fallo.
    """

    def __init__(
        self, path: Path | str, priority: Priority, *, required: bool = False
    ) -> None:
        self._path = Path(path)
        self._priority = priority
        self._required = required

    def load(self) -> ConfigLayer | None:
        if not self._path.is_file():
            if self._required:
                raise ConfigNotFound(
                    "Fichero de configuracion obligatorio ausente",
                    path=str(self._path),
                    priority=self._priority.name,
                )
            return None
        try:
            data = tomllib.loads(self._path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as exc:
            raise ConfigValidationError(
                "TOML invalido", path=str(self._path), detail=str(exc)
            ) from exc
        # tomllib no expone numeros de linea. La procedencia se limita a la ruta
        # del fichero, que es suficiente para localizar el valor a mano.
        return ConfigLayer.from_mapping(self._priority, str(self._path), data)


__all__ = ["TomlFileProvider"]
