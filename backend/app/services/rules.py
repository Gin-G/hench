"""Apply merchant-pattern rules to derive a transaction's category.

Used at sync time to set ``category_primary``/``category_detailed``. Falls
back to Plaid's PFC values when no rule matches.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models import Rule


@dataclass
class CompiledRule:
    pattern: str
    match_type: str
    category_primary: str
    category_detailed: str | None
    priority: int
    _regex: re.Pattern | None = None

    def matches(self, text: str) -> bool:
        if self.match_type == "regex":
            if self._regex is None:
                self._regex = re.compile(self.pattern, re.IGNORECASE)
            return self._regex.search(text) is not None
        return self.pattern.lower() in text.lower()


async def load_rules(session: AsyncSession) -> list[CompiledRule]:
    rows = (
        await session.scalars(
            select(Rule).where(Rule.enabled.is_(True)).order_by(Rule.priority)
        )
    ).all()
    return [
        CompiledRule(
            pattern=r.pattern,
            match_type=r.match_type,
            category_primary=r.category_primary,
            category_detailed=r.category_detailed,
            priority=r.priority,
        )
        for r in rows
    ]


def categorize(
    rules: list[CompiledRule],
    *,
    merchant_name: str | None,
    name: str | None,
    pfc_primary: str | None,
    pfc_detailed: str | None,
) -> tuple[str | None, str | None]:
    """Return (category_primary, category_detailed) after applying rules.

    Rules are assumed pre-sorted by priority; the first match wins.
    """
    haystack = " ".join(filter(None, [merchant_name, name]))
    if haystack:
        for rule in rules:
            if rule.matches(haystack):
                return rule.category_primary, rule.category_detailed
    return pfc_primary, pfc_detailed
