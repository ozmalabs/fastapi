"""
Gamma (Γ) — admissibility grammar extraction for FastAPI.

Automatically extracts from existing code:
- Preconditions from Depends() chains
- Security preconditions from security schemes
- Pydantic validator constraints (via JSON Schema, already in spec)

Provides decorators for explicit Γ annotation where automatic
extraction is insufficient (state machines, postconditions,
ordering constraints, forbidden sequences).

The extracted Γ is emitted as x-gamma on each OpenAPI operation.
"""
from __future__ import annotations

import inspect
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from fastapi.dependencies.models import Dependant


# ---------------------------------------------------------------------------
# Γ data model
# ---------------------------------------------------------------------------

@dataclass
class Precondition:
    type: str           # "dependency" | "security" | "explicit"
    name: str
    description: str | None = None
    scopes: list[str] = field(default_factory=list)


@dataclass
class Postcondition:
    description: str
    effect: str | None = None   # "resource_exists" | "resource_ceases" | "state_change"
    produces_state: str | None = None


@dataclass
class Transition:
    from_state: str
    to_state: str


@dataclass
class GammaSpec:
    """Complete admissibility grammar for one operation."""
    preconditions: list[Precondition] = field(default_factory=list)
    postconditions: list[Postcondition] = field(default_factory=list)
    # State machine — populated from @state_machine or ORM introspection
    states: list[str] | None = None
    transitions: list[Transition] | None = None
    requires_state: list[str] | None = None      # op only valid in these states
    produces_state: str | None = None            # op transitions to this state
    # Cross-operation ordering
    requires_prior: list[str] | None = None      # operationIds that must precede this
    forbidden_after: list[str] | None = None     # operationIds that cannot follow this

    def is_empty(self) -> bool:
        return (
            not self.preconditions
            and not self.postconditions
            and self.states is None
            and self.requires_state is None
            and self.requires_prior is None
            and self.forbidden_after is None
        )

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {}
        if self.preconditions:
            d["preconditions"] = [
                {k: v for k, v in vars(p).items() if v is not None and v != []}
                for p in self.preconditions
            ]
        if self.postconditions:
            d["postconditions"] = [
                {k: v for k, v in vars(p).items() if v is not None}
                for p in self.postconditions
            ]
        if self.states is not None:
            d["states"] = self.states
        if self.transitions is not None:
            d["transitions"] = [
                {"from": t.from_state, "to": t.to_state} for t in self.transitions
            ]
        if self.requires_state is not None:
            d["requires_state"] = self.requires_state
        if self.produces_state is not None:
            d["produces_state"] = self.produces_state
        if self.requires_prior is not None:
            d["requires_prior"] = self.requires_prior
        if self.forbidden_after is not None:
            d["forbidden_after"] = self.forbidden_after
        return d


# ---------------------------------------------------------------------------
# Automatic extraction from Dependant trees
# ---------------------------------------------------------------------------

def _dep_name(dep: Dependant) -> str | None:
    if dep.call is None:
        return None
    call = dep.call
    # unwrap partials
    while hasattr(call, "func"):
        call = call.func
    return getattr(call, "__name__", None) or getattr(
        type(call), "__name__", None
    )


def _dep_description(dep: Dependant) -> str | None:
    if dep.call is None:
        return None
    call = dep.call
    while hasattr(call, "func"):
        call = call.func
    doc = inspect.getdoc(call)
    if doc:
        return doc.split("\n")[0]
    return None


def extract_preconditions(dependant: Dependant) -> list[Precondition]:
    """
    Walk the Dependant tree and extract typed preconditions.

    Security dependencies become type="security" preconditions with scopes.
    All other Depends() become type="dependency" preconditions.
    The root route function itself is skipped.
    """
    preconditions: list[Precondition] = []
    seen: set[Any] = set()

    def _walk(dep: Dependant, is_root: bool = False) -> None:
        key = dep.cache_key
        if key in seen:
            return
        seen.add(key)

        if not is_root:
            name = _dep_name(dep)
            if name:
                if dep._is_security_scheme:
                    preconditions.append(Precondition(
                        type="security",
                        name=name,
                        description=_dep_description(dep),
                        scopes=list(dep.oauth_scopes or []),
                    ))
                else:
                    preconditions.append(Precondition(
                        type="dependency",
                        name=name,
                        description=_dep_description(dep),
                    ))

        for sub in dep.dependencies:
            _walk(sub)

    _walk(dependant, is_root=True)
    return preconditions


# ---------------------------------------------------------------------------
# ORM introspection (SQLAlchemy — optional)
# ---------------------------------------------------------------------------

def _try_extract_sqlalchemy_states(
    model_cls: Any,
) -> tuple[list[str], list[Transition]] | None:
    """
    Attempt to extract state enumeration and transitions from a SQLAlchemy
    model. Looks for columns whose type is Enum or whose name suggests status.
    Returns (states, transitions) or None if SQLAlchemy is not available or
    no state column is found.
    """
    try:
        from sqlalchemy import inspect as sa_inspect
        from sqlalchemy import Enum as SAEnum
    except ImportError:
        return None

    try:
        mapper = sa_inspect(model_cls)
    except Exception:
        return None

    for col_attr in mapper.mapper.column_attrs:
        col = col_attr.columns[0]
        if isinstance(col.type, SAEnum) and col.type.enums:
            states = list(col.type.enums)
            # Transitions: if states look like a lifecycle, infer linear progression.
            # Explicit transitions require @transition decorator.
            return states, []
        if col.key in ("status", "state", "phase") and hasattr(col.type, "enums"):
            states = list(col.type.enums)
            return states, []

    return None


# ---------------------------------------------------------------------------
# Decorator API for explicit Γ annotation
# ---------------------------------------------------------------------------

_GAMMA_ATTR = "_ozma_gamma"


def _get_or_create_gamma(func: Callable[..., Any]) -> GammaSpec:
    if not hasattr(func, _GAMMA_ATTR):
        setattr(func, _GAMMA_ATTR, GammaSpec())
    return getattr(func, _GAMMA_ATTR)


def precondition(description: str, *, effect: str | None = None) -> Callable[..., Any]:
    """
    Explicitly annotate a precondition on a route.

        @app.get("/items/{id}")
        @gamma.precondition("Item must exist")
        async def get_item(id: int): ...
    """
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        spec = _get_or_create_gamma(func)
        spec.preconditions.append(Precondition(
            type="explicit",
            name=description,
            description=description,
        ))
        return func
    return decorator


def postcondition(
    description: str,
    *,
    effect: str | None = None,
    produces_state: str | None = None,
) -> Callable[..., Any]:
    """
    Explicitly annotate a postcondition on a route.

        @app.delete("/items/{id}")
        @gamma.postcondition("Item no longer exists", effect="resource_ceases")
        async def delete_item(id: int): ...
    """
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        spec = _get_or_create_gamma(func)
        spec.postconditions.append(Postcondition(
            description=description,
            effect=effect,
            produces_state=produces_state,
        ))
        if produces_state:
            spec.produces_state = produces_state
        return func
    return decorator


def requires_state(*states: str) -> Callable[..., Any]:
    """
    Declare that this operation is only admissible when the resource
    is in one of the given states.

        @app.post("/items/{id}/publish")
        @gamma.requires_state("draft")
        async def publish_item(id: int): ...
    """
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        spec = _get_or_create_gamma(func)
        spec.requires_state = list(states)
        return func
    return decorator


def produces_state(state: str) -> Callable[..., Any]:
    """
    Declare the state this operation transitions the resource into.

        @app.post("/items/{id}/publish")
        @gamma.produces_state("published")
        async def publish_item(id: int): ...
    """
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        spec = _get_or_create_gamma(func)
        spec.produces_state = state
        return func
    return decorator


def requires_prior(*operation_ids: str) -> Callable[..., Any]:
    """
    Declare that this operation may only be called after the given
    operationIds have been called in the same session.

        @app.post("/checkout")
        @gamma.requires_prior("addToCart")
        async def checkout(): ...
    """
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        spec = _get_or_create_gamma(func)
        spec.requires_prior = list(operation_ids)
        return func
    return decorator


def forbidden_after(*operation_ids: str) -> Callable[..., Any]:
    """
    Declare that after this operation, the given operationIds are
    no longer admissible.

        @app.post("/items/{id}/archive")
        @gamma.forbidden_after("updateItem", "publishItem")
        async def archive_item(id: int): ...
    """
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        spec = _get_or_create_gamma(func)
        spec.forbidden_after = list(operation_ids)
        return func
    return decorator


# ---------------------------------------------------------------------------
# App-level state machine registration
# ---------------------------------------------------------------------------

@dataclass
class StateMachine:
    name: str
    states: list[str]
    transitions: list[Transition]
    model_cls: Any = None   # SQLAlchemy model, if any


_STATE_MACHINES: dict[str, StateMachine] = {}


def state_machine(
    name: str,
    states: list[str],
    transitions: list[tuple[str, str]],
    *,
    model: Any = None,
) -> None:
    """
    Register a named state machine at app level.

    Transitions are (from_state, to_state) pairs.

        gamma.state_machine(
            "ItemLifecycle",
            states=["draft", "published", "archived"],
            transitions=[
                ("draft", "published"),
                ("published", "archived"),
            ],
        )
    """
    _STATE_MACHINES[name] = StateMachine(
        name=name,
        states=states,
        transitions=[Transition(f, t) for f, t in transitions],
        model_cls=model,
    )


def get_state_machine(name: str) -> StateMachine | None:
    return _STATE_MACHINES.get(name)


# ---------------------------------------------------------------------------
# Core extraction: build GammaSpec for a route
# ---------------------------------------------------------------------------

def extract_gamma(route: Any) -> GammaSpec:
    """
    Build a GammaSpec for a FastAPI APIRoute by combining:
    1. Automatically extracted dependency preconditions
    2. Explicit decorator annotations on the endpoint function
    3. ORM-derived state machine (if available)
    """
    spec = GammaSpec()

    # 1. Automatic: dependency preconditions from Dependant tree
    if hasattr(route, "dependant"):
        spec.preconditions = extract_preconditions(route.dependant)

    # 2. Explicit: decorator annotations on the endpoint function
    endpoint = getattr(route, "endpoint", None)
    if endpoint is not None:
        explicit: GammaSpec | None = getattr(endpoint, _GAMMA_ATTR, None)
        if explicit is not None:
            # Merge explicit preconditions (append, don't replace auto-extracted)
            spec.preconditions.extend(
                p for p in explicit.preconditions if p.type == "explicit"
            )
            spec.postconditions = explicit.postconditions
            spec.states = explicit.states
            spec.transitions = explicit.transitions
            spec.requires_state = explicit.requires_state
            spec.produces_state = explicit.produces_state
            spec.requires_prior = explicit.requires_prior
            spec.forbidden_after = explicit.forbidden_after

    return spec
