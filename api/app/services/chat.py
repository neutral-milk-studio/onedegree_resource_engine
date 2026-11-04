"""Chat agent for help seekers.

Anonymous visitors can ask about One Degree and about policies. As soon as they ask for a *service*
("I need rental assistance"), the agent asks them to create a profile. Signed-in seekers get matched
providers and can send a request with one confirmation. Answers about policies cite the source document.
The agent never sends a request or shares personal data without an explicit confirmation action."""

from __future__ import annotations

import re
from pathlib import Path

from api.app.services.documents import chunk_text
from api.app.services.search import EXPANSIONS, Doc, MemoryIndex, stem, tokenize
from api.app.services.seekers import SeekerService
from shared.db import Database, dumps, loads, new_id, now_iso

CRISIS = re.compile(
    r"suicid|kill myself|end my life|hurt myself|self[- ]harm|being abused|abusing me|domestic violence|overdos|"
    r"\bemergency\b|\bunsafe\b|in danger",
    re.I,
)
NEED = re.compile(
    r"\b(i need|i['’]?m looking|im looking|looking for|help (me )?(with|find|getting|paying)|can['’]?t afford|"
    r"don['’]?t have (the )?money|no money|need help|find me|where can i get|do you have|assistance)\b",
    re.I,
)
ABOUT = re.compile(
    r"one ?degree|what do you do|who are you|how does (this|it) work|is (this|it) free|how much|who can use|"
    r"about (you|this)|what is this|how do (you|i) (get|use|start)",
    re.I,
)
POLICY = re.compile(
    r"\b(policy|policies|documents?|bring|proof|identification|id\b|eligib\w*|qualif\w*|requirements?|"
    r"income limits?|appointment|how do i apply|what do i need to)\b",
    re.I,
)

SYSTEM = (
    "You are the One Degree assistant. Help people find free and low-cost services. Answer ONLY from the provided "
    "context. If the context does not contain the answer, say you are not sure and suggest contacting the provider. "
    "Write at a seventh-grade reading level in under four short sentences. No marketing language. Be warm and direct."
)


class ConsentRequired(Exception):
    pass


class ChatEngine:
    def __init__(self, settings, db: Database, store, search, llm, seekers: SeekerService):
        self.s, self.db, self.store, self.search, self.llm, self.seekers = (
            settings,
            db,
            store,
            search,
            llm,
            seekers,
        )
        self._about = MemoryIndex()
        self._about_loaded = False

    # ---- helpers -------------------------------------------------------------------------------------
    def _about_chunks(self, query: str):
        if not self._about_loaded:
            docs = []
            for p in sorted(Path(self.s.content_dir).glob("*.md")):
                for i, c in enumerate(chunk_text(p.read_text())):
                    docs.append(
                        Doc(
                            f"{p.stem}-{i}",
                            {"text": c},
                            {"title": p.stem.replace("-", " ").title(), "text": c},
                        )
                    )
            self._about.rebuild(docs)
            self._about_loaded = True
        return self._about.search(query, limit=2)

    def _session(self, session_id: str | None, user_id: str | None) -> tuple[str, dict]:
        if session_id:
            r = self.db.one("SELECT * FROM chat_session WHERE id=?", (session_id,))
            if r:
                if user_id and not r["user_id"]:
                    self.db.run(
                        "UPDATE chat_session SET user_id=? WHERE id=?", (user_id, session_id)
                    )
                return session_id, loads(r["state"], {})
        sid = new_id()
        self.db.run(
            "INSERT INTO chat_session (id,user_id,created_at,state) VALUES (?,?,?,?)",
            (sid, user_id, now_iso(), "{}"),
        )
        return sid, {}

    def _save(self, sid: str, state: dict) -> None:
        self.db.run("UPDATE chat_session SET state=? WHERE id=?", (dumps(state), sid))

    def _log(self, sid: str, role: str, content: str, meta: dict | None = None) -> None:
        self.db.run(
            "INSERT INTO chat_message (id,session_id,role,content,meta,created_at) VALUES (?,?,?,?,?,?)",
            (new_id(), sid, role, content, dumps(meta) if meta else None, now_iso()),
        )

    def history(self, sid: str, user_id: str | None) -> list[dict]:
        s = self.db.one("SELECT user_id FROM chat_session WHERE id=?", (sid,))
        if not s or (s["user_id"] and s["user_id"] != user_id):
            return []
        return [
            {"role": m["role"], "content": m["content"], "meta": loads(m["meta"], {})}
            for m in self.db.q(
                "SELECT * FROM chat_message WHERE session_id=? ORDER BY created_at, id", (sid,)
            )
        ]

    def _card(self, hit: dict) -> dict:
        row = self.store.get(hit["id"])
        d = row["data"]
        org = (self.store.get(row["org_id"]) or {}).get("data", {}) if row["org_id"] else {}
        phone = (d.get("phones") or [None])[0]
        addr = None
        for s in self.db.q(
            "SELECT data FROM resource WHERE type='service_at_location' AND deleted=0 AND org_id=?",
            (row["org_id"],),
        ):
            sd = loads(s["data"])
            if sd.get("service_id") == hit["id"]:
                loc = (self.store.get(sd["location_id"]) or {}).get("data", {})
                a = (loc.get("addresses") or [None])[0]
                if a:
                    addr = (
                        f"{a['address_1']}, {a['city']}, {a['state_province']} {a['postal_code']}"
                    )
                    break
        desc = (d.get("description") or "").replace("\n", " ")
        return {
            "id": hit["id"],
            "name": d.get("name"),
            "organization": org.get("name"),
            "organization_id": row["org_id"],
            "description": desc[:220] + ("…" if len(desc) > 220 else ""),
            "phone": phone["number"] if phone else None,
            "address": addr,
            "eligibility": (d.get("eligibility_description") or "")[:170] or None,
            "apply_url": d.get("url"),
            "last_verified": d.get("assured_date"),
            "distance_km": hit.get("distance_km"),
            "score": hit["score"],
            "matched": hit["matched"][:4],
        }

    def _generate(self, question: str, context: str, fallback: str) -> str:
        if getattr(self.llm, "is_mock", True):
            return fallback
        try:  # pragma: no cover - needs Bedrock
            return (
                self.llm.complete(
                    SYSTEM, f"Context:\n{context}\n\nQuestion: {question}", max_tokens=300
                ).strip()
                or fallback
            )
        except Exception:  # noqa: BLE001
            return fallback

    # ---- main entry ----------------------------------------------------------------------------------
    def handle(self, principal, session_id: str | None, message: str) -> dict:
        user_id = principal.id if principal else None
        sid, state = self._session(session_id, user_id)
        self._log(sid, "user", message)
        out = self._respond(principal, sid, state, message.strip())
        out["session_id"] = sid
        self._log(
            sid,
            "assistant",
            out["reply"],
            {k: out[k] for k in ("cards", "citations", "actions", "intent") if out.get(k)},
        )
        self._save(sid, state)
        return out

    def _respond(self, principal, sid, state, msg) -> dict:
        base = {"cards": [], "citations": [], "actions": [], "intent": "unknown"}
        if CRISIS.search(msg):
            return base | {
                "intent": "crisis",
                "reply": "If you or someone near you is in danger, call 911 now. For emotional crisis, call or text 988 any time. "
                "For other urgent help, dial 211 to reach a trained specialist. I can still help you look for services when you are ready.",
            }
        explicit_need = bool(NEED.search(msg))
        is_need = explicit_need or any(stem(t) in EXPANSIONS for t in tokenize(msg))
        if (
            ABOUT.search(msg) and not explicit_need
        ):  # "How does One Degree work?" is not a job request
            hits = self._about_chunks(msg)
            text = (
                hits[0]["meta"]["text"]
                if hits
                else "One Degree is a free directory of community services."
            )
            first = " ".join(re.split(r"(?<=[.!?])\s+", re.sub(r"#+ .*\n", "", text).strip())[:3])
            return base | {"intent": "about", "reply": self._generate(msg, text, first)}
        if POLICY.search(msg) and not (
            NEED.search(msg)
            and not re.search(
                r"\b(bring|document|proof|eligib|qualif|requirement|policy|policies)\b", msg, re.I
            )
        ):
            return self._policy(msg, base)
        if is_need:
            state["last_need"] = msg
            if not principal:
                state["pending_need"] = msg
                return base | {
                    "intent": "need_requires_profile",
                    "reply": "I can help with that. To match you with providers and send a request for you, I need a quick profile first. "
                    "It is free, and you choose what to share.",
                    "actions": [
                        {"type": "signup", "label": "Create a profile"},
                        {"type": "login", "label": "Log in"},
                    ],
                }
            return self._match(principal, sid, state, msg, base)
        return base | {
            "intent": "clarify",
            "reply": "Tell me what you need help with, for example rent, food, diapers, a job or a doctor. "
            "You can also ask how One Degree works.",
        }

    def _match(self, principal, sid, state, msg, base) -> dict:
        prof = self.seekers.get_profile(principal.id)
        near = self.search.zip_centroid(prof["zip"]) if prof.get("zip") else None
        pool = self.search.search_services(msg, filters={"status": "active"}, limit=12, near=near)
        hits, seen = [], set()
        for h in pool:  # different programs first; same-named listings only if needed
            key = (h["meta"].get("name") or "").strip().lower()
            if key not in seen:
                seen.add(key)
                hits.append(h)
        hits = (hits + [h for h in pool if h not in hits])[:3]
        if not hits:
            return base | {
                "intent": "no_match",
                "reply": "I could not find a good match for that yet. Try describing it another way, or dial 211 to talk with a specialist.",
            }
        cards = [self._card(h) for h in hits]
        state["last_results"] = [c["id"] for c in cards]
        lines = "; ".join(f"{c['name']} ({c['organization']})" for c in cards)
        reply = (
            f"Here are the best matches I found: {lines}. Choose one and I will send your request."
        )
        if not prof.get("zip"):
            reply += " Add your ZIP code in your profile and I can show what is closest to you."
        ctx = "\n".join(
            f"- {c['name']} by {c['organization']}: {c['description']} Eligibility: {c['eligibility'] or 'not listed'}"
            for c in cards
        )
        reply = self._generate(msg, ctx, reply)
        actions = [
            {
                "type": "request_service",
                "service_id": c["id"],
                "label": f"Send request to {c['organization']}",
            }
            for c in cards
        ]
        return base | {"intent": "match", "reply": reply, "cards": cards, "actions": actions}

    def _policy(self, msg, base) -> dict:
        chunks = [h for h in self.search.search_chunks(msg, limit=3) if h["score"] >= 2.0]
        svc = self.search.search_services(msg, filters={"status": "active"}, limit=1)
        cites = [
            {
                "title": h["meta"]["title"],
                "document_id": h["meta"]["document_id"],
                "effective_date": h["meta"].get("effective_date"),
                "excerpt": self._excerpt(h),
            }
            for h in chunks
        ]
        facts = ""
        if svc:
            card = self._card(svc[0])
            row = self.store.get(svc[0]["id"])["data"]
            docs = ", ".join(x["document"] for x in row.get("required_documents", []))
            facts = (
                f"{card['name']} ({card['organization']})"
                + (f" asks you to bring: {docs}." if docs else ".")
                + (f" Eligibility: {card['eligibility']}." if card["eligibility"] else "")
            )
        if not cites and not facts:
            return base | {
                "intent": "policy_unknown",
                "reply": "I do not have a document that answers that. Please contact the provider directly to confirm.",
            }
        ctx = "\n".join(c["excerpt"] for c in cites) + "\n" + facts
        fallback = cites[0]["excerpt"] if cites else facts
        if cites and facts:
            fallback = f"{cites[0]['excerpt']} {facts}"
        reply = (
            self._generate(msg, ctx, fallback)
            + " Please confirm details with the provider, since policies can change."
        )
        return base | {"intent": "policy", "reply": reply.strip(), "citations": cites}

    @staticmethod
    def _excerpt(hit) -> str:
        t = hit["meta"].get("text") or ""
        return " ".join(t.split())[:320]

    def resume(self, principal, session_id: str) -> dict:
        """After sign-up in the same chat: continue with the need the visitor started with."""
        sid, state = self._session(session_id, principal.id)
        need = state.get("pending_need")
        if not need:
            return {
                "session_id": sid,
                "reply": "You are signed in. What do you need help with?",
                "cards": [],
                "citations": [],
                "actions": [],
                "intent": "clarify",
            }
        state.pop("pending_need", None)
        out = self._match(
            principal,
            sid,
            state,
            need,
            {"cards": [], "citations": [], "actions": [], "intent": "unknown"},
        )
        out["reply"] = "Thanks for signing up. " + out["reply"]
        out["session_id"] = sid
        self._log(
            sid,
            "assistant",
            out["reply"],
            {k: out[k] for k in ("cards", "actions", "intent") if out.get(k)},
        )
        self._save(sid, state)
        return out

    def confirm_request(self, principal, session_id: str, service_id: str) -> dict:
        prof = self.seekers.get_profile(principal.id)
        if not prof.get("share_with_providers"):
            raise ConsentRequired()
        sid, state = self._session(session_id, principal.id)
        need = state.get("last_need") or "Service request"
        hit_score = next((1.0 for i in state.get("last_results", []) if i == service_id), None)
        req = self.seekers.create_request(principal.id, need, service_id, hit_score)
        row = self.store.get(service_id)
        org = (self.store.get(row["org_id"]) or {}).get("data", {}).get("name", "the provider")
        reply = f"Done. I sent your request to {org}. They will see your first name, ZIP code and what you need, and can contact you using your chosen method."
        self._log(sid, "assistant", reply, {"intent": "request_sent", "request_id": req["id"]})
        return {
            "session_id": sid,
            "reply": reply,
            "request": req,
            "intent": "request_sent",
            "cards": [],
            "actions": [],
            "citations": [],
        }
