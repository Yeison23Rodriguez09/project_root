"""Motor de descubrimiento de arquitecturas.

Lee del catalogo de datasets y produce candidatos. Nada mas.

    DatasetRepositoryPort -> Bars -> SearchSpacePort -> StrategySpec(CANDIDATE)

Lo que este motor NO hace, y la lista es el contrato:

    no habla con MT5      solo conoce `DatasetRepositoryPort`
    no optimiza           no evalua, no puntua, no ordena por rendimiento
    no valida             no parte en folds ni ejecuta pruebas estadisticas
    no promociona         no toca el estado de ciclo de vida mas alla de CANDIDATE

La matriz ya lo impone: `discovery` no tiene `broker` en su `depends`, asi que un
import hacia el terminal ni siquiera compila. Lo que este fichero anade es que
tampoco lo intente por una via indirecta: recibe el catalogo, no una fuente.

Que un candidato sea bueno es una pregunta de otras fases. Aqui solo se responde
que ensamblajes EXISTEN y merecen evaluarse.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from app.core.determinism import rng_for
from app.core.exceptions import InvariantViolation
from app.core.types import LifecycleState
from app.domain.entities.bars import Bars
from app.domain.value_objects.strategy_spec import StrategySpec
from app.shared.ports import DatasetRepositoryPort, SearchSpacePort

#: Espacio de nombres de la semilla derivada. Aisla la aleatoriedad de discovery
#: de la de cualquier otro consumidor que use la misma semilla maestra.
SEED_NAMESPACE = "discovery"


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    """Que se descubrio, sobre que datos y con que semilla.

    Attributes:
        candidates: Ensamblajes propuestos, todos en estado `CANDIDATE`.
        dataset_fingerprint: Huella de la serie sobre la que se busco. Sin ella
            el resultado no es reproducible: la misma semilla sobre datos
            reprocesados produce otras cosas.
        requested: Cuantos candidatos se pidieron.
        seed: Semilla maestra de la corrida.
        space: Descripcion del espacio explorado, con su cardinalidad.
    """

    candidates: tuple[StrategySpec, ...]
    dataset_fingerprint: str
    requested: int
    seed: int
    space: dict[str, Any]

    @property
    def exhausted(self) -> bool:
        """El espacio se agoto antes de completar lo pedido.

        Es informacion, no un error: significa que la busqueda cubrio todo lo
        que habia. Ocultarlo haria creer que se exploro una fraccion cuando se
        exploro el total, o al reves.
        """
        return len(self.candidates) < self.requested

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_fingerprint": self.dataset_fingerprint,
            "requested": self.requested,
            "produced": len(self.candidates),
            "exhausted": self.exhausted,
            "seed": self.seed,
            "space": self.space,
            "candidates": [spec.to_dict() for spec in self.candidates],
        }


class DiscoveryEngine:
    """Propone ensamblajes sobre una serie ya catalogada."""

    def __init__(self, *, catalog: DatasetRepositoryPort, space: SearchSpacePort) -> None:
        self._catalog = catalog
        self._space = space

    def discover(self, dataset_fingerprint: str, *, count: int, seed: int) -> DiscoveryResult:
        """Descubre hasta `count` candidatos sobre la serie indicada.

        La serie se carga aunque el generador no la use para muestrear. No es
        gasto inutil: es lo que garantiza que el candidato se propuso sobre datos
        que EXISTEN y cuya huella coincide con la registrada. Proponer
        estrategias para un dataset ausente o reprocesado produciria candidatos
        que nadie puede reevaluar, y el fallo aparecerian tres fases mas tarde.

        Raises:
            InvariantViolation: `count` no es positivo.
            DataSourceError: la huella no esta catalogada, o el artefacto se
                reproceso y ya no coincide con ella.
        """
        if count < 1:
            raise InvariantViolation("Hay que pedir al menos un candidato", count=count)

        bars = self._catalog.get(dataset_fingerprint)
        self._reject_mismatched_series(bars)

        rng = rng_for(seed, SEED_NAMESPACE, dataset_fingerprint, count)
        candidates = tuple(self._space.sample(rng, count))
        self._reject_non_candidates(candidates)

        return DiscoveryResult(
            candidates=candidates,
            dataset_fingerprint=dataset_fingerprint,
            requested=count,
            seed=seed,
            space=self._describe_space(),
        )

    # -- interno -------------------------------------------------------------

    def _reject_mismatched_series(self, bars: Bars) -> None:
        """El espacio y la serie deben hablar del mismo instrumento.

        Buscar arquitecturas de EURUSD M15 sobre una serie de XAUUSD H1 produce
        candidatos sintacticamente validos y semanticamente absurdos, y nada
        aguas abajo lo detectaria: el spec lleva su propio simbolo y parece
        coherente consigo mismo.
        """
        symbol = getattr(self._space, "symbol", None)
        timeframe = getattr(self._space, "timeframe", None)
        if symbol is None or timeframe is None:
            return
        if symbol != bars.symbol or timeframe != bars.timeframe:
            raise InvariantViolation(
                "El espacio de busqueda y la serie describen instrumentos distintos",
                space=f"{symbol} {timeframe}",
                dataset=f"{bars.symbol} {bars.timeframe}",
            )

    @staticmethod
    def _reject_non_candidates(candidates: Sequence[StrategySpec]) -> None:
        """Discovery no puede emitir nada que no sea un candidato.

        Un ensamblaje recien generado no tiene evidencia de ninguna clase.
        Emitirlo en cualquier otro estado saltaria la puerta de promocion desde
        el primer eslabon de la cadena cientifica.
        """
        intruders = sorted(
            {str(spec.state) for spec in candidates if spec.state is not LifecycleState.CANDIDATE}
        )
        if intruders:
            raise InvariantViolation(
                "Discovery solo puede producir candidatos", states=intruders
            )

    def _describe_space(self) -> dict[str, Any]:
        describe = getattr(self._space, "describe", None)
        if callable(describe):
            described: dict[str, Any] = describe()
            return described
        return {"cardinality": self._space.cardinality()}


__all__ = ["SEED_NAMESPACE", "DiscoveryEngine", "DiscoveryResult"]
