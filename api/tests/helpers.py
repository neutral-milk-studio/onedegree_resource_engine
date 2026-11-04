"""A loaded app for API tests: generated demo data, demo accounts, documents and findings."""

from __future__ import annotations

import tempfile
from pathlib import Path

from fastapi.testclient import TestClient

from api.app.config import load_settings
from api.app.container import build_container
from api.app.main import create_app
from api.app.services.ingest import SourceConfig
from tools.sample_data_generator import seed as seed_mod
from tools.sample_data_generator.generate import generate


def make_container(**overrides):
    folder = tempfile.mkdtemp()
    settings = load_settings().with_overrides(
        data_dir=Path(folder), database_url=f"sqlite:///{folder}/t.db", **overrides
    )
    return build_container(settings)


def generated_container(n_odrs=60, n_ndp=30, seed=3):
    gen = Path(tempfile.mkdtemp()) / "gen"
    generate(gen, n_odrs, n_ndp, seed)
    seed_mod.GEN = gen
    c = make_container()
    for name in ("odrs", "ndp"):
        c.ingest.run(SourceConfig(name, name, str(gen / name / "*.json")), "test", "test")
    c.maintenance.website = seed_mod.CombinedPages(
        c.maintenance.website, seed_mod.GeneratedPages(gen / "websites.json")
    )
    return c, gen


def login(client, email, password):
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


def app_with_demo():
    c, gen = generated_container()
    out = seed_mod.seed_demo(c)
    return c, TestClient(create_app(container=c)), out, gen
