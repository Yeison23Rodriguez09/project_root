"""Caso de uso de descarga de historicos.

Compone la cadena completa sin conocer ninguna implementacion concreta:

    MarketDataPort -> Bars -> MarketDataWriterPort -> DatasetRepositoryPort

Es el UNICO componente autorizado a coordinar lectura, validacion y persistencia
(ADR-0011). Los adaptadores no se conocen entre si y ninguno sabe de donde viene
ni adonde va la serie: eso lo decide aqui.
"""

from __future__ import annotations
