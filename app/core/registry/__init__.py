"""Catalogo de componentes con espacio de parametros declarado.

Bisagra entre la fase 4 y la fase 6. Un componente no se registra unicamente
con su funcion, sino con la descripcion de su espacio de parametros, su
calentamiento y su familia. Gracias a eso `app.discovery` enumera y muta el
espacio de busqueda sin conocer ningun bloque concreto y sin que nadie
mantenga a mano una lista paralela de combinaciones. Anadir un indicador
consiste en escribir la funcion y decorarla; entra en la siguiente corrida por
si solo.

Por que vive en `app.core` y no en `app.discovery`: el registro lo **escriben**
`app.research.features` y `app.research.signals` al darse de alta, y lo **lee**
discovery. Si viviera en discovery, el productor dependeria del consumidor. En
core los tres apuntan hacia dentro.

Modulos:

* `exceptions.py`  fallos propios del subsistema
* `params.py`      `ParamSpec`: el dominio admisible de cada parametro
* `protocols.py`   contratos estructurales (`WarmupFn`, `Describable`)
* `metadata.py`    `ComponentEntry`: la unidad que guarda el catalogo
* `registry.py`    `Registry`: almacenamiento y consulta
* `decorators.py`  alta declarativa y calculadores de calentamiento
* `loader.py`      descubrimiento e importacion de componentes y plugins

Catalogo y descubrimiento estan separados a proposito. `Registry` guarda;
`Loader` encuentra e importa. Ninguno conoce al otro: el unico vinculo es que
el decorador se ejecuta durante la importacion. Gracias a eso se puede anadir
un directorio `plugins/` externo sin tocar el catalogo, y construir un catalogo
desde una fuente que no sea Python sin arrastrar la maquinaria de importacion.

Este `__init__` es el unico punto del proyecto donde se reexporta. Se justifica
porque define la superficie publica del subsistema: los consumidores importan
de `app.core.registry` y quedan aislados de una reorganizacion interna.
"""

from __future__ import annotations

from app.core.registry.decorators import register, warmup_from, warmup_sum
from app.core.registry.exceptions import (
    DuplicateComponent,
    InvalidParameter,
    RegistryError,
    UnknownComponent,
)
from app.core.registry.loader import (
    Loader,
    LoadReport,
    PluginLoadError,
    load_default_components,
)
from app.core.registry.metadata import ComponentEntry
from app.core.registry.params import ParamSpec
from app.core.registry.protocols import Describable, WarmupFn
from app.core.registry.registry import Registry

__all__ = [
    "ComponentEntry",
    "Describable",
    "DuplicateComponent",
    "InvalidParameter",
    "LoadReport",
    "Loader",
    "ParamSpec",
    "PluginLoadError",
    "Registry",
    "RegistryError",
    "UnknownComponent",
    "WarmupFn",
    "load_default_components",
    "register",
    "warmup_from",
    "warmup_sum",
]
