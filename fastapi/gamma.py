"""
Ozma Γ (admissibility grammar) — public API.

Provides decorators for explicit Γ annotation on FastAPI routes,
and registration of app-level state machines.

Γ is also extracted automatically from Depends() chains and
security schemes — no decorators needed for those.

Usage::

    from fastapi import FastAPI
    from fastapi import gamma

    app = FastAPI()

    gamma.state_machine(
        "ItemLifecycle",
        states=["draft", "published", "archived"],
        transitions=[
            ("draft", "published"),
            ("published", "archived"),
        ],
    )

    @app.delete("/items/{id}")
    @gamma.requires_state("published", "draft")
    @gamma.produces_state("archived")
    @gamma.postcondition("Item is archived", effect="state_change")
    @gamma.forbidden_after("updateItem")
    async def archive_item(id: int): ...
"""

from fastapi.openapi.gamma import (
    forbidden_after,
    get_state_machine,
    postcondition,
    precondition,
    produces_state,
    requires_prior,
    requires_state,
    state_machine,
)

__all__ = [
    "forbidden_after",
    "get_state_machine",
    "postcondition",
    "precondition",
    "produces_state",
    "requires_prior",
    "requires_state",
    "state_machine",
]
