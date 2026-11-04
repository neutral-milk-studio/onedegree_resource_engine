import io
import json
import unittest

from api.tests.helpers import app_with_demo, login


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c, cls.cl, cls.seed, cls.gen = app_with_demo()
        cls.admin = login(cls.cl, "admin@demo.org", "demo-admin-1")
        cls.prov = login(cls.cl, "provider1@demo.org", "demo-provider-1")
        cls.seek = login(cls.cl, "ana@demo.org", "demo-seeker-1")
        cls.key = cls.seed["api_keys"]["Demo key (all sources)"]
        cls.odrs_key = cls.seed["api_keys"]["Demo key (ODRS only)"]

    def H(self, key):
        return {"Authorization": f"Bearer {key}"}

    # ---- HSDS API ----------------------------------------------------------------------------------
    def test_hsds_api_requires_a_key(self):
        self.assertEqual(self.cl.get("/hsds/services").status_code, 401)
        self.assertEqual(self.cl.get("/hsds/services", headers=self.H("re_wrong")).status_code, 401)
        self.assertEqual(
            self.cl.get("/hsds/services", headers=self.seek).status_code, 401
        )  # a seeker login is not an API key

    def test_list_is_paginated_and_every_service_validates(self):
        r = self.cl.get("/hsds/services?per_page=10&page=2", headers=self.H(self.key)).json()
        self.assertEqual(
            {
                "total_items",
                "total_pages",
                "page_number",
                "size",
                "first_page",
                "last_page",
                "empty",
                "contents",
            },
            set(r),
        )
        self.assertEqual((r["page_number"], r["size"], r["first_page"]), (2, 10, False))
        for s in r["contents"]:
            self.assertEqual(self.c.validator.errors("service", s), [])
            self.assertNotIn("x_resource_engine", s)  # extensions hidden unless requested

    def test_get_by_id_is_nested_hsds(self):
        sid = self.cl.get("/hsds/services?per_page=1", headers=self.H(self.key)).json()["contents"][
            0
        ]["id"]
        s = self.cl.get(f"/hsds/services/{sid}", headers=self.H(self.key)).json()
        self.assertEqual(self.c.validator.errors("service", s), [])
        self.assertIn("organization", s)
        self.assertEqual(
            self.cl.get(
                "/hsds/services/00000000-0000-0000-0000-000000000000", headers=self.H(self.key)
            ).status_code,
            404,
        )

    def test_key_scoped_to_one_source(self):
        all_n = self.cl.get("/hsds/services?minimal=true", headers=self.H(self.key)).json()[
            "total_items"
        ]
        odrs = self.cl.get("/hsds/services?minimal=true", headers=self.H(self.odrs_key)).json()
        self.assertLess(odrs["total_items"], all_n)
        ndp_id = next(r["id"] for r in self.c.store.all_of_type("service") if r["source"] == "ndp")
        self.assertEqual(
            self.cl.get(f"/hsds/services/{ndp_id}", headers=self.H(self.odrs_key)).status_code, 404
        )

    def test_search_through_the_api(self):
        r = self.cl.get("/hsds/services?search=diapers&per_page=5", headers=self.H(self.key)).json()
        self.assertTrue(
            any(
                "diaper" in s["name"].lower() or "diaper" in s["organization"]["name"].lower()
                for s in r["contents"]
            )
        )

    # ---- roles -------------------------------------------------------------------------------------
    def test_role_boundaries(self):
        self.assertEqual(self.cl.get("/api/admin/stats").status_code, 401)
        self.assertEqual(self.cl.get("/api/admin/stats", headers=self.seek).status_code, 403)
        self.assertEqual(self.cl.get("/api/admin/stats", headers=self.prov).status_code, 403)
        self.assertEqual(
            self.cl.get("/api/provider/organization", headers=self.seek).status_code, 403
        )
        self.assertEqual(self.cl.get("/api/admin/stats", headers=self.admin).status_code, 200)

    # ---- provider -> admin approval -----------------------------------------------------------------
    def test_provider_change_needs_admin_approval_and_leaves_history(self):
        org = self.cl.get("/api/provider/organization", headers=self.prov).json()
        svc = org["services"][0]
        before = svc["data"].get("wait_time")
        r = self.cl.post(
            "/api/provider/change-requests",
            headers=self.prov,
            json={"resource_id": svc["id"], "patch": {"wait_time": "Same day"}},
        )
        self.assertEqual(r.status_code, 201)
        self.assertEqual(
            self.c.store.get(svc["id"])["data"].get("wait_time"), before
        )  # not applied yet
        cid = r.json()["id"]
        self.assertEqual(
            self.cl.post(
                f"/api/admin/change-requests/{cid}/approve", headers=self.prov, json={}
            ).status_code,
            403,
        )
        self.assertEqual(
            self.cl.post(
                f"/api/admin/change-requests/{cid}/approve", headers=self.admin, json={"note": "ok"}
            ).status_code,
            200,
        )
        self.assertEqual(self.c.store.get(svc["id"])["data"]["wait_time"], "Same day")
        h = self.c.store.history(svc["id"])[0]
        self.assertEqual(
            (h["field_name"], h["updated_by"], h["actor_type"]),
            ("wait_time", "provider1@demo.org", "provider"),
        )
        self.assertIn("approved by admin@demo.org", h["note"])

    def test_provider_cannot_touch_another_organization(self):
        other = next(
            r
            for r in self.c.store.all_of_type("service")
            if r["org_id"] != self.cl.get("/api/auth/me", headers=self.prov).json()["org_id"]
        )
        r = self.cl.post(
            "/api/provider/change-requests",
            headers=self.prov,
            json={"resource_id": other["id"], "patch": {"wait_time": "x"}},
        )
        self.assertEqual(r.status_code, 403)

    def test_invalid_provider_change_is_rejected_up_front(self):
        svc = self.cl.get("/api/provider/organization", headers=self.prov).json()["services"][0]
        r = self.cl.post(
            "/api/provider/change-requests",
            headers=self.prov,
            json={"resource_id": svc["id"], "patch": {"status": "nope"}},
        )
        self.assertEqual(r.status_code, 422)

    def test_stale_proposal_needs_explicit_force(self):
        svc = self.cl.get("/api/provider/organization", headers=self.prov).json()["services"][0]
        cid = self.cl.post(
            "/api/provider/change-requests",
            headers=self.prov,
            json={"resource_id": svc["id"], "patch": {"fees_description": "Free of charge"}},
        ).json()["id"]
        self.cl.put(
            f"/api/admin/records/{svc['id']}",
            headers=self.admin,
            json={"patch": {"fees_description": "Sliding scale"}},
        )
        r = self.cl.post(f"/api/admin/change-requests/{cid}/approve", headers=self.admin, json={})
        self.assertEqual(r.status_code, 409)
        self.assertIn("fees_description", r.json()["stale_fields"])
        self.assertEqual(
            self.cl.post(
                f"/api/admin/change-requests/{cid}/approve",
                headers=self.admin,
                json={"force": True},
            ).status_code,
            200,
        )

    def test_admin_direct_edit_validates_and_records_history(self):
        sid = self.c.store.all_of_type("service")[0]["id"]
        self.assertEqual(
            self.cl.put(
                f"/api/admin/records/{sid}", headers=self.admin, json={"patch": {"status": "bogus"}}
            ).status_code,
            422,
        )
        self.assertEqual(
            self.cl.put(
                f"/api/admin/records/{sid}",
                headers=self.admin,
                json={"patch": {"wait_time": "1 week"}},
            ).status_code,
            200,
        )
        self.assertEqual(
            self.cl.get(f"/api/admin/records/{sid}", headers=self.admin).json()["history"][0][
                "updated_by"
            ],
            "admin@demo.org",
        )

    # ---- seeker chat -------------------------------------------------------------------------------
    def test_anonymous_service_request_asks_for_a_profile(self):
        r = self.cl.post("/api/chat", json={"message": "I need rental assistance"}).json()
        self.assertEqual(r["intent"], "need_requires_profile")
        self.assertEqual({a["type"] for a in r["actions"]}, {"signup", "login"})
        self.assertEqual(r["cards"], [])  # no provider details before a profile exists

    def test_anonymous_can_ask_about_one_degree(self):
        r = self.cl.post(
            "/api/chat", json={"message": "How does One Degree work? Is it free?"}
        ).json()
        self.assertEqual(r["intent"], "about")
        self.assertIn("free", r["reply"].lower())

    def test_signup_in_chat_then_resume_with_matches(self):
        sid = self.cl.post("/api/chat", json={"message": "I need help paying rent"}).json()[
            "session_id"
        ]
        reg = self.cl.post(
            "/api/auth/register",
            json={"email": "new@demo.org", "name": "New Person", "password": "longenough1"},
        ).json()
        h = {"Authorization": f"Bearer {reg['token']}"}
        r = self.cl.post("/api/chat/resume", headers=h, json={"session_id": sid}).json()
        self.assertEqual(r["intent"], "match")
        self.assertTrue(r["cards"])

    def test_signed_in_chat_matches_then_request_needs_consent(self):
        r = self.cl.post(
            "/api/chat", headers=self.seek, json={"message": "I don't have money to buy diapers"}
        ).json()
        self.assertEqual(r["intent"], "match")
        self.assertIn("diaper", (r["cards"][0]["name"] + r["cards"][0]["organization"]).lower())
        reg = self.cl.post(
            "/api/auth/register",
            json={"email": "noconsent@demo.org", "name": "No Consent", "password": "longenough1"},
        ).json()
        h = {"Authorization": f"Bearer {reg['token']}"}
        sid = self.cl.post("/api/chat", headers=h, json={"message": "I need diapers"}).json()
        resp = self.cl.post(
            "/api/chat/confirm",
            headers=h,
            json={"session_id": sid["session_id"], "service_id": sid["cards"][0]["id"]},
        )
        self.assertEqual((resp.status_code, resp.json()["code"]), (409, "consent_required"))
        self.cl.put(
            "/api/seeker/profile", headers=h, json={"share_with_providers": True, "zip": "94110"}
        )
        ok = self.cl.post(
            "/api/chat/confirm",
            headers=h,
            json={"session_id": sid["session_id"], "service_id": sid["cards"][0]["id"]},
        )
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.json()["request"]["status"], "open")

    def test_policy_answer_cites_the_document(self):
        r = self.cl.post(
            "/api/chat",
            json={"message": "What do I need to bring for the diaper bank? Do I need ID?"},
        ).json()
        self.assertEqual(r["intent"], "policy")
        self.assertTrue(r["citations"])
        self.assertTrue(r["citations"][0]["effective_date"])

    def test_crisis_message_gets_safety_information_first(self):
        r = self.cl.post("/api/chat", json={"message": "I want to hurt myself"}).json()
        self.assertEqual(r["intent"], "crisis")
        self.assertIn("988", r["reply"])

    # ---- provider view of seekers --------------------------------------------------------------------
    def test_provider_sees_consented_needs_and_served_people(self):
        needs = self.cl.get("/api/provider/needs", headers=self.prov).json()
        self.assertTrue(needs["direct"] or needs["suggested"])
        for card in needs["direct"] + needs["suggested"]:
            self.assertNotIn("email", card)  # contact details stay hidden until accepted
        rid = needs["direct"][0]["request_id"]
        self.assertEqual(
            self.cl.post(
                f"/api/provider/requests/{rid}/respond",
                headers=self.prov,
                json={"action": "accept"},
            ).status_code,
            200,
        )
        served = self.cl.get("/api/provider/served", headers=self.prov).json()["served"]
        self.assertTrue(any(s["request_id"] == rid and s.get("email") for s in served))

    # ---- documents -----------------------------------------------------------------------------------
    def test_provider_upload_waits_for_approval_then_feeds_chat(self):
        d = self.cl.post(
            "/api/provider/documents",
            headers=self.prov,
            files={
                "file": (
                    "telescopes.txt",
                    b"Library cards policy\n\nYou may borrow telescopes for two weeks. Bring a library card.",
                )
            },
            data={"title": "Telescope loans"},
        ).json()
        self.assertEqual(d["status"], "pending")
        q = {"message": "Can I borrow a telescope? What do I need to bring, any policy?"}
        self.assertNotEqual(self.cl.post("/api/chat", json=q).json()["intent"], "policy_telescope")
        before = self.cl.post("/api/chat", json=q).json()
        self.assertFalse([c for c in before["citations"] if c["title"] == "Telescope loans"])
        self.assertEqual(
            self.cl.post(
                f"/api/admin/documents/{d['id']}/decide", headers=self.admin, json={"approve": True}
            ).status_code,
            200,
        )
        after = self.cl.post("/api/chat", json=q).json()
        self.assertTrue([c for c in after["citations"] if c["title"] == "Telescope loans"])


if __name__ == "__main__":
    unittest.main()
