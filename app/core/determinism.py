"""Identidad de contenido y control de aleatoriedad.

Sin este modulo no hay reproducibilidad, y `reproducibility = true` deja de ser
una afirmacion verificable. Dos garantias:

1. **Hash estable de contenido.** Dos objetos semanticamente iguales producen
   el mismo hash en cualquier maquina, cualquier version de Python y cualquier
   orden de insercion de claves. `hash()` de Python NO sirve: esta aleatorizado
   por proceso (PYTHONHASHSEED) y no es estable entre ejecuciones.

2. **Semillas derivadas jerarquicamente.** Nunca se usa un generador global.
   Cada componente deriva su semilla de (semilla_maestra, namespace, partes),
   de modo que anadir un fold o reordenar la busqueda no altera las secuencias
   aleatorias de los demas componentes.

Nota sobre `serialization = "orjson"`: orjson es el backend de serializacion
para **artefactos en disco**, donde importa la velocidad. Para **hashing** se
usa deliberadamente el `json` de la biblioteca estandar. Motivo: orjson emite
UTF-8 crudo y stdlib emite ASCII escapado; si el hash dependiera de cual esta
instalado, el mismo experimento produciria identificadores distintos en dos
maquinas. La identidad de contenido no puede depender de una dependencia
opcional.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any, Final

import numpy as np

from app.core.types import ContentHash

#: Longitud del hash en caracteres hexadecimales. 16 hex = 64 bits: suficiente
#: para nombres de artefacto (colision improbable con < 10^9 objetos) y corto
#: para leerse en una ruta de fichero.
HASH_LENGTH: Final[int] = 16

#: Cota superior de una semilla de 32 bits, exigida por numpy.
_SEED_MODULUS: Final[int] = 2**32


def canonical_json(value: Any) -> str:
    """Serializa a JSON de forma canonica y determinista.

    Reglas aplicadas:
      * claves ordenadas alfabeticamente;
      * sin espacios superfluos;
      * ASCII escapado, para que el resultado no dependa de la codificacion;
      * tipos de numpy degradados a tipos nativos;
      * tuplas y sets normalizados a listas (los sets se ordenan).

    Raises:
        TypeError: si aparece un tipo no serializable. Es deliberado: un objeto
            opaco dentro de una configuracion rompe la reproducibilidad y debe
            detectarse en el momento, no silenciarse con `default=str`.
    """
    return json.dumps(
        _normalize(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    )


def _normalize(value: Any) -> Any:
    """Reduce cualquier estructura a primitivas JSON de forma determinista."""
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            # NaN e infinitos no tienen representacion JSON estable.
            raise TypeError(f"Valor flotante no canonizable: {value!r}")
        return value
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, np.floating):
        return _normalize(float(value))
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, np.ndarray):
        # Los arrays se resumen por su contenido binario: incluir 200.000
        # numeros en el JSON haria el hash inmanejable.
        return {"__ndarray__": array_digest(value), "shape": list(value.shape)}
    if isinstance(value, Mapping):
        return {str(k): _normalize(v) for k, v in sorted(value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (set, frozenset)):
        return sorted((_normalize(v) for v in value), key=repr)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [_normalize(v) for v in value]
    # Objetos de dominio que exponen su propia forma canonica.
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return _normalize(to_dict())
    raise TypeError(
        f"Tipo no canonizable: {type(value).__name__}. "
        "Implementa to_dict() o conviertelo antes de hashearlo."
    )


def stable_hash(value: Any, *, length: int = HASH_LENGTH) -> ContentHash:
    """Hash de contenido reproducible entre procesos y maquinas.

    Se usa blake2b por velocidad y por permitir digest de tamano configurable
    sin truncar manualmente.
    """
    payload = canonical_json(value).encode("utf-8")
    digest = hashlib.blake2b(payload, digest_size=max(1, length // 2)).hexdigest()
    return ContentHash(digest[:length])


def array_digest(array: np.ndarray, *, length: int = HASH_LENGTH) -> str:
    """Huella del contenido binario exacto de un array.

    Incluye dtype y forma: dos arrays con los mismos numeros pero distinto
    dtype no son intercambiables para un motor de backtest, y su huella debe
    diferir.
    """
    contiguous = np.ascontiguousarray(array)
    hasher = hashlib.blake2b(digest_size=max(1, length // 2))
    hasher.update(str(contiguous.dtype).encode("ascii"))
    hasher.update(str(contiguous.shape).encode("ascii"))
    hasher.update(contiguous.tobytes())
    return hasher.hexdigest()[:length]


def derive_seed(master_seed: int, namespace: str, *parts: Any) -> int:
    """Deriva una semilla hija determinista y estable.

    Propiedades garantizadas:
      * misma entrada -> misma semilla, siempre;
      * namespaces distintos -> semillas no correlacionadas;
      * anadir un nuevo consumidor no altera las semillas de los existentes.

    Args:
        master_seed: Semilla raiz de la corrida, fijada en configuracion.
        namespace: Etiqueta del consumidor, p.ej. "discovery.mutation".
        parts: Coordenadas adicionales, p.ej. numero de fold o id de estrategia.
    """
    material = canonical_json([master_seed, namespace, list(parts)]).encode("utf-8")
    digest = hashlib.blake2b(material, digest_size=8).digest()
    return int.from_bytes(digest, "big") % _SEED_MODULUS


def rng_for(master_seed: int, namespace: str, *parts: Any) -> np.random.Generator:
    """Generador aislado para un consumidor concreto.

    Se devuelve `np.random.Generator` (PCG64) y nunca `np.random.seed`, que
    modifica estado global y hace que el resultado dependa del orden de
    ejecucion entre modulos.
    """
    return np.random.default_rng(derive_seed(master_seed, namespace, *parts))


__all__ = [
    "HASH_LENGTH",
    "array_digest",
    "canonical_json",
    "derive_seed",
    "rng_for",
    "stable_hash",
]
