"""Especificacion declarativa de una estrategia.

Una estrategia NO es una clase que alguien escribe. Es un dato: una
composicion de bloques con parametros. Esta decision habilita todo lo demas y
es lo que hace posible `optimization_engine = "architecture-search"`.

* Discovery puede generar, mutar y recombinar estrategias sin generar codigo.
* Dos estrategias son iguales si su spec canonico es igual; la deduplicacion
  es exacta y barata.
* El zoo guarda specs, no objetos serializados con pickle: una estrategia
  promovida hace dos anos se reconstruye hoy con el codigo actual, y la
  discrepancia entre el resultado archivado y el recalculado es en si misma
  una senal de alarma valiosa.
* El identificador de estrategia es el hash de su contenido, asi que es
  imposible que dos cosas distintas compartan nombre.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Self

from app.core.determinism import stable_hash
from app.core.exceptions import InvariantViolation
from app.core.types import BlockName, LifecycleState, StrategyId, Symbol, Timeframe


class CombineMode:
    """Modos de combinacion de bloques de entrada.

    Se define como clase de constantes y no como `StrEnum` para que discovery
    pueda proponer modos nuevos desde configuracion sin modificar el dominio.
    """

    ALL = "all"  # conjuncion: todos los bloques deben coincidir en direccion
    ANY = "any"  # disyuncion, con resolucion explicita de conflictos
    MAJORITY = "majority"  # voto por mayoria simple
    WEIGHTED = "weighted"  # suma ponderada de `strength` contra un umbral

    ALL_MODES: tuple[str, ...] = (ALL, ANY, MAJORITY, WEIGHTED)


@dataclass(frozen=True, slots=True)
class BlockSpec:
    """Referencia parametrizada a un bloque registrado.

    Attributes:
        name: Clave en el registro correspondiente. No se valida contra el
            registro aqui: el spec debe seguir siendo un dato puro,
            serializable y leible sin importar ningun motor. La resolucion
            ocurre al materializar la estrategia.
        params: Parametros del bloque. Solo primitivas JSON: nada de callables
            ni objetos, porque el spec debe poder viajar a disco y volver.
        weight: Peso en modos de combinacion ponderada. Ignorado en el resto.
        enabled: Permite desactivar un bloque sin borrarlo, conservando la
            trazabilidad de la variante en discovery.
    """

    name: BlockName
    params: Mapping[str, Any] = field(default_factory=dict)
    weight: float = 1.0
    enabled: bool = True

    def __post_init__(self) -> None:
        if not str(self.name).strip():
            raise InvariantViolation("BlockSpec sin nombre")
        if self.weight < 0:
            raise InvariantViolation("weight no puede ser negativo", weight=self.weight)
        for key, value in self.params.items():
            if not isinstance(value, (int, float, str, bool, type(None), list, tuple)):
                raise InvariantViolation(
                    "Parametro de bloque no serializable",
                    block=str(self.name),
                    param=key,
                    type=type(value).__name__,
                )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": str(self.name),
            "params": {k: self.params[k] for k in sorted(self.params)},
            "weight": self.weight,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        return cls(
            name=BlockName(str(data["name"])),
            params=dict(data.get("params", {})),
            weight=float(data.get("weight", 1.0)),
            enabled=bool(data.get("enabled", True)),
        )


@dataclass(frozen=True, slots=True)
class StrategySpec:
    """Composicion completa y auto-contenida de una estrategia.

    El orden de los bloques dentro de cada lista no altera la semantica, pero
    si alteraria el hash. Por eso `canonical()` los ordena antes de hashear:
    dos specs que describen la misma estrategia obtienen el mismo id aunque
    discovery los haya generado en orden distinto. Sin esa normalizacion, la
    deduplicacion no funcionaria y la busqueda gastaria presupuesto evaluando
    la misma estrategia varias veces.

    Attributes:
        contexts: Bloques contextuales (filtros). No generan direccion; vetan.
        entries: Bloques de entrada, combinados segun `combine_mode`.
        exits: Bloques de salida. Se evaluan en OR: la primera condicion que se
            cumple cierra la posicion.
        risk: Parametros del perfil de riesgo asociado a esta estrategia.
        combine_threshold: Umbral para `WEIGHTED`, como fraccion del peso total.
            Ignorado en el resto de modos.
        state: Estado en el ciclo de vida. Nace siempre como `CANDIDATE`; solo
            la politica de promocion puede avanzarlo.
    """

    symbol: Symbol
    timeframe: Timeframe
    entries: tuple[BlockSpec, ...]
    exits: tuple[BlockSpec, ...] = ()
    contexts: tuple[BlockSpec, ...] = ()
    combine_mode: str = CombineMode.ALL
    combine_threshold: float = 0.5
    risk: Mapping[str, Any] = field(default_factory=dict)
    state: LifecycleState = LifecycleState.CANDIDATE
    label: str = ""
    notes: str = ""

    def __post_init__(self) -> None:
        if not self.entries:
            raise InvariantViolation("Una estrategia necesita al menos un bloque de entrada")
        if self.combine_mode not in CombineMode.ALL_MODES:
            raise InvariantViolation(
                "Modo de combinacion desconocido",
                combine_mode=self.combine_mode,
                allowed=list(CombineMode.ALL_MODES),
            )
        if not (0.0 <= self.combine_threshold <= 1.0):
            raise InvariantViolation(
                "combine_threshold fuera de [0,1]", value=self.combine_threshold
            )
        self._reject_exact_duplicates()

    def _reject_exact_duplicates(self) -> None:
        """Prohibe el mismo bloque con los mismos parametros dos veces.

        Duplicar un bloque identico duplica su peso sin anadir informacion. En
        `WEIGHTED` eso sesga la combinacion de forma invisible, y en discovery
        genera variantes que parecen distintas y no lo son.

        El mismo bloque con parametros distintos si es legitimo: EMA(20) y
        EMA(50) son dos senales diferentes.
        """
        for group_name in ("entries", "exits", "contexts"):
            group: tuple[BlockSpec, ...] = getattr(self, group_name)
            enabled = [b for b in group if b.enabled]
            keys = [stable_hash(b.to_dict()) for b in enabled]
            if len(set(keys)) == len(keys):
                continue
            # Se acumulan los nombres por huella y se denuncian los que aparecen
            # mas de una vez. El error nombra el bloque repetido, no solo dice
            # que hay uno: en un spec generado por discovery, con veinte bloques,
            # "hay un duplicado" no permite corregir nada.
            names_by_key: dict[str, list[str]] = {}
            for block, key in zip(enabled, keys, strict=True):
                names_by_key.setdefault(key, []).append(str(block.name))
            repeated = sorted(
                {names[0] for names in names_by_key.values() if len(names) > 1}
            )
            raise InvariantViolation(
                f"Bloques duplicados en {group_name}", blocks=repeated
            )

    # -- identidad ----------------------------------------------------------

    def canonical(self) -> dict[str, Any]:
        """Forma canonica, independiente del orden de generacion.

        Excluye `state`, `label` y `notes`: son metadatos operativos. Dos specs
        identicos en composicion deben compartir id aunque uno este promovido y
        el otro rechazado, porque son la misma estrategia y su evidencia
        historica debe poder cruzarse.
        """
        return {
            "symbol": str(self.symbol),
            "timeframe": str(self.timeframe),
            "combine_mode": self.combine_mode,
            "combine_threshold": self.combine_threshold,
            "entries": sorted((b.to_dict() for b in self.entries), key=repr),
            "exits": sorted((b.to_dict() for b in self.exits), key=repr),
            "contexts": sorted((b.to_dict() for b in self.contexts), key=repr),
            "risk": {k: self.risk[k] for k in sorted(self.risk)},
        }

    @property
    def strategy_id(self) -> StrategyId:
        """Identidad derivada del contenido.

        Formato: `{simbolo}.{timeframe}.{hash}`. Legible en una ruta de
        artefacto y colisiona solo si dos estrategias son de verdad iguales.
        """
        digest = stable_hash(self.canonical())
        return StrategyId(f"{self.symbol}.{self.timeframe}.{digest}")

    @property
    def active_blocks(self) -> tuple[BlockSpec, ...]:
        return tuple(b for b in (*self.contexts, *self.entries, *self.exits) if b.enabled)

    @property
    def complexity(self) -> int:
        """Numero de bloques activos.

        Es la penalizacion natural en discovery: entre dos estrategias con
        rendimiento equivalente se prefiere la de menor complejidad, porque
        tiene menos grados de libertad y por tanto menos sobreajuste latente.
        """
        return len(self.active_blocks)

    @property
    def n_parameters(self) -> int:
        """Grados de libertad totales. Entrada del ajuste por multiplicidad."""
        return sum(len(b.params) for b in self.active_blocks)

    # -- serializacion ------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.canonical(),
            "strategy_id": str(self.strategy_id),
            "state": str(self.state),
            "label": self.label,
            "notes": self.notes,
            "complexity": self.complexity,
            "n_parameters": self.n_parameters,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Self:
        def blocks(key: str) -> tuple[BlockSpec, ...]:
            raw: Sequence[Mapping[str, Any]] = data.get(key, ())
            return tuple(BlockSpec.from_dict(item) for item in raw)

        return cls(
            symbol=Symbol(str(data["symbol"])),
            timeframe=Timeframe(str(data["timeframe"])),
            entries=blocks("entries"),
            exits=blocks("exits"),
            contexts=blocks("contexts"),
            combine_mode=str(data.get("combine_mode", CombineMode.ALL)),
            combine_threshold=float(data.get("combine_threshold", 0.5)),
            risk=dict(data.get("risk", {})),
            state=LifecycleState(str(data.get("state", LifecycleState.CANDIDATE))),
            label=str(data.get("label", "")),
            notes=str(data.get("notes", "")),
        )

    def __repr__(self) -> str:
        entries = "+".join(str(b.name) for b in self.entries if b.enabled)
        return f"StrategySpec({self.symbol} {self.timeframe} {entries} id={self.strategy_id})"


__all__ = ["BlockSpec", "CombineMode", "StrategySpec"]
