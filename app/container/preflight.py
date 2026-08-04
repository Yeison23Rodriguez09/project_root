"""Validacion de arranque. Si una sola regla falla, el sistema no inicia.

Los tests verifican los contratos en CI. Esto los verifica **en el arranque**, que
no es lo mismo: en CI se comprueba el repositorio, y aqui se comprueba la maquina
real, con su configuracion real, sus plugins instalados y sus adaptadores
disponibles. Un despliegue puede pasar CI y arrancar sobre un entorno donde falta
un fichero de configuracion, sobra un plugin incompatible o el modo de ejecucion
autoriza paquetes que no existen.

Es la puerta de la que habla el punto 3.8: la comprobacion ocurre ANTES de que
exista un solo objeto de negocio. Fallar aqui cuesta un mensaje de error; fallar
mas adelante cuesta una corrida entera cuyo resultado parece valido.

Orden deliberado. Se comprueba de lo mas general a lo mas concreto -constitucion,
arquitectura, comportamiento, capacidades, configuracion, modo, determinismo- y se
acumulan TODOS los hallazgos antes de decidir. Parar en el primero obligaria a
arrancar siete veces para descubrir siete problemas.
"""

from __future__ import annotations

import platform
import sys
import tomllib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.exceptions import NotValidated
from app.core.types import Severity
from app.core.validation import ValidationReport

#: Estados de ciclo de vida que puede admitir un modo capaz de enviar ordenes.
#:
#: `promoted` es la estrategia que supero la validacion y espera aprobacion;
#: `live` la que ya opera. Cualquier otro -`candidate`, `validated`, `rejected`,
#: `retired`- significa que algo sin certificar podria llegar al broker, que es
#: la definicion del fallo que este proyecto existe para evitar.
PROMOTED_STATES: frozenset[str] = frozenset({"promoted", "live"})

#: Ficheros de gobernanza que deben existir para que el sistema pueda arrancar.
#: Su ausencia no es un aviso: sin ellos no hay nada que gobierne el arranque.
REQUIRED_CONTRACTS: tuple[str, ...] = (
    "CONSTITUTION.md",
    "configs/architecture.toml",
    "configs/conventions.toml",
    "configs/runtime.toml",
    "configs/plugins.toml",
)


@dataclass(frozen=True, slots=True)
class PreflightResult:
    """Veredicto del arranque, con todo lo comprobado.

    Attributes:
        report: Inventario completo de hallazgos.
        mode: Modo de ejecucion solicitado.
        checks_run: Nombre de cada comprobacion ejecutada, en orden. Va en el
            resultado a proposito: un arranque que paso porque una comprobacion
            no se ejecuto es indistinguible de uno que paso de verdad, salvo que
            se registre la lista.
    """

    report: ValidationReport
    mode: str
    checks_run: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return self.report.ok

    def raise_if_failed(self) -> None:
        """Aborta el arranque si algo fallo.

        Raises:
            NotValidated: es un `MethodologyError`, no un error tecnico. El
                sistema funciona; lo que no se cumple es el proceso.
        """
        if not self.ok:
            self.report.raise_if_failed(NotValidated)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "mode": self.mode,
            "checks_run": list(self.checks_run),
            "report": self.report.to_dict(),
        }

    def summary(self) -> str:
        state = "OK" if self.ok else "FALLO"
        return (
            f"preflight[{self.mode}] {state}: "
            f"{len(self.checks_run)} comprobaciones, {len(self.report)} hallazgos"
        )


class Preflight:
    """Ejecuta las comprobaciones de arranque sobre un despliegue concreto.

    Recibe la raiz del proyecto y el modo. No construye nada del sistema: si
    tuviera que instanciar adaptadores para validarlos, un fallo de validacion
    dejaria objetos a medio construir.
    """

    def __init__(self, root: Path, *, mode: str) -> None:
        self._root = root
        self._mode = mode
        self._report = ValidationReport(subject=f"arranque en modo {mode!r}")
        self._checks: list[str] = []
        self._architecture: dict[str, Any] = {}
        self._runtime: dict[str, Any] = {}

    # -- ejecucion -----------------------------------------------------------

    def run(
        self,
        *,
        effective_config: Mapping[str, Any] | None = None,
        installed_plugins: Sequence[Mapping[str, Any]] = (),
    ) -> PreflightResult:
        """Ejecuta todas las comprobaciones y devuelve el veredicto."""
        self._check_contracts_present()
        # Sin contratos legibles el resto de comprobaciones no tiene base sobre
        # la que trabajar, y seguir produciria una cascada de fallos derivados
        # que oculta la causa real.
        if self._report.ok:
            self._check_architecture_is_coherent()
            self._check_mode_exists()
            self._check_mode_packages_exist()
            self._check_capabilities_are_owned()
            self._check_plugin_compatibility(installed_plugins)
            self._check_determinism_requirements()
            self._check_lifecycle_states_are_restricted()
            if effective_config is not None:
                self._check_config_is_frozen(effective_config)
        return PreflightResult(report=self._report, mode=self._mode, checks_run=tuple(self._checks))

    # -- comprobaciones ------------------------------------------------------

    def _check_contracts_present(self) -> None:
        self._checks.append("contracts_present")
        for relative in REQUIRED_CONTRACTS:
            path = self._root / relative
            if not path.is_file():
                self._report.add(
                    "PREFLIGHT_MISSING_CONTRACT",
                    f"Falta el contrato {relative}",
                    Severity.FATAL,
                    path=relative,
                )
        architecture = self._root / "configs" / "architecture.toml"
        runtime = self._root / "configs" / "runtime.toml"
        for target, attribute in ((architecture, "_architecture"), (runtime, "_runtime")):
            if not target.is_file():
                continue
            try:
                setattr(self, attribute, tomllib.loads(target.read_text(encoding="utf-8")))
            except tomllib.TOMLDecodeError as exc:
                self._report.add(
                    "PREFLIGHT_CONTRACT_UNREADABLE",
                    f"{target.name} no es TOML valido",
                    Severity.FATAL,
                    path=target.name,
                    detail=str(exc),
                )

    def _check_architecture_is_coherent(self) -> None:
        """La matriz no se contradice: destinos existentes y sin subir de capa."""
        self._checks.append("architecture_coherent")
        packages: dict[str, Any] = self._architecture.get("packages", {})
        ranks: dict[str, int] = {
            k: int(v) for k, v in self._architecture.get("layer_order", {}).items()
        }
        for name, spec in packages.items():
            layer = str(spec.get("layer", ""))
            if layer not in ranks:
                self._report.add(
                    "PREFLIGHT_UNRANKED_LAYER",
                    f"El paquete {name!r} usa la capa {layer!r}, sin rango declarado",
                    Severity.FATAL,
                    package=name,
                    layer=layer,
                )
                continue
            for dependency in spec.get("depends", ()):
                if dependency not in packages:
                    self._report.add(
                        "PREFLIGHT_DANGLING_DEPENDENCY",
                        f"{name} depende de {dependency!r}, que no existe",
                        Severity.FATAL,
                        package=name,
                        dependency=dependency,
                    )
                    continue
                target_layer = str(packages[dependency].get("layer", ""))
                if ranks.get(target_layer, 0) > ranks[layer]:
                    self._report.add(
                        "PREFLIGHT_UPWARD_DEPENDENCY",
                        f"{name} ({layer}) depende de {dependency} ({target_layer})",
                        Severity.FATAL,
                        package=name,
                        dependency=dependency,
                    )

    def _check_mode_exists(self) -> None:
        self._checks.append("mode_exists")
        modes: dict[str, Any] = self._runtime.get("modes", {})
        if self._mode not in modes:
            self._report.add(
                "PREFLIGHT_UNKNOWN_MODE",
                f"Modo de ejecucion desconocido: {self._mode!r}",
                Severity.FATAL,
                mode=self._mode,
                available=sorted(modes),
            )

    def _check_mode_packages_exist(self) -> None:
        """El modo no autoriza paquetes que no existen en la matriz."""
        self._checks.append("mode_packages_exist")
        declared = set(self._architecture.get("packages", {}))
        allowed = self._runtime.get("allowed_packages", {}).get(self._mode, ())
        for package in allowed:
            if package not in declared:
                self._report.add(
                    "PREFLIGHT_MODE_UNKNOWN_PACKAGE",
                    f"El modo {self._mode!r} autoriza {package!r}, que no esta declarado",
                    Severity.ERROR,
                    mode=self._mode,
                    package=package,
                )

    def _check_capabilities_are_owned(self) -> None:
        self._checks.append("capabilities_owned")
        capabilities: dict[str, Any] = self._architecture.get("capabilities", {})
        for name, spec in capabilities.items():
            if not spec.get("owner"):
                self._report.add(
                    "PREFLIGHT_CAPABILITY_UNOWNED",
                    f"La capacidad {name!r} no tiene responsable",
                    Severity.WARNING,
                    capability=name,
                )
        for name, spec in self._architecture.get("packages", {}).items():
            if spec.get("capability") not in capabilities:
                self._report.add(
                    "PREFLIGHT_CAPABILITY_UNKNOWN",
                    f"El paquete {name!r} declara una capacidad inexistente",
                    Severity.ERROR,
                    package=name,
                    capability=spec.get("capability"),
                )

    def _check_plugin_compatibility(self, installed: Sequence[Mapping[str, Any]]) -> None:
        """Los plugins instalados declaran metadatos y una API compatible.

        Se comprueba antes de importarlos. Un plugin que reclama un nombre ya
        tomado o una API futura debe rechazarse sin ejecutar su codigo: importar
        primero y validar despues significa que su `__init__` ya corrio.
        """
        self._checks.append("plugin_compatibility")
        contract = self._root / "configs" / "plugins.toml"
        if not contract.is_file():
            return
        spec = tomllib.loads(contract.read_text(encoding="utf-8"))
        required = set(spec.get("manifest", {}).get("required", ()))
        accepted = str(spec.get("compatibility", {}).get("min_api_version", "1.0"))

        seen: dict[str, str] = {}
        for manifest in installed:
            name = str(manifest.get("name", "<sin nombre>"))
            missing = sorted(required - set(manifest))
            if missing:
                self._report.add(
                    "PREFLIGHT_PLUGIN_INCOMPLETE",
                    f"El plugin {name!r} no declara {missing}",
                    Severity.ERROR,
                    plugin=name,
                    missing=missing,
                )
            api = str(manifest.get("api_version", ""))
            if api and api < accepted:
                self._report.add(
                    "PREFLIGHT_PLUGIN_API_TOO_OLD",
                    f"El plugin {name!r} declara api_version={api!r}, minimo {accepted}",
                    Severity.ERROR,
                    plugin=name,
                    api_version=api,
                )
            for provided in manifest.get("provides", ()):
                if provided in seen:
                    self._report.add(
                        "PREFLIGHT_PLUGIN_NAME_CLASH",
                        f"{name!r} y {seen[provided]!r} registran ambos {provided!r}",
                        Severity.FATAL,
                        component=provided,
                        plugins=[name, seen[provided]],
                    )
                else:
                    seen[str(provided)] = name

    def _check_determinism_requirements(self) -> None:
        """El modo solicitado puede cumplir lo que exige.

        Comprueba las dos garantias que ningun modo puede romper y que el
        contrato de runtime declara como invariantes de gobierno.
        """
        self._checks.append("determinism_requirements")
        effective = self._effective_mode(self._mode)
        if effective.get("allow_broker_orders"):
            for requirement in ("require_promoted_state", "require_approval_record"):
                if not effective.get(requirement):
                    self._report.add(
                        "PREFLIGHT_UNSAFE_MODE",
                        f"El modo {self._mode!r} envia ordenes sin {requirement}",
                        Severity.FATAL,
                        mode=self._mode,
                        requirement=requirement,
                    )
        self._check_seed_implies_clock(effective)

    def _check_lifecycle_states_are_restricted(self) -> None:
        """Un modo que envia ordenes no admite estados sin promover.

        La invariante `live_accepts_only_promoted_states` de `runtime.toml` la
        verificaba solo la suite. La diferencia importa: la suite corre en CI y
        esto corre en la maquina del operador, que es donde un contrato editado
        a mano llega a produccion. Se descubrio al intentar degradar la politica
        de ADR-0017 -anadir `candidate` a los estados de `demo` pasaba el
        preflight en verde-.
        """
        self._checks.append("lifecycle_states_restricted")
        effective = self._effective_mode(self._mode)
        if not effective.get("allow_broker_orders"):
            return

        states: dict[str, Any] = dict(self._runtime.get("allowed_lifecycle_states", {}))
        states.pop("rationale", None)
        if self._mode not in states:
            self._report.add(
                "PREFLIGHT_UNRESTRICTED_LIFECYCLE",
                f"El modo {self._mode!r} envia ordenes y no declara que estados admite",
                Severity.FATAL,
                mode=self._mode,
            )
            return

        forbidden = sorted(set(states[self._mode]) - PROMOTED_STATES)
        if forbidden:
            self._report.add(
                "PREFLIGHT_UNPROMOTED_STATE_ALLOWED",
                f"El modo {self._mode!r} envia ordenes y admite estados sin promover: {forbidden}",
                Severity.FATAL,
                mode=self._mode,
                forbidden=forbidden,
            )

    def _check_seed_implies_clock(self, effective: Mapping[str, Any]) -> None:
        if effective.get("requires_seed") and not effective.get("requires_clock_injection"):
            self._report.add(
                "PREFLIGHT_INCONSISTENT_DETERMINISM",
                f"El modo {self._mode!r} exige semilla pero no inyeccion de reloj; "
                "la semilla sola no basta para reproducir",
                Severity.ERROR,
                mode=self._mode,
            )

    def _check_config_is_frozen(self, effective: Mapping[str, Any]) -> None:
        """En modos operativos la configuracion no puede recargarse en caliente.

        Si se recargara, el sistema que evaluo la barra `i` no seria el mismo que
        evaluo la `i-1`, y la auditoria posterior no podria reconstruir la
        decision.
        """
        self._checks.append("config_frozen")
        mode = self._effective_mode(self._mode)
        if not mode.get("forbid_config_hot_reload"):
            return
        if effective.get("config.hot_reload"):
            self._report.add(
                "PREFLIGHT_HOT_RELOAD_FORBIDDEN",
                f"El modo {self._mode!r} prohibe recarga en caliente y esta activada",
                Severity.FATAL,
                mode=self._mode,
            )

    # -- auxiliares ----------------------------------------------------------

    def _effective_mode(self, name: str, seen: tuple[str, ...] = ()) -> dict[str, Any]:
        """Resuelve un modo aplicando su cadena de herencia."""
        modes: dict[str, Any] = self._runtime.get("modes", {})
        if name not in modes or name in seen:
            return {}
        spec = dict(modes[name])
        parent = spec.pop("inherits", None)
        if parent is None:
            return spec
        resolved = self._effective_mode(str(parent), (*seen, name))
        resolved.update(spec)
        return resolved


def environment_record() -> dict[str, Any]:
    """Entorno de ejecucion, capturado para diagnostico.

    NO forma parte de la identidad de una corrida, y la distincion es
    deliberada. Si la version de Python o el numero de version de numpy
    entraran en el `RunFingerprint`, actualizar un parche haria imposible
    comparar contra cualquier corrida anterior, incluso cuando el resultado es
    identico bit a bit. La huella dejaria de responder "es la misma corrida" y
    pasaria a responder "es la misma maquina", que es otra pregunta.

    Se registra aparte: cuando dos corridas con huella identica dan resultados
    distintos -que no deberia ocurrir nunca- esto es lo primero que se compara.
    """
    return {
        "python": sys.version.split()[0],
        "implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "machine": platform.machine(),
    }


__all__ = [
    "REQUIRED_CONTRACTS",
    "Preflight",
    "PreflightResult",
    "environment_record",
]
