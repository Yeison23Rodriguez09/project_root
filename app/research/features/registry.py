"""Catalogo de features. Un solo sitio donde estan todas, con sus metadatos.

El catalogo existe para que discovery pueda explorar el espacio sin conocer
ninguna feature concreta: pide las de familia `trend`, resuelve sus parametros
declarados y compone. Una feature que no este aqui no existe para el sistema,
por muy implementada que este.

`FEATURES` es estado de modulo, igual que cualquier catalogo: se puebla una vez
al importar y despues es de solo lectura. La alternativa -pasar el registro por
argumento hasta cada decorador- haria tan incomodo dar de alta un indicador que
nadie declararia sus parametros, que es justo lo que da valor al catalogo.
"""

from __future__ import annotations

from collections.abc import Callable

from app.core.registry.registry import Registry
from app.core.types import FloatArray

#: Firma almacenable de una feature.
#
# Se escribe `Callable[..., FloatArray]` y no `shared.ports.FeatureFn` por una
# razon de tipos que conviene dejar dicha: `FeatureFn` declara `**params: Any`
# porque describe el CONSUMO -quien invoca lo hace con un diccionario resuelto-,
# y una funcion concreta como `sma(bars, *, period)` es mas estrecha que eso.
# mypy lo rechaza con razon: nadie podria llamar `sma(bars, foo=1)`.
#
# Las dos alternativas son peores. Declarar `**params: Any` en cada feature haria
# que una errata como `periodd=20` se ignorase en silencio y el indicador
# corriera con su valor por defecto, que es el error mas caro de detectar en una
# plataforma cuantitativa. Silenciar mypy en cada registro convertiria una
# decision de diseno en seis anotaciones repetidas sin explicacion.
#
# Lo que queda sin cubrir por el tipo -longitud, causalidad, calentamiento- no lo
# cubriria `FeatureFn` tampoco: ningun sistema de tipos expresa "no mira al
# futuro". Eso lo verifica `tests/test_no_lookahead.py` sobre todas las
# registradas, y los nombres de parametro los valida `ComponentEntry.resolve`
# contra los `ParamSpec` declarados.
StorableFeature = Callable[..., FloatArray]

#: Catalogo global de features. Se puebla al importar `app.research.features`.
FEATURES: Registry[StorableFeature] = Registry("feature")

__all__ = ["FEATURES", "StorableFeature"]
