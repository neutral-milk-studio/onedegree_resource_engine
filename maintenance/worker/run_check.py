"""Maintenance worker: settings, and the sweep that checks the records that are due.

select-due -> fetch (website source) -> extract (Bedrock) -> compare -> save findings.
Each check is also written to the check log (last-checked time, result, model log).
Spec: Requirements 18.1, 18.4, 19.5, 20, 21, 22, 23.
"""

from __future__ import annotations

import hashlib
import os
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, fields
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from contracts.findings import Category, FindingRejected, FindingsSink
from contracts.store import HsdsStore, ServiceFilter
from contracts.website_source import WebsiteResult, WebsiteSource
from maintenance.comparator.compare import Comparison, check_page, failure, site_loaded
from maintenance.extractor.extract import (
    ExtractionError,
    Invoker,
    ModelNotConfigured,
    extract_facts,
    visible_text,
)

WORKER_MODES = ("sweep", "reports")  # sweep: the monthly check; reports: user-report triggers
WEBSITE_SOURCES = ("sample", "live")
# Upper bound for every whole-number setting (ten years), to catch a typo like an extra digit.
MAXIMUM = 3650


class ConfigError(ValueError):
    """A worker setting is invalid. Raised at startup, so no record is checked with bad settings."""


@dataclass(frozen=True)
class WorkerConfig:
    """Settings for one worker run. Every field has an environment variable (see ``ENV``)."""

    mode: str = "sweep"
    check_interval_days: int = 30  # a record is due when its last check is this old
    severe_report_threshold: int = 3  # distinct reporters of "unavailable" or "deletion"
    inaccuracy_report_threshold: int = 5  # distinct reporters of "inaccurate information"
    report_window_days: int = 30  # reports older than this don't count
    report_cooldown_days: int = 7  # no report-triggered check this soon after the last check
    website_source: str = "sample"

    # Environment variable for each field. The default is the field's default above.
    ENV = {
        "mode": "RE_WORKER_MODE",
        "check_interval_days": "RE_CHECK_INTERVAL_DAYS",
        "severe_report_threshold": "RE_REPORT_SEVERE_THRESHOLD",
        "inaccuracy_report_threshold": "RE_REPORT_INACCURACY_THRESHOLD",
        "report_window_days": "RE_REPORT_WINDOW_DAYS",
        "report_cooldown_days": "RE_REPORT_COOLDOWN_DAYS",
        "website_source": "RE_WEBSITE_SOURCE",
    }
    # The smallest allowed value for each whole-number setting.
    MINIMUM = {
        "check_interval_days": 1,
        "severe_report_threshold": 1,
        "inaccuracy_report_threshold": 1,
        "report_window_days": 1,
        "report_cooldown_days": 0,
    }

    def __post_init__(self) -> None:
        if self.mode not in WORKER_MODES:
            raise ConfigError(f"{self.ENV['mode']} must be one of {', '.join(WORKER_MODES)}")
        if self.website_source not in WEBSITE_SOURCES:
            raise ConfigError(
                f"{self.ENV['website_source']} must be one of {', '.join(WEBSITE_SOURCES)}"
            )
        for name, minimum in self.MINIMUM.items():
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not minimum <= value <= MAXIMUM
            ):
                raise ConfigError(
                    f"{self.ENV[name]} must be a whole number from {minimum} to {MAXIMUM}"
                )
        if self.report_cooldown_days >= self.report_window_days:
            # When the cooldown ends, only reports from the last window-many days count. A
            # cooldown at least as long as the window would let reports made early in the
            # cooldown age out before they could trigger a check.
            raise ConfigError(
                f"{self.ENV['report_cooldown_days']} must be shorter than "
                f"{self.ENV['report_window_days']}"
            )

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> WorkerConfig:
        """Read the settings from the environment. A missing or blank variable uses the default."""
        env = os.environ if env is None else env
        values: dict[str, object] = {}
        for item in fields(cls):
            raw = env.get(cls.ENV[item.name], "").strip()
            if not raw:
                continue
            if item.name in cls.MINIMUM:
                # Plain ASCII digits only: int() would also accept "1_0", "+5" and other
                # scripts' digits, which are more likely typos than intent.
                if not (raw.isascii() and raw.isdigit()):
                    raise ConfigError(f"{cls.ENV[item.name]} must be a whole number, not {raw!r}")
                values[item.name] = int(raw)
            else:
                values[item.name] = raw.lower()
        return cls(**values)


def website_source_for(config: WorkerConfig) -> WebsiteSource:
    """Build the configured website source. Nothing else needs to know which one runs."""
    if config.website_source == "live":
        # Failing here, at startup, beats a failed check for every record.
        raise ConfigError(
            f"{WorkerConfig.ENV['website_source']}=live is not implemented yet; use sample"
        )
    from maintenance.website_source.sample import SampleWebsiteSource

    return SampleWebsiteSource()


# --------------------------------------------------------------------------- #
# Check log
#
# PROVISIONAL: the check log has no shared contract yet. These types move to contracts/
# in the separate contract PR (both owners approve), and the Postgres version is an
# ``ops`` table, never part of an HSDS record.
# --------------------------------------------------------------------------- #


@dataclass
class CheckLogEntry:
    """One check of one record: when, the result, and the model log (R18.4, R20.2)."""

    service_id: str
    checked_at: str  # ISO 8601, UTC
    category: Category | None  # None only when no check could run (see ``note``)
    url: str | None
    fetch_failed: bool  # the page didn't load; two in a row make a site actively bad
    finding_ids: list[str] = field(default_factory=list)
    note: str = ""  # why the check ended the way it did, for operators
    content_sha256: str | None = None  # of the fetched page, so a later run can skip a re-read
    model_id: str = ""
    usage: dict[str, int] = field(default_factory=dict)
    raw_response: str | None = None  # the model's reply as received
    rejected_facts: list[dict[str, Any]] = field(default_factory=list)  # dropped by the extractor
    dropped_findings: list[dict[str, Any]] = field(default_factory=list)  # quotes not in the page
    rejected_findings: list[str] = field(default_factory=list)  # the sink refused these
    page_warnings: list[str] = field(default_factory=list)
    truncated: bool = False


class CheckLog(Protocol):
    def last(self, service_id: str) -> CheckLogEntry | None:
        """The latest check of a record, or None if it has never been checked."""
        ...

    def record(self, entry: CheckLogEntry) -> None:
        """Add a check. It becomes the record's latest."""
        ...


class InMemoryCheckLog:
    """Check log for tests and for running without a database."""

    def __init__(self) -> None:
        self.entries: list[CheckLogEntry] = []
        self._latest: dict[str, CheckLogEntry] = {}

    def last(self, service_id: str) -> CheckLogEntry | None:
        return self._latest.get(service_id)

    def record(self, entry: CheckLogEntry) -> None:
        self.entries.append(entry)
        self._latest[entry.service_id] = entry


# --------------------------------------------------------------------------- #
# Sweep
# --------------------------------------------------------------------------- #

PER_PAGE = 100  # services read from the store per page


class WorkerSetupError(RuntimeError):
    """Every record would fail the same way, so stop: no model id, no Bedrock access, or
    findings or the check log can't be written.

    The checks finished before it are saved and logged; ``summary`` reports them, and
    ``summary.stopped_at`` names the record being checked (it stays due for the next run).
    """

    def __init__(self, message: str, summary: RunSummary) -> None:
        super().__init__(message)
        self.summary = summary


@dataclass
class RunSummary:
    """What one sweep did. Nothing in it is silent: every record due is accounted for."""

    due: int = 0
    checked: int = 0  # checks that ended with at least one category
    categories: Counter[str] = field(default_factory=Counter)  # one per check
    findings_saved: int = 0
    findings_rejected: list[str] = field(default_factory=list)  # "service id: reason"
    no_website: list[str] = field(default_factory=list)  # service ids with no url to check
    not_found: list[str] = field(default_factory=list)  # listed, then gone before the check
    errors: list[str] = field(default_factory=list)  # "service id: error type", as check_failed
    unlisted: int = 0  # listed services with no id, which can't be checked or logged
    stopped_at: str | None = None  # the record being checked when a setup error stopped the run


def iso(moment: datetime) -> str:
    """UTC ISO 8601 with a Z, to the second: the ``checked_at`` format of findings."""
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(text: str) -> datetime:
    moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _utc_now() -> datetime:
    return datetime.now(UTC)


def services_in(store: HsdsStore) -> list[dict[str, Any]]:
    """Every service the store lists, read page by page (pages start at 1)."""
    services: list[dict[str, Any]] = []
    page_number = 1
    while True:
        page = store.list_services(ServiceFilter(), page_number, PER_PAGE)
        services.extend(page.contents)
        # Stop on an empty page too, so a store that never reports a last page can't loop.
        if page.last_page or page.empty or not page.contents or page_number >= page.total_pages:
            return services
        page_number += 1


def due_records(
    store: HsdsStore,
    check_log: CheckLog,
    *,
    now: datetime,
    interval_days: int,
    summary: RunSummary | None = None,
) -> list[str]:
    """Ids of the services due for a check: never checked first, then the oldest check.

    A service is due when it has never been checked, its last check is at least
    ``interval_days`` old, or the time of its last check can't be read. A listed service
    with no id is counted in ``summary.unlisted``.
    """
    cutoff = now - timedelta(days=interval_days)
    ids: list[str] = []
    for service in services_in(store):
        service_id = service.get("id") if isinstance(service, dict) else None
        if isinstance(service_id, str) and service_id.strip():
            ids.append(service_id)
        elif summary is not None:
            summary.unlisted += 1
    never: list[str] = []
    stale: list[tuple[datetime, str]] = []
    for service_id in dict.fromkeys(ids):
        last = check_log.last(service_id)
        checked = _readable_time(last.checked_at) if last is not None else None
        if checked is None:
            never.append(service_id)  # never checked, or a log time that can't be read
        elif checked <= cutoff:
            stale.append((checked, service_id))
    return never + [service_id for _, service_id in sorted(stale)]


def _readable_time(text: Any) -> datetime | None:
    try:
        return parse_iso(text)
    except (TypeError, ValueError, AttributeError):
        return None


def _is_setup_error(exc: Exception) -> bool:
    """Errors that would hit every record: no model id, or Bedrock refusing the request.

    The Bedrock invoker already turns throttling, timeouts and a too-long page into
    ``ExtractionError``; any other botocore error (access denied, unknown model) is setup.
    """
    return isinstance(exc, ModelNotConfigured) or type(exc).__module__.startswith("botocore")


def _fetch(website_source: WebsiteSource, url: str) -> tuple[WebsiteResult, str]:
    """Fetch a page. A source that raises is treated as a fetch that failed.

    Returns the result and a note for the log. Only the error type goes into the result,
    because the result's error becomes finding evidence and a message could quote anything.
    """
    try:
        return website_source.get(url), ""
    except Exception as exc:  # noqa: BLE001 - any fetch failure is a failed check, not a crash
        kind = type(exc).__name__
        return (
            WebsiteResult(content=None, status=None, error=f"{kind} while fetching the page"),
            f"the website source raised {kind}: {exc}",
        )


def check_record(
    record: dict[str, Any],
    *,
    url: str,
    website_source: WebsiteSource,
    invoke: Invoker | None,
    previous: CheckLogEntry | None,
    checked_at: str,
) -> tuple[Comparison, CheckLogEntry]:
    """Check one record: fetch, extract when the page loaded, compare.

    Always returns a result with at least one finding. Raises only for setup errors.
    """
    site, note = _fetch(website_source, url)
    entry = CheckLogEntry(
        service_id=record["id"],
        checked_at=checked_at,
        category=None,
        url=url,
        fetch_failed=not site_loaded(site),
        note=note,
    )
    if site.content:
        entry.content_sha256 = hashlib.sha256(site.content.encode("utf-8")).hexdigest()

    extraction = None
    if not entry.fetch_failed and visible_text(site.content or "").strip():
        try:
            extraction = extract_facts(site.content or "", invoke=invoke)
        except ExtractionError as exc:
            entry.note = f"fact extraction failed: {exc}"
            comparison = failure(
                record, Category.CHECK_FAILED, entry.note, url=url, checked_at=checked_at
            )
            return comparison, entry
        entry.model_id = extraction["model_id"]
        entry.usage = dict(extraction["usage"])
        entry.raw_response = extraction["raw_response"]
        entry.rejected_facts = list(extraction["rejected"])
        entry.page_warnings = list(extraction["warnings"])
        entry.truncated = bool(extraction["truncated"])

    comparison = check_page(
        record,
        site,
        extraction,
        url=url,
        checked_at=checked_at,
        unreachable_before=bool(previous and previous.fetch_failed),
    )
    entry.dropped_findings = list(comparison.dropped)
    return comparison, entry


def run_checks(
    store: HsdsStore,
    website_source: WebsiteSource,
    *,
    sink: FindingsSink,
    check_log: CheckLog,
    config: WorkerConfig | None = None,
    invoke: Invoker | None = None,
    now: Callable[[], datetime] = _utc_now,
    limit: int | None = None,
) -> RunSummary:
    """Check every record that is due, save the findings, and log each check.

    Detection only proposes: it saves findings through ``sink`` and never changes a record.
    ``invoke`` is the model call (Bedrock by default). ``limit`` caps the checks in one run;
    the rest stay due for the next run.

    Raises ``WorkerSetupError`` when every record would fail the same way. A problem with
    one record (a page that won't load, an unusable model answer, a bug hit by one odd
    record) becomes a ``check_failed`` finding for that record, and the run continues.
    """
    config = config or WorkerConfig()
    if config.mode != "sweep":
        raise NotImplementedError(f"{WorkerConfig.ENV['mode']}={config.mode} is not built yet")
    if limit is not None and limit < 0:
        raise ValueError("limit can't be negative")

    summary = RunSummary()
    due = due_records(
        store,
        check_log,
        now=_aware(now()),
        interval_days=config.check_interval_days,
        summary=summary,
    )
    if limit is not None:
        due = due[:limit]
    summary.due = len(due)

    for service_id in due:
        record = store.get_service(service_id)
        if record is None:
            summary.not_found.append(service_id)
            continue
        checked_at = iso(_aware(now()))
        url = record.get("url").strip() if isinstance(record.get("url"), str) else ""
        if not url:
            # A finding needs a url for its evidence, so a record with no website can't get
            # one. It is logged and reported instead, never skipped silently.
            summary.no_website.append(service_id)
            entry = CheckLogEntry(
                service_id=service_id,
                checked_at=checked_at,
                category=None,
                url=None,
                fetch_failed=False,
                note="the record has no website url to check",
            )
            _persist(check_log.record, entry, summary=summary, service_id=service_id)
            continue

        previous = check_log.last(service_id)
        try:
            comparison, entry = check_record(
                record,
                url=url,
                website_source=website_source,
                invoke=invoke,
                previous=previous,
                checked_at=checked_at,
            )
        except Exception as exc:
            if _is_setup_error(exc):
                summary.stopped_at = service_id
                raise WorkerSetupError(f"stopped the run: {exc}", summary) from exc
            # One record broke the check. Report it as a failed check and carry on; the
            # error type (not the message, which may quote the page) goes on the finding.
            summary.errors.append(f"{service_id}: {type(exc).__name__}")
            entry = CheckLogEntry(
                service_id=service_id,
                checked_at=checked_at,
                category=None,
                url=url,
                fetch_failed=False,
                note=f"the check errored: {type(exc).__name__}: {exc}",
            )
            comparison = failure(
                record,
                Category.CHECK_FAILED,
                f"the check errored ({type(exc).__name__})",
                url=url,
                checked_at=checked_at,
            )

        # Findings are saved before the log entry. If the log can't be written the record
        # stays due, so a failure here repeats findings rather than losing them.
        for finding in comparison.findings:
            try:
                saved_id = _persist(sink.save, finding, summary=summary, service_id=service_id)
            except FindingRejected as exc:
                entry.rejected_findings.append(f"{finding.id}: {exc}")
                summary.findings_rejected.append(f"{service_id}: {exc}")
                continue
            entry.finding_ids.append(saved_id)
            summary.findings_saved += 1
        entry.category = comparison.category
        _persist(check_log.record, entry, summary=summary, service_id=service_id)
        summary.checked += 1
        summary.categories[comparison.category.value] += 1
    return summary


def _persist(
    write: Callable[[Any], Any], item: Any, *, summary: RunSummary, service_id: str
) -> Any:
    """Write a finding or a log entry. A store that fails would fail for every record, so the
    run stops with what it has done so far. ``FindingRejected`` is the finding's own fault and
    passes through to the caller.
    """
    try:
        return write(item)
    except FindingRejected:
        raise
    except Exception as exc:
        summary.stopped_at = service_id
        what = "the check log" if isinstance(item, CheckLogEntry) else "a finding"
        raise WorkerSetupError(
            f"stopped the run: could not write {what} ({type(exc).__name__}: {exc})", summary
        ) from exc


def _aware(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        raise ValueError("now() must return a timezone-aware datetime")
    return moment


def main() -> None:
    """Entry point for the worker container."""
    try:
        config = WorkerConfig.from_env()
        website_source_for(config)  # reject bad settings at startup, before any check runs
    except ConfigError as error:
        raise SystemExit(f"invalid worker setting: {error}") from None
    # run_checks is ready, but there is no HSDS store or findings store to run it against
    # yet: api/app/store_pg.py and maintenance/findings_store/pg_findings.py are stubs.
    raise SystemExit(
        "maintenance worker: no HSDS store or findings store is wired yet "
        "(PgHsdsStore and PgFindingsStore are stubs)"
    )


if __name__ == "__main__":  # pragma: no cover
    main()
