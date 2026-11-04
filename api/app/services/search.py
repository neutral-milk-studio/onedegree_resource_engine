"""Search over services and policy-document chunks.

`MemoryIndex` (default): in-process BM25 with plain-language query expansion ("no money for
diapers" -> baby supplies / basic needs). Cheap, needs no extra infrastructure, rebuilds itself when
the store changes. `OpenSearchIndex`: same interface for production scale (keyword + optional kNN).
Both are derived data: dropped and rebuilt from the HSDS store at any time (R5.3)."""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from api.app.hsds.nest import Views
from shared.db import loads

TOKEN = re.compile(r"[a-z0-9]+")
STOP = set(
    "a an the and or of to for in on at is are be i me my we you your with need needs want help can how do does what where who it this that from have has get some any no not".split()
)

# Everyday wording -> words that appear in listings. Extend freely; the LLM can also expand queries.
EXPANSIONS = {
    "diaper": [
        "baby",
        "infant",
        "family",
        "formula",
        "household",
        "supplies",
        "basic needs",
        "child",
    ],
    "formula": ["baby", "infant", "wic", "nutrition", "food"],
    "rent": ["rental", "housing", "eviction", "tenant", "deposit", "homeless", "prevention"],
    "evict": ["eviction", "tenant", "housing", "legal", "rental", "prevention"],
    "homeless": ["shelter", "housing", "temporary", "emergency", "outreach"],
    "shelter": ["homeless", "housing", "emergency", "temporary"],
    "food": ["pantry", "meals", "groceries", "hunger", "nutrition", "calfresh"],
    "hungry": ["food", "pantry", "meals", "groceries"],
    "groceries": ["food", "pantry"],
    "job": ["employment", "career", "training", "workforce"],
    "work": ["employment", "career", "training"],
    "doctor": ["health", "clinic", "medical", "care"],
    "medical": ["health", "clinic", "care"],
    "mental": ["counseling", "behavioral", "health", "therapy", "crisis"],
    "legal": ["attorney", "law", "lawyer", "tenant", "rights"],
    "school": ["education", "tutoring", "student", "youth"],
    "utility": ["electric", "gas", "water", "bill", "energy", "assistance"],
    "clothes": ["clothing", "household", "basic needs"],
    "transport": ["transportation", "bus", "ride", "paratransit"],
    "senior": ["elderly", "older", "aging", "adults"],
    "pregnant": ["maternity", "prenatal", "family", "wic", "baby"],
}


def stem(t: str) -> str:
    if len(t) > 4 and t.endswith("ies"):
        return t[:-3] + "y"
    if len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
        return t[:-1]
    return t


def tokenize(text: str) -> list[str]:
    return [stem(t) for t in TOKEN.findall((text or "").lower()) if t not in STOP]


def expand(query: str) -> tuple[list[str], list[str]]:
    base = tokenize(query)
    extra: list[str] = []
    for t in base:
        extra += [stem(w) for ph in EXPANSIONS.get(t, []) for w in ph.split()]
    return base, [e for e in dict.fromkeys(extra) if e not in base]


def haversine_km(a, b) -> float:
    (la1, lo1), (la2, lo2) = a, b
    p = math.pi / 180
    x = (
        math.sin((la2 - la1) * p / 2) ** 2
        + math.cos(la1 * p) * math.cos(la2 * p) * math.sin((lo2 - lo1) * p / 2) ** 2
    )
    return 12742 * math.asin(math.sqrt(x))


@dataclass
class Doc:
    id: str
    fields: dict[str, str]  # field -> text
    meta: dict = field(default_factory=dict)  # filterable/display data


WEIGHTS = {
    "name": 3.0,
    "terms": 2.5,
    "org": 2.0,
    "description": 1.0,
    "eligibility": 1.0,
    "other": 0.7,
    "text": 1.0,
}


class MemoryIndex:
    name = "memory"

    def __init__(self):
        self.docs: dict[str, Doc] = {}
        self.tf: dict[str, Counter] = {}
        self.df: Counter = Counter()
        self.avg_len = 1.0
        self.version: tuple | None = None
        self.zip_centroids: dict[str, tuple[float, float]] = {}

    def rebuild(self, docs: list[Doc], version: tuple | None = None) -> None:
        self.docs = {d.id: d for d in docs}
        self.tf, self.df = {}, Counter()
        lens = []
        for d in docs:
            c: Counter = Counter()
            for fld, text in d.fields.items():
                w = WEIGHTS.get(fld, 1.0)
                for t in tokenize(text):
                    c[t] += w
            self.tf[d.id] = c
            lens.append(sum(c.values()))
            for t in c:
                self.df[t] += 1
        self.avg_len = (sum(lens) / len(lens)) if lens else 1.0
        self.version = version
        z: dict[str, list] = defaultdict(list)
        for d in docs:
            for pt in d.meta.get("points", []):
                if pt.get("zip") and pt.get("lat") is not None:
                    z[pt["zip"]].append((pt["lat"], pt["lng"]))
        self.zip_centroids = {
            k: (sum(a for a, _ in v) / len(v), sum(b for _, b in v) / len(v)) for k, v in z.items()
        }

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        filters: dict | None = None,
        near: tuple | None = None,
        radius_km: float | None = None,
    ) -> list[dict]:
        base, extra = expand(query)
        if not base and not extra:
            return []
        n, k1, b = max(len(self.docs), 1), 1.4, 0.75
        out = []
        for did, c in self.tf.items():
            d = self.docs[did]
            if filters and any(d.meta.get(k) != v for k, v in filters.items() if v is not None):
                continue
            score, hit = 0.0, []
            dl = sum(c.values()) or 1
            for terms, qw in ((base, 1.0), (extra, 0.45)):
                for t in terms:
                    f = c.get(t, 0)
                    if not f:
                        continue
                    idf = math.log(1 + (n - self.df[t] + 0.5) / (self.df[t] + 0.5))
                    score += qw * idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * dl / self.avg_len))
                    hit.append(t)
            if score <= 0:
                continue
            dist = None
            if near and d.meta.get("points"):
                ds = [
                    haversine_km(near, (p["lat"], p["lng"]))
                    for p in d.meta["points"]
                    if p.get("lat") is not None
                ]
                dist = min(ds) if ds else None
                if radius_km and (dist is None or dist > radius_km):
                    continue
                if dist is not None:
                    score *= 1 + 0.25 / (1 + dist / 5)  # gentle proximity boost
            out.append(
                {
                    "id": did,
                    "score": round(score, 4),
                    "matched": sorted(set(hit)),
                    "distance_km": dist,
                    "meta": d.meta,
                }
            )
        out.sort(key=lambda r: -r["score"])
        return out[:limit]


class OpenSearchIndex:  # pragma: no cover - needs a cluster
    """Production search. Same interface; documents carry the same fields. kNN/semantic search can be
    added by storing a Bedrock Titan embedding per document in a knn_vector field."""

    name = "opensearch"

    def __init__(self, endpoint: str, region: str, index: str):
        import boto3
        from opensearchpy import AWSV4SignerAuth, OpenSearch, RequestsHttpConnection

        auth = AWSV4SignerAuth(boto3.Session().get_credentials(), region, "es")
        self.client = OpenSearch(
            hosts=[endpoint], http_auth=auth, use_ssl=True, connection_class=RequestsHttpConnection
        )
        self.index, self.version, self.zip_centroids = index, None, {}

    def rebuild(self, docs: list[Doc], version=None) -> None:
        from opensearchpy import helpers

        if self.client.indices.exists(self.index):
            self.client.indices.delete(self.index)
        self.client.indices.create(self.index)
        helpers.bulk(
            self.client,
            (
                {"_index": self.index, "_id": d.id, "_source": {**d.fields, "meta": d.meta}}
                for d in docs
            ),
        )
        self.client.indices.refresh(self.index)
        self.version = version

    def search(self, query, *, limit=10, filters=None, near=None, radius_km=None):
        base, extra = expand(query)
        body = {
            "size": limit,
            "query": {
                "multi_match": {
                    "query": " ".join(base + extra),
                    "fields": [
                        "name^3",
                        "terms^2.5",
                        "org^2",
                        "description",
                        "eligibility",
                        "other",
                        "text",
                    ],
                    "fuzziness": "AUTO",
                }
            },
        }
        res = self.client.search(index=self.index, body=body)
        return [
            {
                "id": h["_id"],
                "score": h["_score"],
                "matched": [],
                "distance_km": None,
                "meta": h["_source"].get("meta", {}),
            }
            for h in res["hits"]["hits"]
        ]


def build_service_docs(store) -> list[Doc]:
    rows = store.all_of_type("service")
    views = Views(store)
    views.prefetch(rows)
    docs = []
    for r in rows:
        d = r["data"]
        org = (views.orgs.get(r["org_id"]) or {}).get("data", {})
        terms, term_ids = [], []
        for a in d.get("attributes", []):
            if a.get("taxonomy_term_id"):
                terms.append(" ".join(views.term_path(a["taxonomy_term_id"])))
                term_ids.append(a["taxonomy_term_id"])
        points, cities, zips = [], [], []
        for s in views.sals.get(r["id"], []):
            loc = (views.locs.get(s["data"].get("location_id", "")) or {}).get("data", {})
            for ad in loc.get("addresses", []):
                cities.append(ad.get("city", ""))
                zips.append(ad.get("postal_code", ""))
            points.append(
                {
                    "lat": loc.get("latitude"),
                    "lng": loc.get("longitude"),
                    "zip": (loc.get("addresses") or [{}])[0].get("postal_code"),
                }
            )
        other = " ".join(
            [
                " ".join(sa.get("name", "") for sa in d.get("service_areas", [])),
                " ".join(l.get("name", "") for l in d.get("languages", [])),
                " ".join(cities),
                " ".join(zips),
                " ".join(x.get("document", "") for x in d.get("required_documents", [])),
            ]
        )
        docs.append(
            Doc(
                r["id"],
                {
                    "name": d.get("name", ""),
                    "description": d.get("description", ""),
                    "org": org.get("name", ""),
                    "terms": " ".join(terms),
                    "eligibility": d.get("eligibility_description", ""),
                    "other": other,
                },
                {
                    "status": d.get("status"),
                    "org_id": r["org_id"],
                    "source": r["source"],
                    "name": d.get("name"),
                    "points": points,
                    "term_ids": term_ids,
                    "assured_date": d.get("assured_date"),
                },
            )
        )
    return docs


def build_chunk_docs(db) -> list[Doc]:
    rows = db.q(
        "SELECT c.id, c.text, c.ord, d.id AS document_id, d.title, d.org_id, d.service_id, d.effective_date, "
        "d.visibility FROM doc_chunk c JOIN document d ON d.id=c.document_id "
        "WHERE d.status='approved' AND d.visibility='public'"
    )
    return [
        Doc(
            r["id"],
            {"text": r["text"], "name": r["title"]},
            {
                "document_id": r["document_id"],
                "title": r["title"],
                "org_id": r["org_id"],
                "service_id": r["service_id"],
                "effective_date": r["effective_date"],
                "ord": r["ord"],
            },
        )
        for r in rows
    ]


class SearchService:
    """Keeps both indexes fresh. Cheap version stamps let several app processes stay consistent."""

    def __init__(self, settings, db, store):
        self.db, self.store, self.settings = db, store, settings
        mk = lambda n: (
            OpenSearchIndex(settings.opensearch_endpoint, settings.aws_region, n)
            if settings.search_backend == "opensearch"
            else MemoryIndex()
        )
        self.services, self.chunks = mk("services"), mk("policy-chunks")

    def _svc_version(self):
        r = self.db.one(
            "SELECT COUNT(*) AS n, MAX(updated_at) AS m FROM resource WHERE deleted=0 AND type IN "
            "('service','organization','location','service_at_location','taxonomy_term')"
        )
        return (r["n"], r["m"])

    def _doc_version(self):
        r = self.db.one(
            "SELECT COUNT(*) AS n, MAX(created_at) AS m, SUM(chunk_count) AS c FROM document WHERE status='approved'"
        )
        return (r["n"], r["m"], r["c"])

    def search_services(self, query, **kw):
        v = self._svc_version()
        if self.services.version != v:
            self.services.rebuild(build_service_docs(self.store), v)
        return self.services.search(query, **kw)

    def search_chunks(self, query, **kw):
        v = self._doc_version()
        if self.chunks.version != v:
            self.chunks.rebuild(build_chunk_docs(self.db), v)
        return self.chunks.search(query, **kw)

    def reindex(self) -> dict:
        self.services.version = self.chunks.version = None
        self.search_services("x")
        self.search_chunks("x")
        return {
            "services": len(getattr(self.services, "docs", {})),
            "chunks": len(getattr(self.chunks, "docs", {})),
        }

    def zip_centroid(self, zip_code: str):
        self.search_services("x")
        return self.services.zip_centroids.get(str(zip_code))
