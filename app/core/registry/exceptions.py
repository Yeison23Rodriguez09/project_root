"""Fallos propios del registro de componentes.

Viven aqui y no en `app/core/exceptions.py` porque son especificos de este
subsistema. La jerarquia global solo debe contener lo que atraviesa varias
capas; si cada mecanismo vuelca sus errores en el fichero raiz, ese fichero
acaba siendo un indice de todo el proyecto y deja de poder leerse.

Todos heredan de `PlatformError`, asi que capturar la raiz sigue atrapandolos.
"""

from __future__ import annotations

from app.core.exceptions import PlatformError


class RegistryError(PlatformError):
    """Raiz de los fallos del catalogo de componentes."""

    code = "REGISTRY_ERROR"


class UnknownComponent(RegistryError):
    """Se pidio un componente que nadie registro.

    Casi siempre es una errata en un fichero de configuracion o un modulo de
    bloques que no llego a importarse. El contexto incluye la lista de nombres
    disponibles para que el diagnostico no requiera abrir el codigo.
    """

    code = "UNKNOWN_COMPONENT"


class DuplicateComponent(RegistryError):
    """Dos componentes reclaman el mismo nombre estable.

    Es fatal en tiempo de importacion. Un nombre ambiguo invalida cualquier
    artefacto que lo referencie, porque deja de estar claro que codigo se
    ejecuto: la estrategia guardada en el zoo apuntaria a dos implementaciones
    distintas segun el orden de importacion.
    """

    code = "DUPLICATE_COMPONENT"


class InvalidParameter(RegistryError):
    """Un parametro es desconocido o cae fuera de sus cotas declaradas.

    Se distingue de `UnknownComponent` porque el componente si existe: lo que
    falla es como se le pidio trabajar. La distincion importa en discovery,
    donde un parametro fuera de cotas indica un espacio de busqueda mal
    definido y no un bloque ausente.
    """

    code = "INVALID_PARAMETER"


__all__ = [
    "DuplicateComponent",
    "InvalidParameter",
    "RegistryError",
    "UnknownComponent",
]
