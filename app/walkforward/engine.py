"""Motor de walk-forward: ajusta dentro de muestra, mide fuera.

    fold_i:  IS -> fitter -> variante -> OOS -> objetivo -> puntuacion

La propiedad que lo hace valer: **la variante que se puntua fuera de muestra se
ajusto SOLO con datos de dentro**. Reajustar sobre el OOS, o elegir entre
variantes mirando su resultado OOS, convierte el fuera de muestra en dentro de
muestra y todos los numeros posteriores en ficcion.

Por eso este motor no elige. Ajusta en IS, mide en OOS y agrega. La decision de
promocionar la toma otro, con esta evidencia delante.

No importa `optimization`: la matriz no lo permite, y la direccion es la
correcta -la validacion es una metodologia, no un motor de busqueda-. El
ajustador entra por `StrategyFitterPort`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.exceptions import InvariantViolation
from app.core.types import LifecycleState
from app.domain.entities.bars import Bars
from app.domain.value_objects.strategy_spec import StrategySpec
from app.domain.value_objects.time_range import Fold
from app.domain.value_objects.validation_metrics import WalkForwardMetrics
from app.shared.ports import DatasetRepositoryPort, ObjectivePort, StrategyFitterPort
from app.walkforward import partition

#: Esquemas de particionado admitidos.
ROLLING = "rolling"
ANCHORED = "anchored"
SCHEMES: tuple[str, ...] = (ROLLING, ANCHORED)


@dataclass(frozen=True, slots=True)
class FoldOutcome:
    """Lo que ocurrio en un fold, con el detalle que lo hace auditable."""

    fold: Fold
    fitted: StrategySpec
    is_score: float
    oos_score: float
    is_bars: int
    oos_bars: int

    @property
    def degraded(self) -> bool:
        """El fuera de muestra empeoro respecto al ajuste.

        Es lo normal y no una alarma: si NUNCA degrada, lo sospechoso es la
        particion.
        """
        return self.oos_score < self.is_score

    def to_dict(self) -> dict[str, Any]:
        return {
            "fold": self.fold.to_dict(),
            "strategy_id": str(self.fitted.strategy_id),
            "is_score": self.is_score,
            "oos_score": self.oos_score,
            "is_bars": self.is_bars,
            "oos_bars": self.oos_bars,
            "degraded": self.degraded,
        }


@dataclass(frozen=True, slots=True)
class WalkForwardRun:
    """Resultado completo de una corrida, con su evidencia agregada."""

    spec: StrategySpec
    scheme: str
    outcomes: tuple[FoldOutcome, ...]
    metrics: WalkForwardMetrics
    dataset_fingerprint: str
    seed: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": str(self.spec.strategy_id),
            "scheme": self.scheme,
            "dataset_fingerprint": self.dataset_fingerprint,
            "seed": self.seed,
            "metrics": self.metrics.to_dict(),
            "folds": [outcome.to_dict() for outcome in self.outcomes],
        }


class WalkForwardEngine:
    """Ejecuta walk-forward sobre una serie catalogada."""

    def __init__(
        self,
        *,
        catalog: DatasetRepositoryPort,
        fitter: StrategyFitterPort,
        objective: ObjectivePort,
    ) -> None:
        self._catalog = catalog
        self._fitter = fitter
        self._objective = objective

    def run(
        self,
        spec: StrategySpec,
        dataset_fingerprint: str,
        *,
        folds: int,
        oos_bars: int,
        is_bars: int | None = None,
        purge_bars: int = 0,
        scheme: str = ROLLING,
        seed: int = 0,
    ) -> WalkForwardRun:
        """Parte la serie, ajusta en cada IS y mide en cada OOS.

        Args:
            is_bars: Tamano de la ventana de ajuste. En `anchored` es el minimo
                inicial, porque la ventana crece.
            purge_bars: Separacion entre IS y OOS. Un valor de cero solo es
                legitimo si el calentamiento maximo de todas las features es
                cero, que casi nunca es cierto: cualquier feature con ventana
                calculada al principio del OOS usa datos del final del IS.

        Raises:
            InvariantViolation: esquema desconocido o candidato en estado que no
                admite ajuste.
            InsufficientHistory: la serie no alcanza para los folds pedidos.
        """
        if scheme not in SCHEMES:
            raise InvariantViolation(
                "Esquema de walk-forward desconocido", scheme=scheme, allowed=list(SCHEMES)
            )
        if spec.state is not LifecycleState.CANDIDATE:
            raise InvariantViolation("Walk-forward evalua candidatos", state=str(spec.state))

        bars = self._catalog.get(dataset_fingerprint)
        window = is_bars if is_bars is not None else max(1, len(bars) // (folds + 1))
        partitions = self._partition(
            bars,
            scheme=scheme,
            folds=folds,
            is_bars=window,
            oos_bars=oos_bars,
            purge_bars=purge_bars,
        )

        outcomes = tuple(self._evaluate(spec, bars, fold, seed) for fold in partitions)
        return WalkForwardRun(
            spec=spec,
            scheme=scheme,
            outcomes=outcomes,
            metrics=self._aggregate(outcomes),
            dataset_fingerprint=dataset_fingerprint,
            seed=seed,
        )

    # -- interno -------------------------------------------------------------

    @staticmethod
    def _partition(
        bars: Bars, *, scheme: str, folds: int, is_bars: int, oos_bars: int, purge_bars: int
    ) -> tuple[Fold, ...]:
        if scheme == ROLLING:
            return partition.rolling(
                bars, folds=folds, is_bars=is_bars, oos_bars=oos_bars, purge_bars=purge_bars
            )
        return partition.anchored(
            bars, folds=folds, oos_bars=oos_bars, min_is_bars=is_bars, purge_bars=purge_bars
        )

    def _evaluate(self, spec: StrategySpec, bars: Bars, fold: Fold, seed: int) -> FoldOutcome:
        """Ajusta con el IS del fold y puntua con su OOS.

        El corte se hace por rango temporal y no por indice para que la purga
        declarada en el fold se respete: `Bars.between` es semiabierto, igual
        que `TimeRange`, asi que ninguna barra puede aparecer en los dos tramos.
        """
        in_sample = bars.between(fold.in_sample.start_ns, fold.in_sample.end_ns)
        out_of_sample = bars.between(fold.out_of_sample.start_ns, fold.out_of_sample.end_ns)

        if len(in_sample) == 0 or len(out_of_sample) == 0:
            raise InvariantViolation(
                "Un fold quedo sin barras",
                fold=fold.index,
                is_bars=len(in_sample),
                oos_bars=len(out_of_sample),
            )

        # La semilla se deriva del indice del fold: cada uno explora de forma
        # independiente y reordenarlos no cambiaria ningun resultado.
        fitted = self._fitter.fit(spec, in_sample, seed=seed + fold.index)

        return FoldOutcome(
            fold=fold,
            fitted=fitted,
            is_score=float(self._objective.score(fitted, in_sample)),
            oos_score=float(self._objective.score(fitted, out_of_sample)),
            is_bars=len(in_sample),
            oos_bars=len(out_of_sample),
        )

    @staticmethod
    def _aggregate(outcomes: tuple[FoldOutcome, ...]) -> WalkForwardMetrics:
        """Agrega los folds en la evidencia que consumira la promocion.

        `wfe` es `None` cuando el rendimiento dentro de muestra no es positivo:
        un cociente contra un denominador no positivo no significa nada, y
        publicarlo como numero invitaria a rankear por el.
        """
        if not outcomes:
            return WalkForwardMetrics(
                folds=0, is_return=0.0, oos_return=0.0, wfe=None, stability=0.0
            )

        is_total = sum(o.is_score for o in outcomes) / len(outcomes)
        oos_total = sum(o.oos_score for o in outcomes) / len(outcomes)
        positive = sum(1 for o in outcomes if o.oos_score > 0)

        return WalkForwardMetrics(
            folds=len(outcomes),
            is_return=is_total,
            oos_return=oos_total,
            wfe=(oos_total / is_total) if is_total > 0 else None,
            stability=positive / len(outcomes),
        )


__all__ = ["ANCHORED", "ROLLING", "SCHEMES", "FoldOutcome", "WalkForwardEngine", "WalkForwardRun"]
