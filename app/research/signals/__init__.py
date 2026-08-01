"""Features a decisiones por barra, con codigo de razon.

Tres roles, que son los tres grupos que `StrategySpec` ya declara:

    entry     genera direccion. Es el unico que propone operar.
    context   no genera direccion: veta. Direccion cero y el veredicto en
              `reason`, para que el embudo pueda contar cuantos setups validos
              elimino cada filtro y por que motivo.
    exit      dice que cerrar. `direction` es la direccion que se CIERRA.

Importar este paquete puebla `SIGNALS`. Los modulos de rol se importan aqui por
su efecto de registro: sin esta importacion el catalogo quedaria vacio y
discovery no encontraria bloques que componer, sin que ningun error lo delatara.
"""

from __future__ import annotations

from app.research.signals import contexts, entries, exits
from app.research.signals.contexts import allows, atr_filter, gate, rsi_filter
from app.research.signals.entries import ema_cross, rsi_reversion
from app.research.signals.exits import ema_exit, rsi_exit
from app.research.signals.registry import (
    CONTEXT,
    ENTRY,
    EXIT,
    ROLES,
    SIGNALS,
    Block,
    feature_requests_for,
    register_block,
)

__all__ = [
    "CONTEXT",
    "ENTRY",
    "EXIT",
    "ROLES",
    "SIGNALS",
    "Block",
    "allows",
    "atr_filter",
    "contexts",
    "ema_cross",
    "ema_exit",
    "entries",
    "exits",
    "feature_requests_for",
    "gate",
    "register_block",
    "rsi_exit",
    "rsi_filter",
    "rsi_reversion",
]
