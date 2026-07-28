"""Proveedores de configuracion. Unico codigo que lee del exterior.

Nivel infraestructura (ADR-0005). Ningun motor puede importar este modulo: un
motor que pudiera leer un fichero introduciria una entrada no declarada y el
resultado dejaria de depender solo de (datos, configuracion, semilla).

Cada proveedor devuelve una `ConfigLayer` con su procedencia. La decision de
que gana no se toma aqui: eso es `app.core.config.resolver`, que es puro.
"""

from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from app.core.config.provenance import Origin, Priority
from app.core.config.resolver import ConfigLayer
from app.core.exceptions import ConfigNotFound, ConfigValidationError, ProviderUnavailable

#: Prefijo de las variables de entorno que la plataforma reconoce.
ENV_PREFIX = "QP_"

#: Separador de niveles en variables de entorno. Doble guion bajo porque el
#: simple forma parte de los nombres de clave (`max_drawdown_pct`), y usarlo
#: como separador haria ambigua toda clave con guion bajo.
ENV_LEVEL_SEPARATOR = "__"


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


class YamlFileProvider:
    """Lee un fichero YAML. Proveedor OPCIONAL.

    TOML es el formato oficial porque `tomllib` esta en la biblioteca estandar y
    por tanto no puede faltar. YAML se soporta pero no se exige.

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
            import yaml  # noqa: F401
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


__all__ = [
    "ENV_LEVEL_SEPARATOR",
    "ENV_PREFIX",
    "CommandLineProvider",
    "ConfigProvider",
    "EnvironmentProvider",
    "MappingProvider",
    "TomlFileProvider",
    "YamlFileProvider",
    "coerce",
]
