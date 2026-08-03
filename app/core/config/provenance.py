"""Procedencia de cada valor de configuracion.

Sin esto, reproducir una corrida de hace tres meses exige adivinar que
configuracion estaba activa. Con esto, cada valor efectivo sabe responder de
donde salio, con que prioridad y que otros valores fueron descartados.

El requisito §10 del contrato original -nombre estable, alcance, valor por
defecto, fuente, validacion, prioridad, comportamiento ante ausencia e impacto-
se satisface entre el esquema tipado y esta traza. El esquema aporta nombre,
tipo, defecto y validacion; la traza aporta fuente y prioridad.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

from app.core.exceptions import ConfigError


def _canonical_pair(key: str, value: Any) -> bytes:
    """Serializacion estable de un par clave-valor para su checksum.

    Se usa `json` de la biblioteca estandar y no orjson: la huella no puede
    depender de que dependencia opcional este instalada. Mismo criterio que en
    `core.determinism`.
    """
    return json.dumps(
        [key, value], sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=repr
    ).encode("utf-8")


class Priority(IntEnum):
    """Prioridad de una fuente de configuracion.

    El orden numerico ES la precedencia: mayor gana. Se declara como enum y no
    como numeros sueltos para que anadir una fuente obligue a decidir
    explicitamente donde encaja, en lugar de elegir un entero libre.

    Los huecos entre valores son deliberados: dejan sitio para intercalar una
    fuente nueva sin renumerar las existentes, lo que cambiaria la precedencia
    de todo el sistema en un commit aparentemente inocuo.
    """

    CODE_DEFAULT = 0  # valor por defecto del esquema
    GLOBAL_FILE = 10  # configs/global.toml
    PROFILE_FILE = 20  # configs/profiles/<perfil>.toml
    SYMBOL_FILE = 30  # configs/symbols/<simbolo>.toml
    TIMEFRAME_FILE = 40  # configs/timeframes/<tf>.toml
    STRATEGY_FILE = 50  # configs/strategies/<id>.toml
    LOCAL_FILE = 60  # configs/local.toml, no versionado
    ENVIRONMENT = 70  # variables QP_*
    COMMAND_LINE = 80  # argumentos explicitos
    RUNTIME_OVERRIDE = 90  # inyeccion en tests y en discovery


@dataclass(frozen=True, slots=True)
class Origin:
    """De donde vino un valor concreto.

    Attributes:
        priority: Prioridad de la fuente.
        locator: Identificador legible: ruta del fichero, nombre de la variable
            de entorno o `--flag`. Es lo que se imprime en el informe.
        line: Linea del fichero, cuando la fuente lo permite. No todas pueden
            aportarla y por eso es opcional; cuando existe, convierte "lo fijo
            global.toml" en "lo fijo global.toml:42".
    """

    priority: Priority
    locator: str
    line: int | None = None

    def __str__(self) -> str:
        suffix = f":{self.line}" if self.line is not None else ""
        return f"{self.locator}{suffix} ({self.priority.name})"

    def to_dict(self) -> dict[str, Any]:
        return {
            "priority": self.priority.name,
            "priority_value": int(self.priority),
            "locator": self.locator,
            "line": self.line,
        }


@dataclass(frozen=True, slots=True)
class ResolvedValue:
    """Un valor efectivo con su historia completa.

    Attributes:
        key: Clave con notacion de punto, p.ej. `risk.max_drawdown_pct`.
        value: Valor que gana.
        origin: Fuente que lo fijo.
        overridden: Candidatos descartados, del mas prioritario al menos. Se
            conservan a proposito: el caso mas confuso al depurar una
            configuracion no es "de donde salio este valor" sino "por que NO
            salio el que yo puse". Guardar los descartados responde a la segunda
            pregunta sin reejecutar nada.
    """

    key: str
    value: Any
    origin: Origin
    overridden: tuple[tuple[Any, Origin], ...] = ()
    resolved_at: int | None = None

    @property
    def is_default(self) -> bool:
        return self.origin.priority is Priority.CODE_DEFAULT

    @property
    def was_contested(self) -> bool:
        """True si mas de una fuente propuso un valor para esta clave."""
        return bool(self.overridden)

    @property
    def history(self) -> tuple[tuple[Any, Origin], ...]:
        """Cadena completa de candidatos, del ganador al mas debil.

        Es la vista que reconstruye exactamente por que un valor acabo siendo el
        que es:

            max_positions = 3  <- --set (COMMAND_LINE)
                            5  <- entorno QP_* (ENVIRONMENT)
                            8  <- profiles/live.toml (PROFILE_FILE)
                            10 <- schema (CODE_DEFAULT)

        En produccion es la diferencia entre diagnosticar en un minuto y pasar
        una tarde comparando ficheros a mano.
        """
        return ((self.value, self.origin), *self.overridden)

    @property
    def checksum(self) -> str:
        """Huella del par clave-valor, independiente de la procedencia.

        Permite comparar dos corridas clave a clave y localizar exactamente que
        valores cambiaron, sin que un cambio de fichero de origen -mover un ajuste
        de `global.toml` a `profiles/dev.toml` sin alterar su valor- aparezca
        como diferencia. Ese movimiento no cambia el resultado y no debe cambiar
        el checksum.
        """
        payload = _canonical_pair(self.key, self.value)
        return hashlib.sha256(payload).hexdigest()[:16]

    def explain(self) -> str:
        """Explicacion de una linea, lista para un informe o un log."""
        head = f"{self.key} = {self.value!r}  <- {self.origin}"
        if not self.overridden:
            return head
        losers = "; ".join(f"{v!r} de {o}" for v, o in self.overridden)
        return f"{head}  [descartado: {losers}]"

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "value": self.value,
            "checksum": self.checksum,
            "origin": self.origin.to_dict(),
            "resolved_at": self.resolved_at,
            "history": [{"value": v, "origin": o.to_dict()} for v, o in self.history],
        }


@dataclass(slots=True)
class ResolutionTrace:
    """Traza completa de una resolucion de configuracion.

    Es mutable durante la construccion y debe tratarse como solo lectura una vez
    devuelta. Se persiste junto a cada corrida: es la mitad del par que hace
    reproducible un experimento, siendo la otra mitad el hash de los datos.
    """

    values: dict[str, ResolvedValue] = field(default_factory=dict)
    sources: list[Origin] = field(default_factory=list)

    def record(self, resolved: ResolvedValue) -> None:
        self.values[resolved.key] = resolved

    def note_source(self, origin: Origin) -> None:
        """Registra una fuente consultada, incluso si no aporto ningun valor.

        Un fichero presente pero vacio y un fichero ausente producen la misma
        configuracion efectiva y son dos situaciones distintas: la segunda suele
        ser una ruta mal escrita. Registrar la consulta las separa.
        """
        self.sources.append(origin)

    def __len__(self) -> int:
        return len(self.values)

    def __iter__(self) -> Iterator[ResolvedValue]:
        return iter(self.values[k] for k in sorted(self.values))

    def __contains__(self, key: object) -> bool:
        return key in self.values

    def get(self, key: str) -> ResolvedValue:
        try:
            return self.values[key]
        except KeyError:
            raise ConfigError(
                f"La clave {key!r} no aparece en la traza de resolucion",
                key=key,
                available=len(self.values),
            ) from None

    def contested(self) -> list[ResolvedValue]:
        """Claves que mas de una fuente intento fijar.

        Es la vista mas util al depurar: donde hay desacuerdo entre ficheros es
        donde se esconden las sorpresas.
        """
        return [v for v in self if v.was_contested]

    def by_priority(self, priority: Priority) -> list[ResolvedValue]:
        return [v for v in self if v.origin.priority is priority]

    def non_default(self) -> list[ResolvedValue]:
        """Valores que alguien cambio respecto al codigo.

        Es el resumen que interesa en la cabecera de un informe: la lista corta
        de lo que hace especial a esta corrida.
        """
        return [v for v in self if not v.is_default]

    def flat(self) -> dict[str, Any]:
        """Configuracion efectiva, sin procedencia. Para consumo del sistema."""
        return {key: self.values[key].value for key in sorted(self.values)}

    def report(self) -> str:
        """Informe legible, ordenado por clave."""
        lines = [f"Configuracion resuelta: {len(self)} claves"]
        lines += [
            f"  fuentes consultadas: {len(self.sources)}",
            "",
        ]
        lines += [f"  {v.explain()}" for v in self]
        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_keys": len(self.values),
            "n_contested": len(self.contested()),
            "sources": [o.to_dict() for o in self.sources],
            "values": [v.to_dict() for v in self],
        }


def flatten(mapping: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
    """Convierte un mapa anidado en claves con notacion de punto.

    La resolucion trabaja sobre claves planas y no sobre arboles anidados. El
    motivo es concreto: fusionar dos arboles recursivamente hace ambiguo si un
    diccionario en un fichero de mayor prioridad SUSTITUYE al de menor o se
    combina con el. Con claves planas la pregunta desaparece, porque cada hoja
    se resuelve por separado y la precedencia es siempre valor contra valor.
    """
    out: dict[str, Any] = {}
    for key, value in mapping.items():
        full = f"{prefix}{key}"
        if isinstance(value, Mapping):
            out.update(flatten(value, f"{full}."))
        else:
            out[full] = value
    return out


def unflatten(flat_map: Mapping[str, Any]) -> dict[str, Any]:
    """Reconstruye el arbol a partir de claves con puntos.

    Raises:
        ConfigError: si una clave intenta ser a la vez hoja y rama, p.ej.
            `risk = 1` junto a `risk.max = 2`. Es una incoherencia real de
            configuracion y resolverla en silencio dejaria al usuario con un
            valor que no puso.
    """
    root: dict[str, Any] = {}
    for key in sorted(flat_map):
        parts = key.split(".")
        node = root
        for part in parts[:-1]:
            existing = node.get(part)
            if existing is None:
                existing = {}
                node[part] = existing
            elif not isinstance(existing, dict):
                raise ConfigError(
                    f"La clave {part!r} es hoja y rama a la vez", key=key, conflict=part
                )
            node = existing
        leaf = parts[-1]
        if isinstance(node.get(leaf), dict):
            raise ConfigError(f"La clave {leaf!r} es rama y hoja a la vez", key=key)
        node[leaf] = flat_map[key]
    return root


__all__ = [
    "Origin",
    "Priority",
    "ResolutionTrace",
    "ResolvedValue",
    "flatten",
    "unflatten",
]
