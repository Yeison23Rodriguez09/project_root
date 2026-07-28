"""Proveedor de ficheros YAML. OPCIONAL.

TOML es el formato oficial. YAML se soporta pero no se exige, y por eso este es
el unico proveedor que puede no estar disponible en un entorno dado.

`import yaml` dentro de un modulo llamado `yaml.py` resuelve al paquete de
terceros y no a si mismo: Python 3 usa imports absolutos, de modo que el nombre
local no ensombrece al global.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from app.core.config.provenance import Priority
from app.core.config.resolver import ConfigLayer
from app.core.exceptions import ConfigNotFound, ConfigValidationError, ProviderUnavailable


class YamlFileProvider:
    """Lee un fichero YAML. Proveedor OPCIONAL.

    Si PyYAML no esta instalado el proveedor lanza `ProviderUnavailable`, nunca
    `ImportError`. La diferencia importa: un `ImportError` propagado obligaria al
    ensamblador a conocer los detalles de implementacion del proveedor para
    decidir si continuar. Con un error de dominio, el ensamblador decide segun
    `required`: si la capa era opcional, la omite, lo registra y **el sistema
    sigue funcionando**; si era obligatoria, aborta.

    Lo que no se hace nunca es ignorar el fichero en silencio. Un fichero de
    configuracion presente que no se aplica y no avisa es la causa de las horas
    mas frustrantes de depuracion que existen.
    """

    def __init__(
        self, path: Path | str, priority: Priority, *, required: bool = False
    ) -> None:
        self._path = Path(path)
        self._priority = priority
        self._required = required

    @staticmethod
    def is_available() -> bool:
        """Si el proveedor puede operar en este entorno.

        Se expone para que el ensamblador pueda decidir la composicion de capas
        sin provocar un fallo, y para que el diagnostico de arranque informe de
        que formatos hay disponibles.
        """
        try:
            import yaml  # noqa: F401  (sonda de disponibilidad: solo interesa si importa)
        except ImportError:
            return False
        return True

    def load(self) -> ConfigLayer | None:
        if not self._path.is_file():
            if self._required:
                raise ConfigNotFound(
                    "Fichero de configuracion obligatorio ausente", path=str(self._path)
                )
            return None
        try:
            import yaml
        except ImportError as exc:
            raise ProviderUnavailable(
                "Hay configuracion YAML pero PyYAML no esta instalado",
                path=str(self._path),
                required=self._required,
                remedy="Instala 'pyyaml' o convierte el fichero a TOML, que es el formato oficial.",
            ) from exc
        loaded = yaml.safe_load(self._path.read_text(encoding="utf-8"))
        if loaded is None:
            return ConfigLayer.from_mapping(self._priority, str(self._path), {})
        if not isinstance(loaded, Mapping):
            raise ConfigValidationError(
                "La raiz de un fichero de configuracion debe ser un mapa",
                path=str(self._path),
                found=type(loaded).__name__,
            )
        return ConfigLayer.from_mapping(self._priority, str(self._path), loaded)


__all__ = ["YamlFileProvider"]
