"""Nivel 5: adaptadores con I/O.

Unico lugar del sistema autorizado a tocar disco, red, reloj del sistema
operativo o broker. Cada modulo de esta capa implementa un `Protocol` declarado
en `app/shared/ports.py`; ninguno inventa su propia interfaz.

Es tambien la unica capa donde se permite `pandas` (`data_backend = "pandas"`).
Los `DataFrame` entran, se normalizan y salen convertidos en objetos de dominio.
Un `DataFrame` que cruce hacia `app/domain` o hacia un motor es un fallo de
arquitectura: arrastra un indice con semantica implicita y es mutable, y ambas
cosas producen desalineaciones temporales silenciosas.

Subpaquetes:

* `brokers/` adaptadores MT5, Interactive Brokers y simulado
* `storage/` Parquet, DuckDB y artefactos JSON; carga y normalizacion de series
* `logging/` logger estructurado y telemetria
"""
