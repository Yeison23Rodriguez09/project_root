"""Lectura de un `StrategySpec` declarado en `configs/strategies/`.

Vive en `app/config` por el mismo motivo que `instruments.py` y con el mismo
contrato (ADR-0005): este paquete es el unico autorizado a abrir un fichero de
configuracion, y una composicion de estrategia -que bloques, con que parametros,
con que perfil de riesgo- es declaracion, no codigo.

La consecuencia arquitectonica es la que importa: ningun motor ni caso de uso
puede importar este modulo. `config` es nivel 4 y los motores son nivel 3;
`application` tampoco lo declara en su `depends`. Todos reciben un `StrategySpec`
ya construido -un objeto de DOMINIO- y no saben si vino de un fichero, de
discovery o del zoo. Es lo que permite que el mismo servicio de backtest evalue
una estrategia escrita a mano y una generada por la busqueda.

Carga ansiosa y validacion en el borde: un bloque inexistente o un parametro mal
escrito debe fallar al leer el fichero, no en la barra 4000 de la corrida. La
validacion profunda -que el bloque exista en el catalogo, que sus parametros
encajen- la hace `compile_strategy`; aqui se comprueba la FORMA, que es lo que
distingue "este TOML no describe una estrategia" de "esta estrategia no se puede
compilar".
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from app.core.exceptions import ConfigNotFound, ConfigValidationError
from app.domain.value_objects.strategy_spec import StrategySpec

#: Extension de una declaracion de estrategia. TOML y no YAML: es el formato
#: oficial del proyecto y esta en la biblioteca estandar, de modo que leer una
#: estrategia nunca puede fallar por una dependencia ausente.
SUFFIX = ".toml"

#: Claves que toda declaracion debe traer. `entries` esta porque una estrategia
#: sin bloque de entrada no propone nada, y `StrategySpec` ya lo rechaza; se
#: comprueba tambien aqui para que el mensaje nombre el FICHERO en lugar de una
#: invariante de dominio sin ruta.
REQUIRED_KEYS: tuple[str, ...] = ("symbol", "timeframe", "entries")


def load_strategy_spec(path: Path | str) -> StrategySpec:
    """Lee una declaracion de estrategia y devuelve el objeto de dominio.

    Args:
        path: Fichero `.toml` con la composicion. La forma es exactamente la que
            `StrategySpec.from_dict` consume, para que un spec guardado por un
            artefacto pueda releerse sin conversion intermedia.

    Raises:
        ConfigNotFound: el fichero no existe o no es un fichero.
        ConfigValidationError: el TOML esta mal formado, le faltan claves o
            describe algo que `StrategySpec` rechaza.
    """
    source = Path(path)
    if not source.is_file():
        raise ConfigNotFound("No existe la declaracion de estrategia", path=str(source))

    try:
        raw: dict[str, Any] = tomllib.loads(source.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigValidationError(
            "La declaracion de estrategia no es TOML valido",
            path=str(source),
            detail=str(exc),
        ) from exc

    missing = sorted(key for key in REQUIRED_KEYS if key not in raw)
    if missing:
        raise ConfigValidationError(
            "La declaracion de estrategia esta incompleta",
            path=str(source),
            missing=missing,
        )

    try:
        return StrategySpec.from_dict(raw)
    except (KeyError, TypeError, ValueError) as exc:
        # Se traduce a error de configuracion PORQUE el origen es un fichero: sin
        # esta traduccion, un `ValueError` de un timeframe mal escrito llegaria a
        # la CLI sin decir de que fichero salio, y el operador tendria que
        # adivinar cual de sus estrategias esta rota.
        raise ConfigValidationError(
            "La declaracion no describe una estrategia valida",
            path=str(source),
            detail=str(exc),
        ) from exc


__all__ = ["REQUIRED_KEYS", "SUFFIX", "load_strategy_spec"]
