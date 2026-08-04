"""Objetos de valor de la evidencia: cuanta confianza merece un resultado.

Familia hermana de `PerformanceMetrics` y deliberadamente separada de ella
(ADR-0010). Son dos preguntas distintas y mezclarlas es como se pierde el control
del proceso cientifico:

    PerformanceMetrics   COMO gano        retorno, Sharpe, drawdown, CAGR...
    ValidationMetrics    SI ES CREIBLE    walk-forward, pruebas estadisticas

Un Sharpe de 3.0 no dice nada sobre si sobrevivira fuera de muestra, y una
eficiencia walk-forward alta no dice nada sobre cuanto gano. Un unico objeto con
las dos cosas invita a rankear por rentabilidad y a mirar la confianza despues,
que es exactamente el orden que `SYSTEM_MODEL.md` prohibe: "Promocion: no evalua
rentabilidad, evalua confianza".

El fichero se llama `validation_metrics.py` y no `validation.py` porque
`app/core/validation.py` ya existe con otro significado -`ValidationReport`
acumula violaciones de regla, no evidencia estadistica- y `architecture.toml`
prohibe modulos duplicados fuera de su lista de repeticiones permitidas.

Estos tipos se definen ahora, antes que los motores que los produciran. El
paquete `walkforward` y el paquete `validation` estan vacios; declarar aqui la
forma del resultado evita que cada motor invente la suya y que el contrato
aparezca de golpe cuando ya haya codigo encima.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any

from app.core.exceptions import InvariantViolation


@dataclass(frozen=True, slots=True)
class StatisticalTestResult:
    """Resultado de una prueba estadistica, con la forma que promete su puerto.

    `shared.ports.StatisticalTestPort` declara que "toda prueba devuelve la misma
    forma: estadistico, p-valor y veredicto, asi la capa de promocion puede
    componer pruebas heterogeneas sin conocerlas". Esa forma existia como promesa
    en prosa y como `Mapping[str, Any]` en la firma. Aqui se convierte en tipo.

    `passed` se guarda ademas del `p_value` a proposito: el umbral que separa
    "significativo" de "ruido" es una decision de politica -vive en la
    configuracion de promocion- y no una propiedad de la prueba. Registrar el
    veredicto junto al p-valor permite auditar mas tarde con que umbral se
    decidio, en lugar de tener que reconstruirlo.

    Attributes:
        name: Identificador estable de la prueba. Es la clave con la que viaja
            en `ValidationMetrics.tests` y la que aparece en los artefactos.
        statistic: Valor del estadistico de contraste.
        p_value: Probabilidad de observar algo al menos tan extremo bajo la
            hipotesis nula. En [0, 1].
        passed: Veredicto segun el umbral vigente cuando se ejecuto.
    """

    name: str
    statistic: float
    p_value: float
    passed: bool

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise InvariantViolation("Una prueba estadistica necesita nombre")
        if not (0.0 <= self.p_value <= 1.0):
            raise InvariantViolation("p_value fuera de [0,1]", test=self.name, p_value=self.p_value)

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "statistic": self.statistic,
            "p_value": self.p_value,
            "passed": self.passed,
        }


@dataclass(frozen=True, slots=True)
class WalkForwardMetrics:
    """Evidencia producida por un walk-forward. Mide; no decide.

    `wfe` -eficiencia walk-forward- es el cociente entre el rendimiento fuera de
    muestra y el rendimiento dentro de muestra. No pertenece a
    `PerformanceMetrics` porque es una relacion entre DOS evaluaciones y no una
    propiedad de una: un backtest simple no conoce la particion IS/OOS, y
    obligarle a declarar el campo produciria un `None` estructural que ensena a
    los consumidores a ignorarlo (ADR-0010).

    NO contiene decision de promocion. La promocion es una autoridad distinta
    -`shared.ports.PromotionPolicyPort` la emite- y guardarla aqui haria que el
    motor que mide cargue el veredicto de quien decide. Es la misma frontera que
    `architecture.toml` protege al mantener `promotion` fuera del `depends` de
    `discovery`: quien busca no promociona, y quien mide no decide.

    Attributes:
        folds: Numero de particiones evaluadas. Metrica de contexto obligatoria:
            una eficiencia calculada sobre dos folds no es evidencia.
        is_return: Retorno agregado dentro de muestra, en fraccion.
        oos_return: Retorno agregado fuera de muestra, en fraccion.
        wfe: `oos_return / is_return`. `None` cuando el rendimiento dentro de
            muestra no es positivo, porque entonces el cociente no significa
            nada. Mismo criterio que `PerformanceMetrics.profit_factor`, y por el
            mismo motivo: un infinito propagado contamina rankings y artefactos.
        stability: Fraccion de folds con resultado fuera de muestra positivo, en
            [0, 1]. Distingue una eficiencia alta sostenida de una que depende de
            un unico fold afortunado, que es la forma mas comun de sobreajuste
            que sobrevive a un walk-forward mal leido.
    """

    folds: int
    is_return: float
    oos_return: float
    wfe: float | None
    stability: float

    def __post_init__(self) -> None:
        if self.folds < 0:
            raise InvariantViolation("folds no puede ser negativo", folds=self.folds)
        if not (0.0 <= self.stability <= 1.0):
            raise InvariantViolation("stability fuera de [0,1]", stability=self.stability)
        if self.wfe is not None and self.is_return <= 0.0:
            raise InvariantViolation(
                "wfe exige rendimiento dentro de muestra positivo",
                is_return=self.is_return,
                wfe=self.wfe,
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "folds": self.folds,
            "is_return": self.is_return,
            "oos_return": self.oos_return,
            "wfe": self.wfe,
            "stability": self.stability,
        }


@dataclass(frozen=True, slots=True)
class ValidationMetrics:
    """Toda la evidencia disponible sobre un candidato, en un solo objeto.

    Es lo que `PromotionPolicyPort` evalua. Reune las dos fuentes de confianza
    -la particion temporal y las pruebas estadisticas- sin mezclarlas con el
    rendimiento.

    `tests` es un mapa y no un conjunto de campos fijos a proposito. Fijar
    `pbo`, `reality_check` y `spa` como atributos obligaria a la capa de
    promocion a conocer cada prueba que exista, y `StatisticalTestPort` se
    diseno precisamente para lo contrario. Anadir una prueba nueva no debe tocar
    este tipo ni la politica que lo lee.

    Attributes:
        walk_forward: Evidencia temporal, o `None` si el candidato no ha pasado
            por walk-forward todavia. El `None` aqui SI es informativo -significa
            "no evaluado"- a diferencia del que se evito en `PerformanceMetrics`.
        tests: Pruebas ejecutadas, indexadas por nombre.
        overfitting_score: Medida agregada de sobreajuste, en [0, 1] si se
            informa, donde 0 es sin evidencia de sobreajuste. `None` mientras
            ninguna prueba la aporte.
    """

    walk_forward: WalkForwardMetrics | None = None
    tests: Mapping[str, StatisticalTestResult] = field(default_factory=dict)
    overfitting_score: float | None = None

    def __post_init__(self) -> None:
        if self.overfitting_score is not None and not (0.0 <= self.overfitting_score <= 1.0):
            raise InvariantViolation(
                "overfitting_score fuera de [0,1]", score=self.overfitting_score
            )
        for key, result in self.tests.items():
            if key != result.name:
                raise InvariantViolation(
                    "La clave de una prueba no coincide con su nombre",
                    key=key,
                    name=result.name,
                )
        # Copia envuelta: nadie puede anadir ni sustituir una prueba despues de
        # construir la evidencia, ni siquiera quien conserve el dict original.
        object.__setattr__(self, "tests", MappingProxyType(dict(self.tests)))

    @property
    def is_evaluated(self) -> bool:
        """Hay alguna evidencia, de cualquier tipo.

        Un candidato sin evidencia no se rechaza por malo: se rechaza por no
        haber sido evaluado, y esas dos situaciones exigen acciones distintas.
        """
        return self.walk_forward is not None or bool(self.tests)

    def to_dict(self) -> dict[str, Any]:
        return {
            "walk_forward": self.walk_forward.to_dict() if self.walk_forward else None,
            "tests": {name: result.to_dict() for name, result in sorted(self.tests.items())},
            "overfitting_score": self.overfitting_score,
            "is_evaluated": self.is_evaluated,
        }


__all__ = ["StatisticalTestResult", "ValidationMetrics", "WalkForwardMetrics"]
