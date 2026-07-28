"""Fallos de composicion y de ciclo de vida.

Fichero propio y no una seccion de `container.py`. El motivo no es de tamano: es
que una jerarquia de excepciones es una lista por naturaleza y crece con cada
frontera nueva del contenedor, mientras que `container.py` describe un mecanismo
que no deberia crecer. Mezclarlas hacia que el modulo pareciera engordar cuando
lo unico que pasaba era que se nombraba un fallo mas.

Todos descienden de `ContainerError`, y esa de `PlatformError`, de modo que un
punto de entrada puede capturar "algo fallo al componer" sin enumerar los casos,
y el diagnostico puede distinguirlos cuando le importa.
"""

from __future__ import annotations

from app.core.exceptions import PlatformError


class ContainerError(PlatformError):
    code = "CONTAINER_ERROR"


class UnregisteredPort(ContainerError):
    """Se pidio un puerto que nadie registro.

    Es el fallo mas comun al anadir un componente: se declara la dependencia y se
    olvida el adaptador. El contexto lista lo registrado para que el diagnostico
    no exija abrir el codigo de arranque.
    """

    code = "UNREGISTERED_PORT"


class DuplicateRegistration(ContainerError):
    """Dos adaptadores reclaman el mismo puerto.

    Fatal, y no "gana el ultimo": cual ganase dependeria del orden de
    importacion, y el sistema construido dejaria de ser reproducible.
    """

    code = "DUPLICATE_REGISTRATION"


class ResolutionCycle(ContainerError):
    code = "RESOLUTION_CYCLE"


class LifecycleError(ContainerError):
    code = "LIFECYCLE_ERROR"


class IllegalPhaseTransition(ContainerError):
    """Se intento una operacion que la fase actual no permite.

    Registrar despues de sellar, arrancar dos veces, resolver antes de sellar.
    Todas son el mismo tipo de error -orden de composicion- y todas deben tener
    el mismo nombre para que se reconozcan como tal.
    """

    code = "ILLEGAL_PHASE_TRANSITION"


class ScopeNotImplemented(ContainerError):
    """El scope esta declarado en el contrato pero aun no tiene implementacion.

    Se declara antes de implementarse a proposito: la arquitectura queda
    preparada y el fallo es explicito, en lugar de que alguien descubra a mitad
    de camino que el vocabulario no da para lo que necesita.
    """

    code = "SCOPE_NOT_IMPLEMENTED"


__all__ = [
    "ContainerError",
    "DuplicateRegistration",
    "IllegalPhaseTransition",
    "LifecycleError",
    "ResolutionCycle",
    "ScopeNotImplemented",
    "UnregisteredPort",
]
