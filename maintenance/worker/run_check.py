"""Maintenance worker: settings, and the sweep that checks the records that are due.

select-due -> fetch (website source) -> review need -> extract (Bedrock) -> compare -> save.
Each check is also written to the check log (last-checked time, result, model log).

Review need: a page and record that are both unchanged since the last completed AI review
skip the model and carry that result forward. Anything else gets the standard AI review,
including a record whose page has never been reviewed: records come from agency databases
that vet their organizations, so a first review needs no extra human sign-off.

User reports (counted per distinct reporter, since the last check, within the window):
- severe (unavailable or deletion) at the threshold: a fresh AI review even on an unchanged
  page, and the legitimacy flag. The category is whatever the review finds.
- inaccuracy at the threshold: an unchanged page skips the model and raises the
  reports-on-unchanged-page flag; a changed page gets the standard AI review.
Severe wins when both cross. The ``reports`` mode checks only reported records, and not
within the cooldown after a record's last check.
Spec: Requirements 18.1, 18.4, 19.5, 20, 21, 22, 23.
"""

from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, fields
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any, Protocol

from contracts.findings import Category, FindingRejected, FindingsSink
from contracts.store import HsdsStore, ServiceFilter
from contracts.website_source import WebsiteResult, WebsiteSource
from maintenance.comparator.compare import Comparison, check_page, failure, site_loaded
from maintenance.extractor.extract import (
    MODEL_ID_ENV,
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
# Check log and review flags
#
# PROPOSED FOR CONTRACTS: these types have no shared contract yet. They move to contracts/
# in the separate contract PR (both owners approve); until then nothing outside detection
# should import them. The Postgres check log is an ``ops`` table, never part of HSDS.
# --------------------------------------------------------------------------- #


class ReviewFlag(str, Enum):  # noqa: UP042 - str+Enum matches contracts/findings.py
    """Asks a person to look at a record, whatever its category. Detection only raises
    flags; a person resolves them (they are sticky in the review layer)."""

    LEGITIMACY_REVIEW = "legitimacy_review"  # severe user reports (unavailable, deletion)
    REPORTS_ON_UNCHANGED_PAGE = "reports_on_unchanged_page"  # inaccuracy reports, same page


# Part of the record hash. Change it when extraction or comparison changes enough that
# earlier results shouldn't be carried forward, and every record is reviewed again.
REVIEW_VERSION = "1"


REPORT_KINDS = ("inaccurate", "unavailable", "deletion")
SEVERE_KINDS = ("unavailable", "deletion")


class ReportTrigger(str, Enum):  # noqa: UP042 - str+Enum matches contracts/findings.py
    SEVERE = "severe"  # unavailable or deletion reports at the threshold
    INACCURACY = "inaccuracy"  # inaccurate-information reports at the threshold


@dataclass
class ReportSummary:
    """Distinct reporters of a record since a time. Each person counts once per bucket."""

    service_id: str
    severe_reporters: int  # reported it unavailable or for deletion
    inaccuracy_reporters: int  # reported inaccurate information


class ReportSource(Protocol):
    def services_with_reports(self, since: str) -> list[str]:
        """Ids of the services reported after ``since`` (ISO 8601)."""
        ...

    def summary(self, service_id: str, since: str) -> ReportSummary:
        """Distinct reporters of one service after ``since``."""
        ...


class NoReports:
    """The default: no user reports, so only the monthly sweep runs checks."""

    def services_with_reports(self, since: str) -> list[str]:
        return []

    def summary(self, service_id: str, since: str) -> ReportSummary:
        return ReportSummary(service_id, 0, 0)


@dataclass(frozen=True)
class UserReport:
    service_id: str
    reporter_id: str  # one identity per person, so a person reporting twice counts once
    kind: str  # one of REPORT_KINDS
    reported_at: str  # ISO 8601

    def __post_init__(self) -> None:
        if self.kind not in REPORT_KINDS:
            raise ValueError(f"report kind must be one of {', '.join(REPORT_KINDS)}")
        if not self.reporter_id.strip():
            raise ValueError("a report needs a reporter identity, or it can't be counted once")


class InMemoryReportSource:
    """Reports held in memory: for tests, the demo, and as the counting rules to match."""

    def __init__(self, reports: list[UserReport] | None = None) -> None:
        self.reports = list(reports or [])

    def add(self, report: UserReport) -> None:
        self.reports.append(report)

    def _after(self, since: str) -> list[UserReport]:
        start = parse_iso(since)
        return [r for r in self.reports if parse_iso(r.reported_at) > start]

    def services_with_reports(self, since: str) -> list[str]:
        return list(dict.fromkeys(r.service_id for r in self._after(since)))

    def summary(self, service_id: str, since: str) -> ReportSummary:
        mine = [r for r in self._after(since) if r.service_id == service_id]
        severe = {r.reporter_id for r in mine if r.kind in SEVERE_KINDS}
        inaccurate = {r.reporter_id for r in mine if r.kind == "inaccurate"}
        return ReportSummary(service_id, len(severe), len(inaccurate))


def report_trigger(summary: ReportSummary, config: WorkerConfig) -> ReportTrigger | None:
    """Which report rule a record meets, if any. Severe wins when both are met."""
    if summary.severe_reporters >= config.severe_report_threshold:
        return ReportTrigger.SEVERE
    if summary.inaccuracy_reporters >= config.inaccuracy_report_threshold:
        return ReportTrigger.INACCURACY
    return None


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
    flags: list[ReviewFlag] = field(default_factory=list)
    skipped_unchanged: bool = False  # no model call: page and record unchanged since review
    reports: dict[str, Any] = field(default_factory=dict)  # the report counts that applied
    model_called: bool = False  # fact extraction was attempted
    # The last completed AI review, carried from check to check until a new one completes.
    # A failed fetch or a failed model call keeps them, so a passing outage doesn't cost a
    # later skip. A skip is only allowed when both hashes match them.
    content_hash: str | None = None  # of the reviewed page's visible text
    record_hash: str | None = None  # of the record as it was reviewed
    reviewed_category: Category | None = None  # the review's category, carried by a skip
    reviewed_at: str | None = None  # when that review ran
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


def page_hash(content: str) -> str:
    """Hash of a page's visible text with whitespace collapsed, so layout-only edits match.

    Markup, scripts and comments don't count; a dynamic page (a date, a counter) just means
    an extra model call, never a wrong skip.
    """
    text = " ".join(visible_text(content).split())
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def record_hash(record: dict[str, Any]) -> str:
    """Hash of the whole stored record, the review version and the configured model.

    Any change to the record (such as a person applying a finding), to ``REVIEW_VERSION``
    or to ``BEDROCK_MODEL_ID`` means the record is reviewed again rather than carried
    forward. Values JSON can't hold are hashed by their text, which at worst costs an
    extra review.
    """
    canonical = json.dumps(record, sort_keys=True, default=str, ensure_ascii=False)
    model = os.environ.get(MODEL_ID_ENV, "").strip()
    return hashlib.sha256(f"{REVIEW_VERSION}\n{model}\n{canonical}".encode()).hexdigest()


def _new_entry(
    service_id: str, checked_at: str, url: str | None, previous: CheckLogEntry | None
) -> CheckLogEntry:
    """A log entry that carries the last completed review forward from ``previous``."""
    entry = CheckLogEntry(
        service_id=service_id, checked_at=checked_at, category=None, url=url, fetch_failed=False
    )
    if previous is not None:
        entry.content_hash = previous.content_hash
        entry.record_hash = previous.record_hash
        entry.reviewed_category = previous.reviewed_category
        entry.reviewed_at = previous.reviewed_at
    return entry


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
    model_calls: int = 0  # fact extractions attempted
    skipped_unchanged: int = 0  # checks that carried the last review forward, no model call
    flags: Counter[str] = field(default_factory=Counter)  # review flags raised
    triggered: Counter[str] = field(default_factory=Counter)  # checks run under a report rule
    below_threshold: int = 0  # reported records whose reports don't meet a rule yet
    cooling_down: list[str] = field(default_factory=list)  # reported, but checked too recently
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
    trigger: ReportTrigger | None = None,
) -> tuple[Comparison | None, CheckLogEntry]:
    """Check one record: fetch, decide whether it needs an AI review, extract, compare.

    Returns the comparison, or None when the page and record are unchanged since the last
    completed review: then nothing new is found, and ``entry.category`` carries that
    review's result forward. Severe reports always get a fresh review; inaccuracy reports
    on an unchanged page are skipped with a flag for a person. Raises only for setup errors.
    """
    site, note = _fetch(website_source, url)
    entry = _new_entry(record["id"], checked_at, url, previous)
    entry.fetch_failed = not site_loaded(site)
    entry.note = note
    readable = not entry.fetch_failed and bool(visible_text(site.content or "").strip())

    if readable:
        page = page_hash(site.content or "")
        stored = record_hash(record)
        unchanged = (
            entry.content_hash is not None
            and entry.content_hash == page
            and entry.record_hash == stored
            and entry.reviewed_category is not None
        )
        if unchanged and trigger is not ReportTrigger.SEVERE:
            if trigger is ReportTrigger.INACCURACY:
                # People say it's wrong, but the page says what it said at the last review,
                # so another AI review would find the same. A person has to look.
                entry.flags.append(ReviewFlag.REPORTS_ON_UNCHANGED_PAGE)
            entry.skipped_unchanged = True
            entry.category = entry.reviewed_category
            entry.note = f"page and record unchanged since the review at {entry.reviewed_at}"
            return None, entry

    extraction = None
    if readable:
        entry.model_called = True
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
    if extraction is not None and comparison.category is not Category.CHECK_FAILED:
        # A completed review: later checks can carry it forward while nothing changes.
        entry.content_hash = page
        entry.record_hash = stored
        entry.reviewed_category = comparison.category
        entry.reviewed_at = checked_at
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
    report_source: ReportSource | None = None,
) -> RunSummary:
    """Check every record that is due, save the findings, and log each check.

    Detection only proposes: it saves findings through ``sink`` and never changes a record.
    ``invoke`` is the model call (Bedrock by default). ``limit`` caps the checks in one run;
    the rest stay due for the next run.

    ``config.mode`` picks the records: ``sweep`` checks those due by age, ``reports`` those
    whose user reports meet a rule. Either way, reports since a record's last check decide
    how it is checked (see the module docstring). ``report_source`` defaults to no reports.

    Raises ``WorkerSetupError`` when every record would fail the same way. A problem with
    one record (a page that won't load, an unusable model answer, a bug hit by one odd
    record) becomes a ``check_failed`` finding for that record, and the run continues.
    """
    config = config or WorkerConfig()
    reports = report_source or NoReports()
    if limit is not None and limit < 0:
        raise ValueError("limit can't be negative")

    summary = RunSummary()
    started = _aware(now())
    if config.mode == "reports":
        due, triggers = reported_records(
            reports, check_log, now=started, config=config, summary=summary
        )
    else:
        due = due_records(
            store,
            check_log,
            now=started,
            interval_days=config.check_interval_days,
            summary=summary,
        )
        triggers = {}
    if limit is not None:
        due = due[:limit]
    summary.due = len(due)

    for service_id in due:
        record = store.get_service(service_id)
        if record is None:
            summary.not_found.append(service_id)
            continue
        checked_at = iso(_aware(now()))
        if config.mode == "reports":
            trigger, counts = triggers[service_id]
        else:
            trigger, counts = _reports_for(
                reports, check_log, service_id, now=started, config=config, summary=summary
            )
        if trigger is not None:
            summary.triggered[trigger.value] += 1
        url = record.get("url").strip() if isinstance(record.get("url"), str) else ""
        if not url:
            # A finding needs a url for its evidence, so a record with no website can't get
            # one. It is logged and reported instead, never skipped silently.
            summary.no_website.append(service_id)
            entry = _new_entry(service_id, checked_at, None, check_log.last(service_id))
            entry.note = "the record has no website url to check"
            _apply_reports(entry, trigger, counts, summary)
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
                trigger=trigger,
            )
        except Exception as exc:
            if _is_setup_error(exc):
                summary.stopped_at = service_id
                raise WorkerSetupError(f"stopped the run: {exc}", summary) from exc
            # One record broke the check. Report it as a failed check and carry on; the
            # error type (not the message, which may quote the page) goes on the finding.
            summary.errors.append(f"{service_id}: {type(exc).__name__}")
            entry = _new_entry(service_id, checked_at, url, previous)
            entry.note = f"the check errored: {type(exc).__name__}: {exc}"
            comparison = failure(
                record,
                Category.CHECK_FAILED,
                f"the check errored ({type(exc).__name__})",
                url=url,
                checked_at=checked_at,
            )

        _apply_reports(entry, trigger, counts, summary)
        if entry.model_called:
            summary.model_calls += 1
        if comparison is None:  # unchanged since the last review: carried forward
            _persist(check_log.record, entry, summary=summary, service_id=service_id)
            summary.checked += 1
            summary.skipped_unchanged += 1
            summary.categories[entry.category.value] += 1
            continue

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
        if entry.rejected_findings and entry.reviewed_at == checked_at:
            # The review found something that couldn't be stored. Don't let a later check
            # carry it forward: keep the previous review so this one is redone next time.
            carried = _new_entry(service_id, checked_at, url, previous)
            entry.content_hash, entry.record_hash = carried.content_hash, carried.record_hash
            entry.reviewed_category = carried.reviewed_category
            entry.reviewed_at = carried.reviewed_at
        entry.category = comparison.category
        _persist(check_log.record, entry, summary=summary, service_id=service_id)
        summary.checked += 1
        summary.categories[comparison.category.value] += 1
    return summary


def _readable_or_none(entry: CheckLogEntry | None) -> datetime | None:
    return _readable_time(entry.checked_at) if entry is not None else None


def _reports_since(last: datetime | None, *, now: datetime, config: WorkerConfig) -> datetime:
    """Reports count after the last check, and never from before the window."""
    window_start = now - timedelta(days=config.report_window_days)
    return max(last, window_start) if last is not None else window_start


def _reports_for(
    reports: ReportSource,
    check_log: CheckLog,
    service_id: str,
    *,
    now: datetime,
    config: WorkerConfig,
    summary: RunSummary,
) -> tuple[ReportTrigger | None, dict[str, Any]]:
    """The report rule a record meets now, and the counts behind it."""
    since = iso(
        _reports_since(_readable_or_none(check_log.last(service_id)), now=now, config=config)
    )
    counts = _ask(reports.summary, service_id, since, summary=summary, service_id=service_id)
    trigger = report_trigger(counts, config)
    return trigger, {
        "since": since,
        "severe_reporters": counts.severe_reporters,
        "inaccuracy_reporters": counts.inaccuracy_reporters,
        "trigger": trigger.value if trigger else None,
    }


def reported_records(
    reports: ReportSource,
    check_log: CheckLog,
    *,
    now: datetime,
    config: WorkerConfig,
    summary: RunSummary,
) -> tuple[list[str], dict[str, tuple[ReportTrigger, dict[str, Any]]]]:
    """Records whose reports meet a rule and that are past the cooldown: severe first."""
    window_start = iso(now - timedelta(days=config.report_window_days))
    candidates = _ask(reports.services_with_reports, window_start, summary=summary)
    cooldown = timedelta(days=config.report_cooldown_days)
    severe: list[str] = []
    inaccuracy: list[str] = []
    triggers: dict[str, tuple[ReportTrigger, dict[str, Any]]] = {}
    for service_id in dict.fromkeys(c for c in candidates if isinstance(c, str) and c.strip()):
        last = _readable_or_none(check_log.last(service_id))
        if last is not None and now - last < cooldown:
            summary.cooling_down.append(service_id)
            continue
        trigger, counts = _reports_for(
            reports, check_log, service_id, now=now, config=config, summary=summary
        )
        if trigger is None:
            summary.below_threshold += 1
            continue
        triggers[service_id] = (trigger, counts)
        (severe if trigger is ReportTrigger.SEVERE else inaccuracy).append(service_id)
    return severe + inaccuracy, triggers


def _ask(
    read: Callable[..., Any], *args: Any, summary: RunSummary, service_id: str | None = None
) -> Any:
    """Read the report source. If it is down every record is affected, so stop the run."""
    try:
        return read(*args)
    except Exception as exc:
        summary.stopped_at = service_id  # the record being counted, if any
        raise WorkerSetupError(
            f"stopped the run: could not read user reports ({type(exc).__name__}: {exc})",
            summary,
        ) from exc


def _apply_reports(
    entry: CheckLogEntry,
    trigger: ReportTrigger | None,
    counts: dict[str, Any],
    summary: RunSummary,
) -> None:
    """Record the report counts on the entry and raise the flags they call for."""
    if counts and (counts["severe_reporters"] or counts["inaccuracy_reporters"]):
        entry.reports = dict(counts)
    if trigger is ReportTrigger.SEVERE and ReviewFlag.LEGITIMACY_REVIEW not in entry.flags:
        entry.flags.append(ReviewFlag.LEGITIMACY_REVIEW)  # whatever the check itself found
    for flag in entry.flags:
        summary.flags[flag.value] += 1


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
