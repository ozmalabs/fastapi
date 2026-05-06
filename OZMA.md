# ozmalabs/fastapi — FastAPI + Γ (admissibility grammar)

A drop-in fork of FastAPI that adds **admissibility grammar** to your API.

## Install

```bash
pip install git+https://github.com/ozmalabs/fastapi.git
```

That's it. All your existing imports work unchanged. You immediately get:

- `x-gamma` in your `/openapi.json` showing preconditions extracted from your existing `Depends()` chains and security schemes — **no code changes required**
- `GammaError` — a structured error type you can return instead of raising `HTTPException`

---

## What you get for free

The fork walks your existing dependency graph and emits it into the OpenAPI spec. If you already use `Depends()` for auth, database sessions, or guards — the grammar is already there. It just wasn't visible before.

```json
{
  "paths": {
    "/items/{id}": {
      "post": {
        "x-gamma": {
          "preconditions": [
            { "type": "security", "name": "OAuth2PasswordBearer", "scopes": ["items:write"] },
            { "type": "dependency", "name": "get_current_user", "description": "Authenticated user" }
          ]
        }
      }
    }
  }
}
```

This is extracted automatically from your `Depends()` tree. **You wrote it already. Now it's visible.**

---

## Return errors instead of raising them

```python
# Before
@app.get("/items/{id}")
async def get_item(id: int):
    item = db.get(id)
    if not item:
        raise HTTPException(status_code=404, detail="Not found")
    return item

# After — same behaviour, but the error explains WHY
from fastapi.gamma import GammaError

@app.get("/items/{id}")
async def get_item(id: int) -> Item | GammaError:
    item = db.get(id)
    if not item:
        return GammaError.not_found("item", id)
    return item
```

FastAPI intercepts `GammaError` return values and serialises them at the right status code. The response body carries `violation`, `description`, and the graph state — not just `{"detail": "Not found"}`.

```json
{
  "violation": "resource_not_found",
  "description": "item with id 42 does not exist",
  "resource": "item"
}
```

### Named constructors

```python
GammaError.not_found("item", id)
GammaError.wrong_state(operation="publishItem", resource="item", current="archived", required=["draft"])
GammaError.requires_prior(operation="checkout", missing=["addToCart"])
GammaError.forbidden_after(operation="archiveItem", blocked_by="archiveItem")
GammaError.permission_denied(operation="deleteItem", reason="Only owners can delete")
```

---

## Add explicit grammar with decorators

```python
from fastapi import gamma

gamma.state_machine(
    "ItemLifecycle",
    states=["draft", "published", "archived"],
    transitions=[("draft", "published"), ("published", "archived")],
)

@app.post("/items/{id}/publish")
@gamma.requires_state("draft")
@gamma.produces_state("published")
async def publish_item(id: int) -> Item | GammaError:
    ...

@app.post("/items/{id}/archive")
@gamma.requires_state("published")
@gamma.produces_state("archived")
@gamma.forbidden_after("archiveItem")
async def archive_item(id: int) -> Item | GammaError:
    ...

@app.post("/checkout")
@gamma.requires_prior("addToCart")
async def checkout() -> dict | GammaError:
    ...
```

All of this appears in `x-gamma` on the relevant operations. Clients that parse `x-gamma` can enforce the grammar before making calls.

---

## Generate a complete spec from any FastAPI app

```bash
ozma-spec mypackage.web:app                        # stdout JSON
ozma-spec --format yaml --output spec.yaml pkg:app
ozma-spec --install somepackage somepackage.api:app # pip install first
```

`ozma-spec` imports the app, calls `app.openapi()`, and outputs the complete spec with `x-gamma` included.

---

## Add grammar to your models (optional)

Install [pydantic-gamma](https://github.com/ozmalabs/pydantic-gamma) to get `GammaModel` — the same `GammaError` type is shared across both packages:

```bash
pip install git+https://github.com/ozmalabs/pydantic-gamma.git
```

```python
from pydantic_gamma import GammaModel, gamma_field

class Item(GammaModel):
    __gamma_states__ = ["draft", "published", "archived"]
    __gamma_transitions__ = [("draft", "published"), ("published", "archived")]
    __gamma_state_field__ = "status"

    id: int
    title: str
    status: str = "draft"
    published_at: datetime | None = gamma_field(default=None, writable_in=["published", "archived"])

item = Item(id=1, title="hello")
result = item.update(status="archived")   # GammaError — draft→archived not a valid transition
result = item.update(published_at=now())  # GammaError — field not writable in draft
```

The `GammaError` returned from `item.update()` is the same type FastAPI intercepts in your routes — return it directly from your endpoint and it serialises correctly.

---

## What this solves

OpenAPI specifies **what** operations exist and **what shape** their inputs and outputs are. It does not specify **when** an operation is admissible — which state a resource must be in, which operations must precede it, which operations it forecloses.

That grammar currently lives in prose documentation, or nowhere. This fork makes it explicit, machine-readable, and enforceable at the client.

See [ozmalabs/openapi-ozma-clients](https://github.com/ozmalabs/openapi-ozma-clients) for a Γ-aware client that parses `x-gamma` and enforces it before making calls.
