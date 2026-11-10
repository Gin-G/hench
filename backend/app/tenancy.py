"""Whose data a piece of work may see.

Every table but ``users`` carries a ``user_id`` (models.Owned). Rather than
trusting each query to remember a ``WHERE user_id = ...``, the session adds
it to every ORM select, update and delete it runs, from the scope set here:

* a user's id — a request, once Access has said who is asking, or a sync
  working through one user's Items;
* ``SYSTEM`` — the few places that must look across users, and only at ids:
  finding whose Item a webhook is for, listing Items for the nightly sync,
  looking a user up by email;
* nothing — which fails every query, so work that forgot to say whose data
  it is cannot quietly read everyone's.

Rows written in a user's scope are stamped with that user's id by default.
"""
from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from sqlalchemy import event
from sqlalchemy.orm import ORMExecuteState, Session, with_loader_criteria


class _System:
    def __repr__(self) -> str:
        return "SYSTEM"


SYSTEM = _System()

_scope: ContextVar[int | _System | None] = ContextVar("hench_scope", default=None)


class NoScope(RuntimeError):
    """A query ran without saying whose data it is for."""


def set_user(user_id: int) -> None:
    """Scope the rest of this task (a request) to one user."""
    _scope.set(user_id)


@contextmanager
def as_user(user_id: int) -> Iterator[None]:
    token = _scope.set(user_id)
    try:
        yield
    finally:
        _scope.reset(token)


@contextmanager
def as_system() -> Iterator[None]:
    token = _scope.set(SYSTEM)
    try:
        yield
    finally:
        _scope.reset(token)


def current_user_id() -> int:
    """The user in scope; raises outside one (including in SYSTEM scope)."""
    scope = _scope.get()
    if not isinstance(scope, int):
        raise NoScope(f"needs a user in scope, not {scope!r}")
    return scope


def install(owned: type) -> None:
    """Filter every ORM statement on ``owned`` subclasses to the scope's user."""

    @event.listens_for(Session, "do_orm_execute")
    def _filter(state: ORMExecuteState) -> None:
        scope = _scope.get()
        if scope is SYSTEM:
            return
        if scope is None:
            raise NoScope("query ran with no user in scope")
        if state.is_column_load or state.is_relationship_load:
            # Already filtered by the statement that started the load.
            return
        if state.is_select or state.is_update or state.is_delete:
            user_id = scope
            state.statement = state.statement.options(
                with_loader_criteria(
                    owned,
                    lambda cls: cls.user_id == user_id,
                    include_aliases=True,
                )
            )
