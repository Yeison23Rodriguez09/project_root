"""Identidad y procedencia de una corrida. Implementa `RunContextPort`.

Responde las cinco preguntas sin las cuales un artefacto no vale nada seis meses
despues: quien lo genero, con que codigo, con que configuracion, sobre que datos
y con que semilla.

Vive en `monitoring` y no en `core` por una razon concreta: construir el
contexto exige leer el mundo -la version del codigo sale de git, el `run_id` de
un reloj y un contador-, y `core` es puro por contrato. Lo que si es puro es el
OBJETO resultante, que es `RunFingerprint` y ya vive en `core.config`.

El `run_id` se DERIVA, no se sortea. Un `uuid4()` haria que dos ejecuciones
identicas produjeran artefactos con nombres distintos, y entonces "ya calcule
esto" dejaria de poder responderse sin recalcularlo. La regla 4.2 de CLAUDE.md
lo dice de frente: los identificadores aleatorios se inyectan, nunca se
instancian dentro del dominio.
"""

from __future__ import annotations

import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.config.fingerprint import RunFingerprint
from app.core.determinism import stable_hash
from app.core.types import RunId, TimestampNs

#: Valor de `code_version` cuando no se puede interrogar a git. No es un fallo:
#: un despliegue desde un tarball no tiene repositorio y debe poder arrancar.
#: Lo que no puede es MENTIR diciendo que corrio sobre un commit concreto.
UNKNOWN_CODE_VERSION = "unknown"

#: Sufijo que marca un arbol con cambios sin confirmar. Sin el, "corri sobre
#: main" es falso en cuanto alguien edita un fichero, y la comparacion contra
#: otra corrida del mismo commit deja de ser valida sin que nada lo delate.
DIRTY_SUFFIX = "+dirty"


def code_version(root: Path) -> str:
    """Version del codigo: SHA corto del commit, con marca si el arbol esta sucio.

    Se consulta a git en lugar de leer un fichero de version porque el fichero
    se olvida y el commit no. Si git no responde -no hay repositorio, no esta
    instalado, el directorio no es un arbol de trabajo- se devuelve
    `UNKNOWN_CODE_VERSION` en vez de inventar algo: un artefacto que declara una
    version falsa es peor que uno que admite no saberla.
    """
    try:
        head = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        ).stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return UNKNOWN_CODE_VERSION
    if not head:
        return UNKNOWN_CODE_VERSION
    try:
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
            timeout=10,
        ).stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return head
    return f"{head}{DIRTY_SUFFIX}" if dirty else head


def derive_run_id(*, config_hash: str, code: str, seed: int, at_ns: TimestampNs) -> RunId:
    """Identificador reproducible de una corrida.

    Se deriva del contenido -configuracion, codigo, semilla e instante- en lugar
    de sortearse. Dos corridas con los mismos cuatro valores producen el mismo
    identificador, que es lo que permite detectar que algo ya se calculo.

    `at_ns` entra en la derivacion a proposito: sin el, relanzar la misma corrida
    para comprobar el determinismo sobreescribiria los artefactos de la primera
    y ya no habria con que comparar. Como el reloj se inyecta por `ClockPort`, en
    modo determinista `at_ns` es fijo y el identificador tambien.
    """
    return RunId(
        stable_hash({"config": config_hash, "code": code, "seed": int(seed), "at_ns": int(at_ns)})
    )


@dataclass(frozen=True, slots=True)
class RunContext:
    """Procedencia completa de una corrida. Implementa `RunContextPort`.

    Attributes:
        run_id: Identificador derivado del contenido.
        seed: Semilla maestra de la que cuelgan todos los generadores, via
            `app.core.determinism.rng_for`.
        config_hash: Huella de la configuracion efectiva.
        code_version: SHA del commit, con `+dirty` si habia cambios locales.
        started_at_ns: Instante de arranque, inyectado por `ClockPort`.
        dataset_hash: Huella de los datos. Vacia en corridas que no leen series
            -un `qp doctor`, un arranque de plataforma-, y por eso no es
            obligatoria aqui aunque si lo sea en `RunFingerprint`.
        strategy_id: Composicion evaluada, si la corrida evalua una.
    """

    run_id: RunId
    seed: int
    config_hash: str
    code_version: str
    started_at_ns: TimestampNs
    dataset_hash: str = ""
    strategy_id: str | None = None

    @classmethod
    def create(
        cls,
        *,
        root: Path,
        config_hash: str,
        seed: int,
        at_ns: TimestampNs,
        dataset_hash: str = "",
        strategy_id: str | None = None,
    ) -> RunContext:
        """Construye el contexto interrogando a git por la version del codigo."""
        code = code_version(root)
        return cls(
            run_id=derive_run_id(config_hash=config_hash, code=code, seed=seed, at_ns=at_ns),
            seed=seed,
            config_hash=config_hash,
            code_version=code,
            started_at_ns=at_ns,
            dataset_hash=dataset_hash,
            strategy_id=strategy_id,
        )

    def fingerprint(self) -> RunFingerprint:
        """Huella compuesta, para sellar un artefacto.

        Raises:
            ConfigError: si falta el hash del dataset. `RunFingerprint` lo exige
                porque un artefacto de investigacion sin procedencia de datos no
                es reconstruible, y esa comprobacion debe fallar al sellar y no
                al intentar reproducir la corrida meses despues.
        """
        return RunFingerprint(
            dataset=self.dataset_hash,
            config=self.config_hash,
            code=self.code_version,
            seed=self.seed,
            strategy=self.strategy_id,
        )

    def with_dataset(self, dataset_hash: str) -> RunContext:
        """Contexto nuevo con la procedencia de datos ya conocida.

        El hash del dataset se conoce despues de cargar las series, cuando el
        contexto ya existe. Se devuelve un objeto nuevo en lugar de mutar para
        que el contexto siga siendo inmutable: un identificador de corrida que
        cambia a mitad de la corrida no identifica nada.
        """
        return RunContext(
            run_id=self.run_id,
            seed=self.seed,
            config_hash=self.config_hash,
            code_version=self.code_version,
            started_at_ns=self.started_at_ns,
            dataset_hash=dataset_hash,
            strategy_id=self.strategy_id,
        )

    def to_dict(self) -> Mapping[str, Any]:
        return {
            "run_id": str(self.run_id),
            "seed": self.seed,
            "config_hash": self.config_hash,
            "code_version": self.code_version,
            "started_at_ns": int(self.started_at_ns),
            "dataset_hash": self.dataset_hash,
            "strategy_id": self.strategy_id,
        }

    def __str__(self) -> str:
        return f"run:{self.run_id} code:{self.code_version} seed:{self.seed}"


__all__ = [
    "DIRTY_SUFFIX",
    "UNKNOWN_CODE_VERSION",
    "RunContext",
    "code_version",
    "derive_run_id",
]
