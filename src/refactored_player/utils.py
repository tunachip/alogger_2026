from __future__ import annotations

import re
from dataclasses import dataclass

SEARCH_FIELD_OPTIONS = [
    "$title",
    "$creator",
    "$genre",
    "$summary",
    "$length",
    "$ts",
    "$*",
]


@dataclass(slots=True)
class SearchClause:
    field: str
    negated: bool
    expression: str


def is_search_field_token(token: str) -> bool:
    raw = str(token or "").strip()
    if not raw:
        return False
    if not raw.startswith("$"):
        return False
    base = raw[1:]
    if base.endswith("!"):
        base = base[:-1]
    if base == "*":
        return True
    return bool(re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", base))


def format_hms(seconds: float) -> str:
    total = max(0, int(seconds))
    hours = total // 3600
    minutes = (total % 3600) // 60
    remainder = total % 60
    return f"{hours:02d}:{minutes:02d}:{remainder:02d}"


def parse_search_query(query: str) -> list[list[str]]:
    raw = str(query or "").strip().lower()
    if not raw:
        return []
    clauses: list[list[str]] = []
    for clause in raw.split("|"):
        terms = [term.strip() for term in clause.split("&")]
        clean_terms = [term for term in terms if term]
        if clean_terms:
            clauses.append(clean_terms)
    return clauses


def search_terms(query: str) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for clause in parse_search_query(query):
        for term in clause:
            if term in seen:
                continue
            seen.add(term)
            ordered.append(term)
    return ordered


def matches_search_query(text: str, query: str) -> bool:
    haystack = str(text or "").lower()
    clauses = parse_search_query(query)
    if not clauses:
        return True
    return any(all(term in haystack for term in clause) for clause in clauses)


def parse_advanced_search_query(query: str) -> list[SearchClause]:
    raw = str(query or "").strip()
    if not raw:
        return []
    clauses: list[SearchClause] = []
    for piece in raw.split(";"):
        token = piece.strip()
        if not token:
            continue
        parts = token.split(None, 1)
        head = parts[0].strip()
        tail = parts[1].strip() if len(parts) > 1 else ""
        base = head[1:]
        if base.endswith("!"):
            base = base[:-1]
        negated = head.endswith("!")
        if is_search_field_token(head):
            clauses.append(
                SearchClause(
                    field=("ANY" if base == "*" else base.upper()),
                    negated=negated,
                    expression=tail or "*",
                )
            )
            continue
        clauses.append(
            SearchClause(
                field="TS",
                negated=False,
                expression=token,
            )
        )
    return clauses
