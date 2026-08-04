"""Construccion de `FeatureFrame`: resuelve el catalogo y calcula una sola vez.

    [(nombre, params), ...]  ->  FEATURES  ->  FeatureFrame

Aqui vive la cache, y su unidad es la CLAVE CANONICA y no la peticion: dos
bloques que pidan `ema` con periodo 20 -uno escribiendolo `20` y otro `20.0`-
comparten el calculo, porque `feature_key` normaliza ambos a `ema_20`. Sin esa
normalizacion la cache tendria dos entradas para el mismo indicador y el ahorro
desapareceria justo en el caso para el que existe.

Separado de `domain` porque calcular exige conocer el catalogo, y `domain` no
puede importar `research`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from app.core.exceptions import InvariantViolation
from app.domain.entities.bars import Bars
from app.domain.entities.feature_frame import FeatureFrame, feature_key
from app.research.features.registry import FEATURES

#: Peticion de feature: nombre en el catalogo y parametros a resolver.
FeatureRequest = tuple[str, Mapping[str, Any]]


def build_frame(
    bars: Bars,
    requests: Iterable[FeatureRequest],
    *,
    metadata: Mapping[str, Any] | None = None,
) -> FeatureFrame:
    """Calcula las features pedidas y las empaqueta con sus calentamientos.

    Los parametros se resuelven contra los `ParamSpec` declarados antes de
    calcular nada, asi que una errata en un nombre falla aqui y no produce un
    indicador corriendo con su valor por defecto.

    Raises:
        UnknownComponent: se pidio una feature que no esta en el catalogo.
        InvalidParameter: un parametro no existe o esta fuera de sus cotas.
        InvariantViolation: dos peticiones producen la misma clave con
            parametros distintos.
    """
    values: dict[str, Any] = {}
    warmups: dict[str, int] = {}
    resolved_by_key: dict[str, Mapping[str, Any]] = {}

    for name, params in requests:
        entry = FEATURES.get(name)
        resolved = entry.resolve(params)
        key = feature_key(name, resolved)

        previous = resolved_by_key.get(key)
        if previous is not None:
            if previous != resolved:
                # Dos parametrizaciones distintas colisionando en la misma clave
                # significaria que una pisa a la otra y el bloque leeria valores
                # que no pidio. Es un fallo de la convencion de nombres, no del
                # llamante, y tiene que ser ruidoso.
                raise InvariantViolation(
                    "Dos features distintas comparten clave canonica",
                    key=key,
                    first=dict(previous),
                    second=dict(resolved),
                )
            continue  # Ya calculada: es exactamente la misma peticion.

        resolved_by_key[key] = resolved
        values[key] = entry.fn(bars, **resolved)
        warmups[key] = entry.warmup(resolved)

    return FeatureFrame(
        bars=bars,
        features=values,
        warmups=warmups,
        metadata=dict(metadata or {}),
    )


def warmup_of(requests: Iterable[FeatureRequest]) -> int:
    """Calentamiento agregado de un conjunto de peticiones, sin calcular nada.

    Permite saber cuantas barras iniciales descartar antes de gastar un solo
    ciclo en calcular indicadores. Es el MAXIMO y no la suma: un cruce de medias
    de 20 y 50 es evaluable en la barra 50.
    """
    return max(
        (
            FEATURES.get(name).warmup(FEATURES.get(name).resolve(params))
            for name, params in requests
        ),
        default=0,
    )


__all__ = ["FeatureRequest", "build_frame", "warmup_of"]
