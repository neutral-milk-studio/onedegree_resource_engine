"""Maintenance worker: its configuration and entry point. The loop is in worker.service.

select-due -> fetch (website source) -> extract (Bedrock) -> compare -> finding.
Records last-checked time + result in the check_log ops table.
Spec: Requirements 18.1, 18.4, 19.5, 20, 21, 22, 23.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass, fields

from contracts.website_source import WebsiteSource

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


def run_checks(service, *, limit: int | None = None, force: bool = False) -> dict:
    """Run maintenance checks for records due, writing findings and the check results."""
    return service.run(limit=limit, force=force)


def main() -> None:
    """Entry point for the worker container."""
    try:
        config = WorkerConfig.from_env()
        website_source_for(config)  # reject bad settings at startup, before any check runs
    except ConfigError as error:
        raise SystemExit(f"invalid worker setting: {error}") from None
    if config.mode == "reports":
        raise SystemExit(f"{WorkerConfig.ENV['mode']}=reports is not implemented yet; use sweep")
    from api.app.container import build_container

    print(json.dumps(run_checks(build_container().maintenance)))


if __name__ == "__main__":  # pragma: no cover
    main()
