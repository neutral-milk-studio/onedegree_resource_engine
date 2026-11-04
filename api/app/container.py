"""Composition root: builds every service once. Swapping a backend (SQLite->Aurora, mock->Bedrock,
sample->live websites, memory->OpenSearch, disk->S3, local->Cognito) happens here, driven by settings."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from api.app.config import Settings, load_settings
from api.app.hsds.validator import HsdsValidator
from api.app.services.blob import build_blob_store
from api.app.services.chat import ChatEngine
from api.app.services.documents import DocumentService
from api.app.services.ingest import IngestService, load_source_configs
from api.app.services.llm import build_llm
from api.app.services.proposals import ChangeRequests
from api.app.services.search import SearchService
from api.app.services.seekers import SeekerService
from api.app.services.store import HsdsStore
from api.app.services.users import UserService, build_auth
from maintenance.extractor.offline import offline_invoke
from maintenance.worker.run_check import WorkerConfig, website_source_for
from maintenance.worker.service import MaintenanceService
from shared.db import Database


@dataclass
class Container:
    settings: Settings
    db: Database
    validator: HsdsValidator
    store: HsdsStore
    proposals: ChangeRequests
    blob: Any
    ingest: IngestService
    search: SearchService
    users: UserService
    auth: Any
    llm: Any
    maintenance: MaintenanceService
    documents: DocumentService
    seekers: SeekerService
    chat: ChatEngine
    jobs: Any = None

    def sources(self):
        return load_source_configs(self.settings.sources_path)

    def ingest_all(
        self, trigger="scheduled", who="scheduler", only: str | None = None
    ) -> list[dict]:
        return [
            self.ingest.run(c, trigger, who)
            for c in self.sources()
            if c.enabled and (only in (None, c.name))
        ]


def build_container(settings: Settings | None = None) -> Container:
    s = settings or load_settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    db = Database(s.database_url)
    db.init_schema()
    validator = HsdsValidator()
    store = HsdsStore(db, validator)
    proposals = ChangeRequests(db, store)
    blob = build_blob_store(s)
    search = SearchService(s, db, store)
    users = UserService(db)
    llm = build_llm(s)
    website = website_source_for(WorkerConfig(website_source=s.website_backend))
    invoke = None if s.llm_backend == "bedrock" else offline_invoke  # None: the Bedrock default
    maintenance = MaintenanceService(s, db, store, proposals, website, invoke)
    seekers = SeekerService(db, search, store)
    c = Container(
        settings=s,
        db=db,
        validator=validator,
        store=store,
        proposals=proposals,
        blob=blob,
        ingest=IngestService(s, db, store, blob, proposals),
        search=search,
        users=users,
        auth=build_auth(s, users),
        llm=llm,
        maintenance=maintenance,
        documents=DocumentService(s, db, blob),
        seekers=seekers,
        chat=ChatEngine(s, db, store, search, llm, seekers),
    )
    from api.app.jobs.runner import JobRunner

    c.jobs = JobRunner(c)
    return c
