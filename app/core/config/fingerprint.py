"""Huella de la configuracion efectiva y del conjunto reproducible de una corrida.

Esta pieza suele aparecer demasiado tarde y entonces obliga a refactorizar medio
proyecto, porque cada motor ya ha inventado su propia forma de identificar una
corrida. Se construye ahora.

La idea es simple y el efecto grande: cualquier artefacto -backtest,
walk-forward, discovery, promocion o live- registra cuatro huellas.

    dataset      que datos exactos se usaron
    config       que configuracion estaba activa
    code         que version del codigo la ejecuto
    strategy     que composicion se evaluo

Con esas cuatro mas la semilla, una corrida es reconstruible sin adivinar nada.
Sin ellas, un resultado de hace seis meses es un numero sin procedencia.

Sobre el algoritmo. `core.determinism.stable_hash` usa blake2b truncado a 64 bits
para identificadores internos cortos, legibles en una ruta de fichero. Aqui se usa
SHA-256 completo: es la huella que va al registro de auditoria y la que alguien
externo podria querer verificar con herramientas estandar. Dos algoritmos con dos
propositos distintos, no una inconsistencia.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Final

from app.core.config.provenance import ResolutionTrace
from app.core.determinism import canonical_json
from app.core.exceptions import ConfigError

#: Algoritmo de la huella publica. Fijado en el dato para que un artefacto
#: antiguo siga sabiendo con que se calculo su propia huella.
ALGORITHM: Final[str] = "sha256"

#: Version del esquema de normalizacion. Cambiarla invalida la comparabilidad
#: con huellas anteriores, asi que se versiona explicitamente en lugar de
#: cambiar el calculo en silencio.
SCHEMA_VERSION: Final[int] = 1


@dataclass(frozen=True, slots=True)
class ConfigFingerprint:
    """Identificador de una configuracion efectiva.

    Attributes:
        digest: SHA-256 hexadecimal de la configuracion normalizada.
        n_keys: Numero de claves que participaron. Va junto al digest a
            proposito: si dos huellas difieren, saber si tambien difiere el
            numero de claves distingue "cambio un valor" de "cambio el esquema".
        excluded: Claves deliberadamente fuera del calculo, ordenadas.
        algorithm / schema_version: Como se calculo, para que sea verificable
            dentro de dos anos.
    """

    digest: str
    n_keys: int
    excluded: tuple[str, ...] = ()
    algorithm: str = ALGORITHM
    schema_version: int = SCHEMA_VERSION

    @property
    def short(self) -> str:
        """Primeros 16 caracteres, para nombres de fichero y logs."""
        return self.digest[:16]

    def matches(self, other: ConfigFingerprint) -> bool:
        """Comparacion honesta entre dos huellas.

        Exige que coincidan tambien algoritmo, version de esquema y exclusiones.
        Dos digests iguales calculados con exclusiones distintas no significan la
        misma configuracion, y compararlos solo por el digest daria un falso
        positivo justo en el caso que mas importa.
        """
        return (
            self.digest == other.digest
            and self.algorithm == other.algorithm
            and self.schema_version == other.schema_version
            and self.excluded == other.excluded
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "digest": self.digest,
            "short": self.short,
            "n_keys": self.n_keys,
            "excluded": list(self.excluded),
            "algorithm": self.algorithm,
            "schema_version": self.schema_version,
        }

    def __str__(self) -> str:
        return f"{self.algorithm}:{self.short}"


def normalize(values: Mapping[str, Any], *, exclude: Sequence[str] = ()) -> dict[str, Any]:
    """Forma canonica de una configuracion, lista para hashear.

    Args:
        values: Configuracion efectiva con claves planas.
        exclude: Claves que NO entran en la huella. Se admite porque hay valores
            que no afectan al resultado -la ruta de salida de artefactos, el nivel
            de log- y sin excluirlos dos corridas identicas tendrian huellas
            distintas por un detalle operativo.

            Cada exclusion es peligrosa y debe justificarse: excluir de mas hace
            que dos configuraciones que SI producen resultados distintos parezcan
            la misma. Por eso la lista viaja dentro de la huella y `matches` la
            compara.

    Raises:
        ConfigError: si `exclude` menciona una clave que no existe. Suele ser una
            exclusion obsoleta que ya no protege nada y que alguien creeria activa.
    """
    excluded = tuple(sorted(set(exclude)))
    unknown = [key for key in excluded if key not in values]
    if unknown:
        raise ConfigError(
            "Exclusiones de huella que no corresponden a ninguna clave",
            unknown=unknown,
            hint="Retiralas: una exclusion obsoleta parece protegerte y no lo hace.",
        )
    return {key: values[key] for key in sorted(values) if key not in set(excluded)}


def fingerprint(
    source: ResolutionTrace | Mapping[str, Any],
    *,
    exclude: Sequence[str] = (),
) -> ConfigFingerprint:
    """Calcula la huella de una configuracion efectiva.

    Acepta la traza completa o el mapa plano. Con la traza se usa unicamente el
    VALOR de cada clave, nunca su procedencia: la misma configuracion escrita en
    un fichero o inyectada por la CLI debe producir la misma huella, porque
    produce el mismo resultado. La procedencia se guarda aparte, en la traza.
    """
    values = source.flat() if isinstance(source, ResolutionTrace) else dict(source)
    normalized = normalize(values, exclude=exclude)
    payload = canonical_json(
        {"schema_version": SCHEMA_VERSION, "config": normalized}
    ).encode("utf-8")
    return ConfigFingerprint(
        digest=hashlib.sha256(payload).hexdigest(),
        n_keys=len(normalized),
        excluded=tuple(sorted(set(exclude))),
    )


@dataclass(frozen=True, slots=True)
class RunFingerprint:
    """Las cuatro huellas que hacen reconstruible una corrida, mas la semilla.

    Attributes:
        dataset: Huella de los datos exactos (`Bars.digest()`).
        config: Huella de la configuracion efectiva.
        code: Version del codigo. Idealmente el SHA del commit; si el arbol
            tiene cambios sin confirmar debe reflejarlo con un sufijo, porque
            "corri sobre main" es falso cuando hay ediciones locales.
        strategy: Identificador de la composicion evaluada, o `None` en corridas
            que no evaluan una estrategia concreta (una ingesta, un benchmark de
            features).
        seed: Semilla maestra.

    Se declara `frozen` y con `slots` porque acompana a cada artefacto y no debe
    poder alterarse despues de escribirse.
    """

    dataset: str
    config: str
    code: str
    seed: int
    strategy: str | None = None

    def __post_init__(self) -> None:
        for name in ("dataset", "config", "code"):
            if not str(getattr(self, name)).strip():
                raise ConfigError(
                    f"RunFingerprint sin {name}: el artefacto no seria reconstruible",
                    field=name,
                )

    @property
    def digest(self) -> str:
        """Huella compuesta de la corrida completa.

        Es el identificador que responde "es esto exactamente la misma corrida".
        Se deriva de las cuatro partes mas la semilla, asi que cambiar cualquiera
        lo cambia.
        """
        payload = canonical_json(
            {
                "dataset": self.dataset,
                "config": self.config,
                "code": self.code,
                "strategy": self.strategy,
                "seed": self.seed,
            }
        ).encode("utf-8")
        return hashlib.sha256(payload).hexdigest()

    @property
    def short(self) -> str:
        return self.digest[:16]

    def differs_from(self, other: RunFingerprint) -> list[str]:
        """Que partes cambiaron respecto a otra corrida.

        Es la funcion que convierte "el resultado cambio" en un diagnostico. Si
        solo difiere `code`, el cambio viene del codigo; si difiere `dataset`,
        los datos se reprocesaron y la comparacion no es valida.
        """
        return [
            name
            for name in ("dataset", "config", "code", "strategy", "seed")
            if getattr(self, name) != getattr(other, name)
        ]

    def to_dict(self) -> dict[str, Any]:
        return {
            "digest": self.digest,
            "short": self.short,
            "dataset": self.dataset,
            "config": self.config,
            "code": self.code,
            "strategy": self.strategy,
            "seed": self.seed,
        }

    def __str__(self) -> str:
        return f"run:{self.short}"


__all__ = [
    "ALGORITHM",
    "SCHEMA_VERSION",
    "ConfigFingerprint",
    "RunFingerprint",
    "fingerprint",
    "normalize",
]
