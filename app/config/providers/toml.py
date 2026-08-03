"""Proveedor de ficheros TOML. Formato oficial de la plataforma.

Es el oficial porque `tomllib` esta en la biblioteca estandar y por tanto no
puede faltar en ningun entorno. Un formato de configuracion que dependiese de
una dependencia opcional convertiria "el sistema no arranca" en un problema de
instalacion, que es lo ultimo que se quiere diagnosticar en produccion.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from app.core.config.provenance import Priority
from app.core.config.resolver import ConfigLayer
from app.core.exceptions import ConfigNotFound, ConfigValidationError


class TomlFileProvider:
    """Lee un fichero TOML.

    Attributes:
        required: Si es `True`, la ausencia del fichero es un error. Por defecto
            es `False` porque la mayoria de capas son opcionales: no tener
            `configs/local.toml` es lo normal, no un fallo.
        prefix: Espacio de nombres bajo el que cuelgan las claves del fichero.

    El prefijo existe porque los contratos de dominio -`risk.toml`,
    `backtest.toml`- comparten nivel con `global.toml` y todos declaran su propio
    `[meta] version`. Sin espacio de nombres, tres ficheros distintos escribirian
    la misma clave `meta.version` con la misma prioridad y la resolucion tendria
    que elegir entre valores igual de legitimos. Con el, la clave resultante
    -`risk.meta.version`- dice ademas de que fichero salio, que es exactamente lo
    que `qp config show` necesita para explicar una procedencia.
    """

    def __init__(
        self,
        path: Path | str,
        priority: Priority,
        *,
        required: bool = False,
        prefix: str = "",
    ) -> None:
        self._path = Path(path)
        self._priority = priority
        self._required = required
        self._prefix = prefix

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
        pruned = _without_rationale(data)
        payload = {self._prefix: pruned} if self._prefix else pruned
        return ConfigLayer.from_mapping(self._priority, str(self._path), payload)


#: Nombre de las subtablas que explican un valor en lugar de declararlo.
#:
#: Es la convencion del proyecto entera -`risk.toml`, `backtest.toml`,
#: `architecture.toml`, `delivery.toml` la usan- y por eso se filtra por nombre y
#: no por fichero.
RATIONALE_KEY = "rationale"


def _without_rationale(data: Mapping[str, Any]) -> dict[str, Any]:
    """Elimina las subtablas de justificacion antes de resolver.

    La prosa que EXPLICA un valor no es un valor, y meterla en la configuracion
    efectiva tiene una consecuencia que no se ve hasta que ocurre: entra en el
    `ConfigFingerprint`, de modo que reescribir un comentario para aclararlo
    cambiaria la huella de la configuracion y con ella la identidad de toda
    corrida posterior. Dos experimentos identicos dejarian de parecerlo por una
    correccion de estilo.

    Se filtra por nombre en lugar de por fichero porque la convencion es del
    proyecto entero, y porque un fichero nuevo que la siga debe quedar cubierto
    sin que nadie se acuerde de anadirlo a una lista.
    """
    return {
        key: _without_rationale(value) if isinstance(value, Mapping) else value
        for key, value in data.items()
        if key != RATIONALE_KEY
    }


__all__ = ["RATIONALE_KEY", "TomlFileProvider"]
