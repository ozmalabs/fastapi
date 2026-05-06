"""
ORM introspection for Γ extraction.

Walks SQLAlchemy models to extract:
- State enumerations from Enum columns
- Typed relationships as graph edges
- Check constraints as admissibility rules
- Column-level validators

Attaches extracted Γ to the app-level state machine registry
and to per-route GammaSpec objects where routes can be matched
to models via response type annotations.
"""
from __future__ import annotations

import inspect
from typing import Any

from fastapi.openapi.gamma import GammaSpec, StateMachine, Transition, _STATE_MACHINES


def _import_sqlalchemy() -> Any:
    try:
        import sqlalchemy as sa
        return sa
    except ImportError:
        return None


def _get_mapper(model_cls: Any, sa: Any) -> Any:
    try:
        return sa.inspect(model_cls)
    except Exception:
        return None


def _extract_enum_states(col: Any, sa: Any) -> list[str] | None:
    if isinstance(col.type, sa.Enum) and col.type.enums:
        return list(col.type.enums)
    # String column with name suggesting state
    if col.key in ("status", "state", "phase", "lifecycle", "stage"):
        if hasattr(col.type, "enums") and col.type.enums:
            return list(col.type.enums)
    return None


def _extract_check_constraints(table: Any) -> list[str]:
    """Extract human-readable check constraints as admissibility rules."""
    rules = []
    for constraint in table.constraints:
        cls_name = type(constraint).__name__
        if cls_name == "CheckConstraint":
            text = str(getattr(constraint, "sqltext", "") or "")
            if text:
                rules.append(text)
    return rules


def introspect_model(model_cls: Any) -> dict[str, Any] | None:
    """
    Introspect a SQLAlchemy model and return a dict describing its Γ.

    Returns None if SQLAlchemy is not installed or model is not mappable.
    """
    sa = _import_sqlalchemy()
    if sa is None:
        return None

    mapper = _get_mapper(model_cls, sa)
    if mapper is None:
        return None

    result: dict[str, Any] = {
        "model": model_cls.__name__,
        "states": None,
        "state_column": None,
        "transitions": [],
        "constraints": [],
        "relationships": [],
    }

    # Column scan: find state enumerations
    for col_attr in mapper.mapper.column_attrs:
        col = col_attr.columns[0]
        states = _extract_enum_states(col, sa)
        if states and result["states"] is None:
            result["states"] = states
            result["state_column"] = col.key

    # Check constraints from table
    try:
        result["constraints"] = _extract_check_constraints(mapper.mapper.local_table)
    except Exception:
        pass

    # Relationships as typed edges
    try:
        for rel in mapper.mapper.relationships:
            result["relationships"].append({
                "name": rel.key,
                "target": rel.mapper.class_.__name__,
                "direction": rel.direction.name,  # MANYTOONE | ONETOMANY | MANYTOMANY
                "uselist": rel.uselist,
            })
    except Exception:
        pass

    return result


def register_model_state_machines(models: list[Any]) -> None:
    """
    Introspect a list of SQLAlchemy model classes and register any
    discovered state machines into the Γ registry.

    Call this after your models are defined:

        from fastapi.openapi.gamma_orm import register_model_state_machines
        from myapp.models import Item, Order, User

        register_model_state_machines([Item, Order, User])
    """
    for model_cls in models:
        info = introspect_model(model_cls)
        if info and info["states"]:
            name = f"{info['model']}Lifecycle"
            if name not in _STATE_MACHINES:
                _STATE_MACHINES[name] = StateMachine(
                    name=name,
                    states=info["states"],
                    transitions=[],   # explicit transitions require @transition decorator
                    model_cls=model_cls,
                )


def enrich_gamma_from_response_type(spec: GammaSpec, endpoint: Any) -> None:
    """
    Attempt to enrich a GammaSpec by inspecting the return type annotation
    of the endpoint function for a matching SQLAlchemy model.

    If the return type is a Pydantic model whose __name__ matches a registered
    state machine's model, attach the state machine info to the spec.
    """
    sa = _import_sqlalchemy()
    if sa is None:
        return

    hints = {}
    try:
        hints = inspect.get_annotations(endpoint, eval_str=True)
    except Exception:
        try:
            hints = getattr(endpoint, "__annotations__", {})
        except Exception:
            return

    return_type = hints.get("return")
    if return_type is None:
        return

    type_name = getattr(return_type, "__name__", None)
    if type_name is None:
        return

    # Look for a registered state machine whose name starts with type_name
    for sm_name, sm in _STATE_MACHINES.items():
        if sm_name.startswith(type_name):
            if spec.states is None:
                spec.states = sm.states
            if spec.transitions is None and sm.transitions:
                spec.transitions = sm.transitions
            break
