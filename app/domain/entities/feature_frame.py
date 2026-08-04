"""Contenedor de features ya calculadas, alineadas a una serie de barras.

Es lo que reciben los bloques de senal en vez de `Bars` crudo, y la diferencia
importa por dos motivos:

    calculo unico   dos bloques que usan `ema_20` la comparten. Sin contenedor,
                    una estrategia con cinco bloques recalcularia el mismo
                    indicador cinco veces por cada evaluacion, y discovery
                    evalua miles.
    trazabilidad    lo que se evaluo queda enumerado. Un bloque que recibiera
                    `Bars` podria calcular cualquier cosa por dentro, y no
                    habria forma de saber que entro en la decision.

Vive en `domain` y no en `research` por la matriz: `shared.ports.SignalBlockFn`
tiene que poder tiparlo, y `shared` solo ve `core` y `domain`. Si viviera en
research, el puerto seguiria diciendo `frame: Any`.

Es un CONTENEDOR y no una calculadora: no sabe calcular nada. Quien las computa
es `app.research.features.frame`, que si conoce el catalogo. Lo contrario
obligaria a `domain` a importar `research` y romperia la regla de dependencia.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

import numpy as np

from app.core.exceptions import InvariantViolation
from app.core.types import FloatArray
from app.domain.entities.bars import Bars


def feature_key(name: str, params: Mapping[str, Any]) -> str:
    """Clave canonica de una feature parametrizada: `ema_20`, `atr_14`.

    Se compone del nombre y los VALORES, en el orden en que el catalogo declara
    los parametros. Ese orden es fijo -vive en el `ComponentEntry`- asi que la
    clave es estable entre corridas y entre maquinas, que es lo que permite
    usarla como identidad de cache.

    Se usan los valores y no `nombre=valor` porque la clave aparece en informes y
    artefactos, y `ema_20` se lee de un vistazo donde `ema_period20` no.
    """
    if not params:
        return name
    return "_".join([name, *(_render(value) for value in params.values())])


def _render(value: Any) -> str:
    """Texto estable para un valor de parametro.

    Los flotantes se normalizan: `20.0` y `20` deben producir la misma clave,
    porque son el mismo periodo y una cache que los distinguiera calcularia dos
    veces lo mismo.
    """
    if isinstance(value, bool):
        return "on" if value else "off"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


@dataclass(frozen=True, slots=True)
class FeatureFrame:
    """Barras mas sus features calculadas, indexadas por clave canonica.

    Attributes:
        bars: Serie sobre la que se calculo todo. Las features estan alineadas
            barra a barra con ella.
        features: Valores por clave canonica (`ema_20`, `rsi_14`...).
        warmups: Calentamiento declarado de cada feature. Se guarda aparte y no
            dentro de `metadata` porque es LOAD-BEARING: el calentamiento de una
            estrategia es el maximo de los de sus features, y de ahi sale cuantas
            barras iniciales hay que descartar en cada fold. Enterrarlo en un
            mapa sin tipo invitaria a olvidarlo.
        metadata: Contexto libre de la construccion (huella del dataset,
            version del catalogo...). No participa en ningun calculo.
    """

    bars: Bars
    features: Mapping[str, FloatArray]
    warmups: Mapping[str, int]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        expected = len(self.bars)
        for key, values in self.features.items():
            if values.size != expected:
                raise InvariantViolation(
                    "Feature desalineada con las barras",
                    feature=key,
                    length=int(values.size),
                    bars=expected,
                )
            if values.dtype != np.float64:
                raise InvariantViolation(
                    "Una feature debe ser float64", feature=key, dtype=str(values.dtype)
                )
        missing = set(self.features) - set(self.warmups)
        if missing:
            # Sin su calentamiento una feature no puede descontarse del arranque,
            # y las primeras barras del fold entrarian contaminadas.
            raise InvariantViolation(
                "Hay features sin calentamiento declarado", features=sorted(missing)
            )

        frozen: dict[str, FloatArray] = {}
        for key, values in self.features.items():
            copy = np.array(values, dtype=np.float64, copy=True)
            copy.setflags(write=False)
            frozen[key] = copy
        object.__setattr__(self, "features", MappingProxyType(frozen))
        object.__setattr__(self, "warmups", MappingProxyType(dict(self.warmups)))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    def __len__(self) -> int:
        """Numero de BARRAS, no de features: es lo que indexan los bloques."""
        return len(self.bars)

    def __contains__(self, key: object) -> bool:
        return key in self.features

    def __getitem__(self, key: str) -> FloatArray:
        """Valores de una feature.

        Raises:
            InvariantViolation: no se pidio al construir el frame. Es un error de
                programacion y no de datos: un bloque que declara sus features no
                puede recibir un frame sin ellas.
        """
        try:
            return self.features[key]
        except KeyError as error:
            raise InvariantViolation(
                "El frame no contiene esa feature",
                feature=key,
                available=sorted(self.features),
            ) from error

    def get(self, name: str, params: Mapping[str, Any]) -> FloatArray:
        """Atajo por nombre y parametros, sin componer la clave a mano."""
        return self[feature_key(name, params)]

    def require(self, *keys: str) -> None:
        """Comprueba de golpe que estan todas las que un bloque necesita.

        Falla con la lista COMPLETA de las que faltan y no con la primera: quien
        depura prefiere una pasada a cinco.
        """
        missing = [key for key in keys if key not in self.features]
        if missing:
            raise InvariantViolation(
                "Al frame le faltan features",
                missing=missing,
                available=sorted(self.features),
            )

    @property
    def warmup(self) -> int:
        """Barras iniciales no fiables del frame entero.

        Es el MAXIMO de los calentamientos, no la suma ni el de la primera: un
        cruce de medias de 20 y 50 no es evaluable hasta la barra 50, y tomar
        cualquier otro agregado arrastraria treinta barras contaminadas al
        inicio de cada fold.
        """
        return max(self.warmups.values(), default=0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": str(self.bars.symbol),
            "timeframe": str(self.bars.timeframe),
            "bars": len(self.bars),
            "features": sorted(self.features),
            "warmups": dict(sorted(self.warmups.items())),
            "warmup": self.warmup,
            "metadata": dict(self.metadata),
        }


__all__ = ["FeatureFrame", "feature_key"]
