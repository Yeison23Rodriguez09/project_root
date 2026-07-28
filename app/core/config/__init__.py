"""Configuracion: la mitad pura. Esquema, precedencia y procedencia.

Opera sobre diccionarios ya cargados en memoria. No abre ficheros, no lee
variables de entorno y no conoce formatos. Cualquier capa puede importarlo.

La mitad que si lee vive en `app/config/`, nivel infraestructura, y ningun motor
puede importarla: un motor capaz de leer un fichero de configuracion
introduciria una entrada no declarada, y el resultado dejaria de depender solo
de (datos, configuracion, semilla). Ver ADR-0005.

Modulos:

* `provenance.py`  `Priority`, `Origin`, `ResolvedValue`, `ResolutionTrace`
* `resolver.py`    `ConfigLayer` y el motor de precedencia

Regla nemotecnica: si lee, esta en `config`; si decide, esta en `core.config`.
"""
