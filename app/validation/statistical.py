"""Contrastes de hipotesis sobre las puntuaciones por fold.

Todas las pruebas de este modulo son UNILATERALES y contrastan

    H0: la estrategia no tiene ventaja
    H1: la estrategia tiene ventaja POSITIVA

La direccion no es un detalle. Con contraste bilateral, una estrategia que
pierde en los seis folds produce p = 0.031 y quedaria sellada como
significativa: lo seria, pierde de forma muy consistente. Una bateria que
premia a un perdedor consistente es peor que no tener bateria.

Ninguna prueba lee el umbral: `alpha` entra por parametro. El p-valor es una
propiedad del dato; el veredicto es politica, y separarlos permite auditar mas
tarde con que umbral se decidio en lugar de reconstruirlo.
"""

from __future__ import annotations

from math import comb

import numpy as np

from app.core.determinism import rng_for
from app.core.exceptions import InvariantViolation
from app.core.types import FloatArray
from app.domain.value_objects.validation_metrics import StatisticalTestResult

SEED_NAMESPACE = "validation"

#: Tolerancia relativa al comparar el estadistico observado contra la nula.
#
# La asignacion de signos "todo positivo" reproduce la suma observada, pero por
# un camino de sumas distinto: sin margen, el error de redondeo puede dejar
# fuera del recuento al propio valor observado y producir un p-valor menor que
# el minimo alcanzable, que es imposible por construccion.
_TOLERANCE = 1e-12


def minimum_folds_for(alpha: float) -> int:
    """Folds necesarios para que `alpha` sea alcanzable.

    El p-valor unilateral mas pequeno que una prueba de signos puede producir
    con n folds es 1/2^n. Por debajo de este minimo el contraste esta condenado
    de antemano y un rechazo no dice nada sobre la estrategia.
    """
    if not (0.0 < alpha < 1.0):
        raise InvariantViolation("alpha fuera de (0,1)", alpha=alpha)
    folds = 1
    while 1.0 / (2.0**folds) > alpha:
        folds += 1
    return folds


def _binomial_tail(n: int, k: int) -> float:
    """P(X >= k) con X ~ Binomial(n, 1/2). Exacta, sin aproximar."""
    if k <= 0:
        return 1.0
    if k > n:
        return 0.0
    return sum(comb(n, i) for i in range(k, n + 1)) / (2.0**n)


class SignTest:
    """Prueba de signos exacta sobre el numero de folds rentables.

    Solo mira el SIGNO de cada fold, no su magnitud. Es su virtud: no supone
    normalidad, no supone varianza constante y un unico fold extraordinario no
    puede arrastrar el resultado. Es tambien su limite -desperdicia informacion-
    y por eso se acompana de la prueba de permutacion, que si usa magnitudes.

    Los folds con puntuacion exactamente cero se excluyen del recuento, como es
    estandar: un cero no aporta evidencia direccional en ninguno de los dos
    sentidos, y contarlo como fracaso sesgaria la prueba contra la estrategia.
    """

    @property
    def name(self) -> str:
        return "sign_test"

    def run(
        self,
        *,
        is_scores: FloatArray,
        oos_scores: FloatArray,
        alpha: float,
        seed: int,
    ) -> StatisticalTestResult:
        """Cuenta folds positivos y contrasta contra una moneda justa.

        Es determinista y no consume `seed`: el p-valor sale de una suma de
        coeficientes binomiales, no de un muestreo.
        """
        del is_scores, seed  # La prueba mira el fuera de muestra y no muestrea.
        scores = np.asarray(oos_scores, dtype=np.float64)
        effective = scores[scores != 0.0]
        positive = int(np.count_nonzero(effective > 0.0))

        p_value = _binomial_tail(len(effective), positive) if len(effective) else 1.0
        return StatisticalTestResult(
            name=self.name,
            statistic=float(positive),
            p_value=p_value,
            passed=p_value <= alpha,
        )


class SignFlipPermutationTest:
    """Permutacion por inversion de signos sobre la media fuera de muestra.

    Bajo H0 -sin ventaja- el resultado de un fold es simetrico alrededor de
    cero: ganar 3 o perder 3 son igual de probables. Invertir signos genera
    entonces una nula legitima sin suponer forma alguna de distribucion.

    Se prefiere a invertir signos sobre las barras porque respeta la unidad de
    observacion: los folds son independientes por construccion -no se solapan y
    hay purga entre ellos-, mientras que dos barras consecutivas no lo son en
    absoluto.

    Con pocos folds la nula se ENUMERA entera y el p-valor es exacto: sin
    muestreo, sin semilla y sin variabilidad entre corridas. El resultado no
    oculta cual de los dos caminos se uso.
    """

    def __init__(self, *, exact_max_folds: int = 16, monte_carlo_samples: int = 20_000) -> None:
        if exact_max_folds < 1:
            raise InvariantViolation("exact_max_folds debe ser positivo")
        if monte_carlo_samples < 1:
            raise InvariantViolation("monte_carlo_samples debe ser positivo")
        self._exact_max_folds = exact_max_folds
        self._samples = monte_carlo_samples

    @property
    def name(self) -> str:
        return "sign_permutation"

    def is_exact_for(self, folds: int) -> bool:
        """Si con `folds` folds la nula se enumera entera.

        Se responde desde el numero de folds y no desde lo que ocurrio en la
        ultima corrida: guardar aquello convertiria la prueba en un objeto con
        estado, y entonces la respuesta dependeria del orden de ejecucion.
        """
        return folds <= self._exact_max_folds

    def run(
        self,
        *,
        is_scores: FloatArray,
        oos_scores: FloatArray,
        alpha: float,
        seed: int,
    ) -> StatisticalTestResult:
        del is_scores
        scores = np.asarray(oos_scores, dtype=np.float64)
        folds = len(scores)
        if folds == 0:
            raise InvariantViolation("No hay folds que permutar")

        observed = float(scores.sum())
        exact = self.is_exact_for(folds)
        null = self._enumerate(scores) if exact else self._sample(scores, seed)

        # Margen a favor de contar el observado: sin el, el redondeo podria
        # producir un p-valor por debajo del minimo alcanzable.
        margin = _TOLERANCE * max(1.0, abs(observed))
        at_least_as_extreme = int(np.count_nonzero(null >= observed - margin))

        if exact:
            # El vector "todo positivo" pertenece a la enumeracion, asi que el
            # recuento ya incluye lo observado y el p-valor nunca es cero.
            p_value = at_least_as_extreme / null.size
        else:
            # Correccion de Davison-Hinkley: un p-valor de Monte Carlo igual a
            # cero afirmaria una precision que el muestreo no tiene.
            p_value = (at_least_as_extreme + 1) / (null.size + 1)

        return StatisticalTestResult(
            name=self.name,
            statistic=float(scores.mean()),
            p_value=min(1.0, p_value),
            passed=p_value <= alpha,
        )

    def _enumerate(self, scores: FloatArray) -> FloatArray:
        """Las 2^n asignaciones de signo, sin muestrear."""
        folds = len(scores)
        combinations = np.arange(2**folds, dtype=np.int64)[:, None]
        bits = (combinations >> np.arange(folds, dtype=np.int64)) & 1
        signs = (1 - 2 * bits).astype(np.float64)
        return np.asarray(signs @ np.asarray(scores, dtype=np.float64))

    def _sample(self, scores: FloatArray, seed: int) -> FloatArray:
        """Nula por muestreo cuando enumerar no cabe."""
        rng = rng_for(seed, SEED_NAMESPACE, self.name, len(scores))
        signs = rng.integers(0, 2, size=(self._samples, len(scores))) * 2 - 1
        return np.asarray(signs.astype(np.float64) @ np.asarray(scores, dtype=np.float64))


__all__ = [
    "SEED_NAMESPACE",
    "SignFlipPermutationTest",
    "SignTest",
    "minimum_folds_for",
]
