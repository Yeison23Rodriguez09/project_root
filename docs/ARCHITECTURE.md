<!-- ARCHIVO GENERADO. NO EDITAR. -->
<!-- Fuente: CONSTITUTION.md + configs/*.toml + decisions/*.toml -->
<!-- Regenerar: python scripts/generate_docs.py -->

# Arquitectura

**Este fichero está pendiente de generación.**

La versión escrita a mano se retiró: describía un árbol anterior y era una
segunda fuente de verdad sobre la arquitectura, lo que viola P5 y P7 de la
Constitución.

Para materializarlo:

```
python scripts/generate_docs.py
```

Hasta entonces, `tests/test_governance.py::test_derived_docs_are_in_sync` falla
a propósito. Ese fallo es el principio funcionando: un artefacto derivado que no
coincide con sus contratos rompe el build en lugar de quedarse mintiendo en
silencio.

La arquitectura vigente está en:

- [`CONSTITUTION.md`](../CONSTITUTION.md) — principios irrenunciables
- [`configs/architecture.toml`](../configs/architecture.toml) — paquetes, capas, capacidades, dependencias y comportamiento
- [`configs/conventions.toml`](../configs/conventions.toml) — calidad y umbrales
- [`configs/runtime.toml`](../configs/runtime.toml) — modos de ejecución
- [`configs/plugins.toml`](../configs/plugins.toml) — entrada de código externo
- [`decisions/`](../decisions) — decisiones registradas
