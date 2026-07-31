"""Motor de validacion: somete la evidencia de folds a contraste estadistico.

    evidencia -> [pruebas] -> ValidationMetrics + veredicto auditable

El veredicto tiene TRES valores y no dos. `INCONCLUSIVE` no es un adorno: con
pocos folds el p-valor minimo alcanzable puede quedar ya por encima de alpha, y
entonces el rechazo lo causa el montaje del experimento y no la estrategia.
Colapsarlo en `False` haria descartar candidatos por un defecto del reparto en
folds, sin que nada en la salida lo delatara.

Lo que este motor NO hace:

    no promociona      emite evidencia; el zoo lo gobierna `promotion`
    no reajusta        no toca parametros ni vuelve a mirar los datos
    no mide            el rendimiento es asunto de `analytics`

La entrada llega por `WalkForwardEvidencePort` y no por import: la matriz
declara `validation` y `walkforward` como capacidades hermanas, no como
consumidor y proveedor. Al ser el puerto estructural, el resultado de
walk-forward lo cumple sin conocerlo.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import numpy as np

from app.core.exceptions import InvariantViolation
from app.core.types import FloatArray, LifecycleState, Severity
from app.core.validation import ValidationReport
from app.domain.value_objects.strategy_spec import StrategySpec
from app.domain.value_objects.validation_metrics import (
    StatisticalTestResult,
    ValidationMetrics,
    WalkForwardMetrics,
)
from app.shared.ports import FoldTestPort, WalkForwardEvidencePort
from app.validation.statistical import minimum_folds_for


class Verdict(StrEnum):
    """Resultado del contraste. Tres valores, no dos.

    `REJECTED` dice "esta estrategia no supera el contraste".
    `INCONCLUSIVE` dice "este experimento no puede responder la pregunta".

    Exigen acciones opuestas -descartar el candidato contra repartir en mas
    folds y repetir- y confundirlas es como se pierden candidatos buenos por un
    montaje pobre.
    """

    PASSED = "passed"
    REJECTED = "rejected"
    INCONCLUSIVE = "inconclusive"


@dataclass(frozen=True, slots=True)
class ValidationOutcome:
    """Evidencia estadistica sobre un candidato, con su veredicto y su motivo.

    Attributes:
        metrics: Toda la evidencia, en el tipo que `promotion` ya sabe leer.
        verdict: Veredicto del contraste. NO es una decision de promocion.
        report: Hallazgos que sustentan el veredicto. Un rechazo sin motivo
            registrado obliga a repetir el trabajo para entenderlo.
        alpha: Umbral vigente cuando se decidio, para poder auditarlo despues.
        folds: Folds sobre los que se contrasto.
        minimum_folds: Folds que `alpha` exigia. Si supera a `folds`, el
            veredicto es `INCONCLUSIVE` por construccion.
    """

    spec: StrategySpec
    metrics: ValidationMetrics
    verdict: Verdict
    report: ValidationReport
    alpha: float
    folds: int
    minimum_folds: int
    dataset_fingerprint: str
    seed: int

    @property
    def passed(self) -> bool:
        """Atajo deliberadamente estrecho: solo `PASSED` cuenta como superado.

        Un `INCONCLUSIVE` no es un aprobado, y leerlo como tal dejaria pasar a
        Risk candidatos sin evidencia.
        """
        return self.verdict is Verdict.PASSED

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy_id": str(self.spec.strategy_id),
            "verdict": str(self.verdict),
            "alpha": self.alpha,
            "folds": self.folds,
            "minimum_folds": self.minimum_folds,
            "dataset_fingerprint": self.dataset_fingerprint,
            "seed": self.seed,
            "metrics": self.metrics.to_dict(),
            "report": self.report.to_dict(),
        }


class ValidationEngine:
    """Aplica una bateria de contrastes a la evidencia de un walk-forward."""

    def __init__(
        self,
        *,
        tests: Sequence[FoldTestPort],
        alpha: float = 0.05,
    ) -> None:
        if not tests:
            raise InvariantViolation("Una bateria vacia validaria cualquier cosa")
        names = [test.name for test in tests]
        if len(set(names)) != len(names):
            # `ValidationMetrics.tests` indexa por nombre: dos pruebas homonimas
            # se pisarian y una desapareceria del informe sin aviso.
            raise InvariantViolation("Dos pruebas comparten nombre", names=names)
        self._tests = tuple(tests)
        self._alpha = alpha
        self._minimum_folds = minimum_folds_for(alpha)

    @property
    def minimum_folds(self) -> int:
        """Folds que `alpha` exige para ser alcanzable."""
        return self._minimum_folds

    def validate(self, evidence: WalkForwardEvidencePort) -> ValidationOutcome:
        """Contrasta la evidencia y emite veredicto con su motivo.

        Raises:
            InvariantViolation: la evidencia no trae folds, o el candidato esta
                en un estado que ya no admite contraste.
        """
        spec = evidence.spec
        if spec.state is not LifecycleState.CANDIDATE:
            # Revalidar algo ya validado produciria una segunda evidencia sobre
            # el mismo spec, y nada diria cual de las dos rige.
            raise InvariantViolation("Validation contrasta candidatos", state=str(spec.state))

        is_scores, oos_scores = self._scores(evidence)
        folds = len(oos_scores)
        report = ValidationReport(subject=str(spec.strategy_id))

        # La potencia se resuelve ANTES de correr las pruebas porque decide con
        # que severidad se anotan sus fallos. Sin folds suficientes el fallo de
        # una prueba no es un rechazo sino un dato no interpretable, y anotarlo
        # como ERROR haria que `report.ok` contradijera al veredicto: quien
        # leyera el informe en vez del veredicto veria un rechazo donde no lo
        # hay, que es justo el colapso que `INCONCLUSIVE` existe para impedir.
        conclusive = folds >= self._minimum_folds
        if not conclusive:
            report.add(
                "INSUFFICIENT_POWER",
                f"Con {folds} folds el p-valor minimo alcanzable es "
                f"{1 / 2**folds:.4f}, por encima de alpha={self._alpha}. "
                f"Hacen falta {self._minimum_folds}.",
                Severity.WARNING,
                folds=folds,
                minimum_folds=self._minimum_folds,
                alpha=self._alpha,
            )

        results = self._run_tests(
            is_scores,
            oos_scores,
            evidence.seed,
            report,
            severity=Severity.ERROR if conclusive else Severity.WARNING,
        )
        walk_forward = self._recompute(is_scores, oos_scores)
        verdict = self._verdict(conclusive, results)

        return ValidationOutcome(
            spec=spec,
            metrics=ValidationMetrics(
                walk_forward=walk_forward,
                tests={result.name: result for result in results},
                overfitting_score=self._overfitting(walk_forward),
            ),
            verdict=verdict,
            report=report,
            alpha=self._alpha,
            folds=folds,
            minimum_folds=self._minimum_folds,
            dataset_fingerprint=evidence.dataset_fingerprint,
            seed=evidence.seed,
        )

    # -- interno -------------------------------------------------------------

    @staticmethod
    def _scores(evidence: WalkForwardEvidencePort) -> tuple[FloatArray, FloatArray]:
        outcomes = tuple(evidence.outcomes)
        if not outcomes:
            raise InvariantViolation("La evidencia no trae ningun fold")
        is_scores = np.array([float(o.is_score) for o in outcomes], dtype=np.float64)
        oos_scores = np.array([float(o.oos_score) for o in outcomes], dtype=np.float64)
        return is_scores, oos_scores

    def _run_tests(
        self,
        is_scores: FloatArray,
        oos_scores: FloatArray,
        seed: int,
        report: ValidationReport,
        *,
        severity: Severity,
    ) -> tuple[StatisticalTestResult, ...]:
        """Corre la bateria entera y anota cada fallo con su motivo.

        Se ejecutan TODAS aunque una ya haya fallado: cortar al primer fallo
        ahorraria computo irrelevante y perderia el resto de p-valores, que son
        lo que permite entender por que se rechazo.
        """
        results: list[StatisticalTestResult] = []
        for test in self._tests:
            result = test.run(
                is_scores=is_scores,
                oos_scores=oos_scores,
                alpha=self._alpha,
                seed=seed,
            )
            if not result.passed:
                report.add(
                    "TEST_NOT_SIGNIFICANT",
                    f"{result.name}: p={result.p_value:.4f} > alpha={self._alpha}",
                    severity,
                    test=result.name,
                    p_value=result.p_value,
                    statistic=result.statistic,
                )
            results.append(result)
        return tuple(results)

    @staticmethod
    def _verdict(conclusive: bool, results: Sequence[StatisticalTestResult]) -> Verdict:
        """Potencia primero, significacion despues.

        El orden importa: sin folds suficientes el resultado de las pruebas no
        es interpretable, asi que un rechazo emitido ahi hablaria del montaje y
        no de la estrategia.
        """
        if not conclusive:
            return Verdict.INCONCLUSIVE

        # Conjuncion y no disyuncion: exigir que TODAS pasen hace el rechazo de
        # la nula mas dificil con cada prueba anadida, no mas facil. Por eso no
        # se aplica correccion por multiplicidad.
        return Verdict.PASSED if all(r.passed for r in results) else Verdict.REJECTED

    @staticmethod
    def _recompute(is_scores: FloatArray, oos_scores: FloatArray) -> WalkForwardMetrics:
        """Reconstruye la evidencia agregada desde las puntuaciones por fold.

        Se recalcula en vez de aceptar la agregacion que traiga la fuente: asi el
        contraste no depende de como agregase quien produjo los folds, y la
        evidencia sigue siendo comparable entre productores distintos.
        """
        is_mean = float(np.mean(is_scores))
        oos_mean = float(np.mean(oos_scores))
        positive = int(np.count_nonzero(oos_scores > 0.0))
        return WalkForwardMetrics(
            folds=len(oos_scores),
            is_return=is_mean,
            oos_return=oos_mean,
            wfe=(oos_mean / is_mean) if is_mean > 0.0 else None,
            stability=positive / len(oos_scores),
        )

    @staticmethod
    def _overfitting(metrics: WalkForwardMetrics) -> float | None:
        """Fraccion de la ventaja dentro de muestra que NO sobrevivio fuera.

        `clip(1 - wfe, 0, 1)`: 0 significa que la ventaja sobrevivio entera, 1
        que no sobrevivio nada o que se dio la vuelta.

        Es DESCRIPTIVA y no un contraste: no lleva p-valor y no participa en el
        veredicto. Publicarla como si fuera una prueba invitaria a rechazar por
        un numero sin hipotesis nula detras. Es `None` exactamente cuando `wfe`
        lo es, porque sin ventaja dentro de muestra no hay nada que pudiera
        sobrevivir.
        """
        if metrics.wfe is None:
            return None
        return float(min(1.0, max(0.0, 1.0 - metrics.wfe)))


__all__ = ["ValidationEngine", "ValidationOutcome", "Verdict"]
