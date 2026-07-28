# Directorio de plugins externos

Ruta declarada en `configs/plugins.toml` → `[discovery].external_directories`.
Lo inspecciona `app.container.plugins.discover_manifests` y lo importa
`app.core.registry.loader.Loader.load_plugin_directory`.

**Hoy esta vacio, y ese es el estado correcto.** Un plugin declara su `kind`
entre `feature`, `signal`, `strategy`, `statistical_test` y `broker`; los cinco
pertenecen a la Fase 4 o posteriores. Enviar aqui un plugin antes de que exista
el motor que lo consume seria codigo sin consumidor, que es exactamente lo que
ADR-0006 obliga a recortar.

Que el directorio este vacio **no** significa que el camino de carga externo no
se ejercite: `tests/fixtures/plugins/` contiene cuatro paquetes de plugin reales
-con su `plugin.toml`- que `tests/test_plugins.py` descubre, valida y carga con
el `Loader` de verdad. Cubren los cuatro finales posibles de una carga:
conforme, con campos ausentes, con API incompatible y con manifiesto ilegible.
Lo que se prueba es el mecanismo, y se prueba con plugins autenticos.

## Que debe cumplir un plugin

Un plugin es un **directorio con `__init__.py`**, nunca un `.py` suelto. La
exigencia deja sitio desde el primer dia para su manifiesto, sus pruebas y sus
datos de referencia.

```
plugins/
    indicator_kama/
        __init__.py
        plugin.toml
        kama.py
        tests/
```

`plugin.toml` declara ocho campos obligatorios. Sin uno solo de ellos el loader
rechaza el paquete entero, sin cargarlo a medias:

| Campo | Para que |
|---|---|
| `name` | identificador estable; se persiste en los `StrategySpec` |
| `version` | semver del propio plugin |
| `api_version` | version del contrato que dice cumplir |
| `kind` | `feature`, `signal`, `strategy`, `statistical_test` o `broker` |
| `capability` | capacidad del sistema a la que pertenece |
| `owner` | quien responde de este codigo |
| `description` | una linea |
| `provides` | nombres que registra, para detectar colisiones sin importar nada |

`provides` es el que evita el peor estado posible: sin el, una colision de
nombres aparece a mitad de la carga, con parte del catalogo poblado y parte no.

## Aislamiento

Un plugin ve exactamente lo mismo que un motor de calculo:

```
permitido:  app.core   app.domain   app.shared
prohibido:  app.broker  app.storage  app.monitoring  app.application
            app.live    app.paper    app.interfaces
```

Sin red, sin sistema de ficheros y sin subprocesos. Un indicador de terceros que
pudiera consultar precios en vivo durante un backtest lo volveria irreproducible
sin que nada lo delatara.

## Prioridad de nombres

`load_order = "internal_first"`. Los bloques del nucleo reclaman su nombre antes
que los externos, de modo que un plugin no puede secuestrar `ema` y cambiar en
silencio el significado de todas las estrategias del zoo que lo referencian.

## Comprobarlo

```bash
qp plugins list
qp plugins validate
```
