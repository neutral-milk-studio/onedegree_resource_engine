"""Turn a plain-language request into search terms. Small, readable, and easy to extend.

"I just lost my job and can't pay rent" -> the person's own words (job, rent, pay, lost)
plus the service words those needs map to (employment, unemployment, financial assistance,
rental, housing, utilities). The person's own words rank higher than the added ones.
Semantic search (Bedrock embeddings) can replace or complement this later.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

STOPWORDS = {
    "i", "im", "i'm", "me", "my", "we", "our", "you", "a", "an", "the", "and", "or", "but",
    "to", "of", "in", "on", "for", "with", "at", "by", "from", "is", "am", "are", "was", "be",
    "just", "can", "cant", "can't", "dont", "don't", "not", "no", "so", "it", "this", "that",
    "need", "needs", "help", "get", "find", "looking", "want", "some", "any", "please", "how",
    "where", "what", "who", "near", "there", "have", "has", "had", "do", "does", "will",
}  # fmt: skip

# Needs, matched in the lower-cased request -> terms that services use for them.
INTENTS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "Rent and housing costs": (
        ("rent", "evict", "landlord", "mortgage", "behind on", "move-in", "deposit", "lease"),
        ("rent", "rental", "housing", "eviction", "utilities", "financial assistance"),
    ),
    "Job loss and income": (
        ("lost my job", "laid off", "fired", "unemploy", "no job", "job", "work", "income",
         "paycheck"),
        ("employment", "job", "unemployment", "financial assistance", "cash assistance",
         "benefits"),
    ),
    "Food": (
        ("food", "hungry", "grocer", "eat", "meal", "pantry", "lunch", "dinner"),
        ("food", "pantry", "groceries", "meal", "produce"),
    ),
    "Shelter": (
        ("homeless", "shelter", "nowhere to", "sleep", "living in my car", "unhoused"),
        ("shelter", "homeless", "homelessness", "housing", "coordinated entry"),
    ),
    "Bills and utilities": (
        ("bill", "utilit", "electric", "gas bill", "water bill", "pg&e", "shut off"),
        ("utilities", "utility", "financial assistance"),
    ),
    "Legal help": (
        ("legal", "lawyer", "attorney", "court", "immigration", "tenant rights"),
        ("legal", "law", "clinic", "attorney"),
    ),
    "Health": (
        ("doctor", "health", "medical", "clinic", "sick", "medicine"),
        ("health", "medical", "clinic"),
    ),
    "Older adults": (
        ("senior", "elderly", "older adult", "retired"),
        ("senior", "seniors", "older adults"),
    ),
}  # fmt: skip


@dataclass
class QueryPlan:
    text: str
    own_terms: list[str] = field(default_factory=list)
    added_terms: list[str] = field(default_factory=list)
    needs: list[str] = field(default_factory=list)

    @property
    def all_terms(self) -> list[str]:
        return self.own_terms + [t for t in self.added_terms if t not in self.own_terms]


def plan(text: str) -> QueryPlan:
    lowered = (text or "").lower()
    words = re.findall(r"[a-z0-9][a-z0-9'&-]*", lowered)
    own = []
    for word in words:
        word = word.strip("'-")
        if len(word) > 1 and word not in STOPWORDS and word not in own:
            own.append(word)
    result = QueryPlan(text=text, own_terms=own)
    for need, (triggers, terms) in INTENTS.items():
        if any(trigger in lowered for trigger in triggers):
            result.needs.append(need)
            for term in terms:
                if term not in result.added_terms and term not in own:
                    result.added_terms.append(term)
    return result
