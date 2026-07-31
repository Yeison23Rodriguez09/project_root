"""Motor de optimizacion: ajusta parametros de un candidato ya descubierto.

    StrategySpec(CANDIDATE) -> mutaciones -> ObjectivePort -> mejor variante

Busqueda por ascenso: parte del candidato, muta un parametro cada vez y se queda
con la variante si puntua mas. Una mutacion minima por paso es lo que hace
atribuible la mejora; mutar varias cosas a la vez produce resultados que nadie
puede explicar.

Lo que este motor NO hace:

    no descubre       recibe el candidato, no lo genera
    no valida         no parte en folds ni ejecuta pruebas estadisticas
    no promociona     lo que sale sigue siendo CANDIDATE

Y lo mas importante de todo: **la puntuacion es dentro de muestra**. El
optimizador ajusta sobre los mismos datos que mira, asi que su mejor resultado es
por construccion optimista. Por eso el campo se llama `in_sample_score` y no
`score`: un nombre neutro invitaria a leerlo como rendimiento, y esa confusion es
la que walk-forward existe para deshacer.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.core.determinism import rng_for
from app.core.exceptions import InvariantViolation
from app.core.types import LifecycleState
from app.domain.value_objects.strategy_spec import StrategySpec
from app.shared.ports import (
    DatasetRepositoryPort,
    ObjectivePort,
    SearchSpacePort,
)

SEED_NAMESPACE = "optimization"


@dataclass(frozen=True, slots=True)
class OptimizationResult:
    """Mejor variante encontrada y como se llego a ella.

    Attributes:
        best: Variante con mejor puntuacion dentro de muestra. Sigue en estado
            `CANDIDATE`: optimizar no aporta evidencia.
        in_sample_score: Su puntuacion. Nombre explicito porque es lo unico que
            impide leerla como rendimiento esperado.
        baseline_score: Puntuacion del candidato de partida. Sin ella no se
            puede saber si la busqueda aporto algo o solo gasto computo.
        evaluations: Variantes distintas evaluadas de verdad.
        improved: Si la busqueda mejoro sobre el punto de partida.
        seed: Semilla de la corrida.
        dataset_fingerprint: Serie sobre la que se ajusto.
    """

    best: StrategySpec
    in_sample_score: float
    baseline_score: float
    evaluations: int
    seed: int
    dataset_fingerprint: str

    @property
    def improved(self) -> bool:
        return self.in_sample_score > self.baseline_score

    @property
    def gain(self) -> float:
        return self.in_sample_score - self.baseline_score

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": str(self.best.strategy_id),
            "in_sample_score": self.in_sample_score,
            "baseline_score": self.baseline_score,
            "gain": self.gain,
            "improved": self.improved,
            "evaluations": self.evaluations,
            "seed": self.seed,
            "dataset_fingerprint": self.dataset_fingerprint,
            "best": self.best.to_dict(),
        }


class OptimizationEngine:
    """Ascenso por mutacion sobre el espacio de parametros de un candidato."""

    def __init__(
        self,
        *,
        catalog: DatasetRepositoryPort,
        space: SearchSpacePort,
        objective: ObjectivePort,
    ) -> None:
        self._catalog = catalog
        self._space = space
        self._objective = objective

    def optimize(
        self,
        spec: StrategySpec,
        dataset_fingerprint: str,
        *,
        iterations: int,
        seed: int,
    ) -> OptimizationResult:
        """Ajusta `spec` durante `iterations` pasos sobre la serie indicada.

        Raises:
            InvariantViolation: `iterations` no es positivo, o el candidato no
                esta en estado `CANDIDATE`.
            DataSourceError: la huella no esta catalogada o el artefacto se
                reproceso.
        """
        if iterations < 1:
            raise InvariantViolation("Hay que dar al menos una iteracion", iterations=iterations)
        if spec.state is not LifecycleState.CANDIDATE:
            # Optimizar algo ya validado invalidaria su evidencia sin decirlo:
            # los numeros archivados dejarian de corresponder al spec.
            raise InvariantViolation(
                "Solo se optimizan candidatos", state=str(spec.state)
            )

        bars = self._catalog.get(dataset_fingerprint)
        rng = rng_for(seed, SEED_NAMESPACE, str(spec.strategy_id), iterations)

        baseline = float(self._objective.score(spec, bars))
        best, best_score = spec, baseline
        seen = {str(spec.strategy_id)}

        for _ in range(iterations):
            variant = self._space.mutate(best, rng)
            key = str(variant.strategy_id)
            if key in seen:
                # Ya se evaluo: volver a puntuarla gastaria computo y, peor,
                # inflaria el contador de evaluaciones haciendo creer que la
                # busqueda exploro mas de lo que exploro.
                continue
            seen.add(key)
            score = float(self._objective.score(variant, bars))
            if score > best_score:
                best, best_score = variant, score

        return OptimizationResult(
            best=best,
            in_sample_score=best_score,
            baseline_score=baseline,
            evaluations=len(seen),
            seed=seed,
            dataset_fingerprint=dataset_fingerprint,
        )

    def optimize_all(
        self,
        specs: tuple[StrategySpec, ...],
        dataset_fingerprint: str,
        *,
        iterations: int,
        seed: int,
    ) -> tuple[OptimizationResult, ...]:
        """Optimiza un lote, cada candidato con su propia semilla derivada.

        Derivada del `strategy_id` y no del indice en la lista: reordenar el
        lote no debe cambiar el resultado de ninguno de sus miembros.
        """
        return tuple(
            self.optimize(spec, dataset_fingerprint, iterations=iterations, seed=seed)
            for spec in specs
        )


__all__ = ["SEED_NAMESPACE", "OptimizationEngine", "OptimizationResult"]
