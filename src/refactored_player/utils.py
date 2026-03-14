from __future__ import annotations


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
