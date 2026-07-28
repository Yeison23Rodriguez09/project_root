"""Proveedores de configuracion. Unico codigo del sistema que lee del exterior.

Nivel infraestructura (ADR-0005). Ningun motor puede importar este paquete: un
motor que pudiera leer un fichero introduciria una entrada no declarada y el
resultado dejaria de depender solo de (datos, configuracion, semilla).

Cada proveedor devuelve una `ConfigLayer` con su procedencia. La decision de
que valor gana no se toma aqui: eso es `app.core.config.resolver`, que es puro.

Un fichero por mecanismo de lectura, porque cada uno falla de forma distinta y
mezclarlos escondia esa diferencia:

* `base.py`  el Protocol `ConfigProvider` y `coerce`
* `toml.py`  formato oficial; `tomllib` esta en la biblioteca estandar
* `yaml.py`  opcional; puede no estar disponible en el entorno
* `env.py`   variables `QP_*`, con su traduccion a claves con punto
* `cli.py`   `--set clave=valor` e inyeccion de mapas en memoria

Este `__init__` NO reexporta. Los consumidores importan del modulo concreto
(`from app.config.providers.toml import TomlFileProvider`), de modo que la
dependencia real queda escrita en el import y no oculta tras una fachada. El
unico punto del proyecto donde se reexporta es `app.core.registry`, y alli se
justifica porque define la superficie publica de un subsistema.
"""
