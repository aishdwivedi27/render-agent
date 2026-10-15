"""API routes, one module per screen area."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from render_agent.app.main import AppContext


def context(request: Request) -> AppContext:
    ctx: AppContext = request.app.state.ctx
    return ctx


Ctx = Annotated[AppContext, Depends(context)]
