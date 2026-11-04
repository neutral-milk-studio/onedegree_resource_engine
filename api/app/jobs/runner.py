"""Starts long jobs (import, maintenance, reindex). Local: background thread. AWS: ECS Fargate RunTask using the
same image (`python -m api.app.jobs.run <job>`), so the web tier stays small and cheap. EventBridge Scheduler uses the
same RunTask path for the recurring runs."""

from __future__ import annotations

import os
import threading


class JobRunner:
    def __init__(self, container):
        self.c = container

    def _do(self, kind: str, params: dict) -> dict:
        c = self.c
        if kind == "import":
            return {
                "results": c.ingest_all("manual", params.get("who", "admin"), params.get("source"))
            }
        if kind == "maintenance":
            return c.maintenance.run(
                scope=params.get("scope", "all"),
                service_id=params.get("service_id"),
                trigger="manual",
                initiated_by=params.get("who", "admin"),
                limit=params.get("limit"),
                force=bool(params.get("force")),
            )
        if kind == "reindex":
            return c.search.reindex()
        raise ValueError(f"unknown job {kind}")

    def start(self, kind: str, wait: bool = False, **params) -> dict:
        if wait:
            return {"started": True, "finished": True, **self._do(kind, params)}
        if os.environ.get("JOB_RUNNER") == "ecs":  # pragma: no cover - AWS
            import boto3

            ecs = boto3.client("ecs", region_name=self.c.settings.aws_region)
            cmd = ["python", "-m", "api.app.jobs.run", kind] + [
                f"--{k.replace('_', '-')}={v}" for k, v in params.items() if v not in (None, False)
            ]
            ecs.run_task(
                cluster=os.environ["ECS_CLUSTER"],
                taskDefinition=os.environ["JOB_TASK_DEFINITION"],
                launchType="FARGATE",
                networkConfiguration={
                    "awsvpcConfiguration": {
                        "subnets": os.environ["JOB_SUBNETS"].split(","),
                        "securityGroups": os.environ["JOB_SECURITY_GROUPS"].split(","),
                        "assignPublicIp": "ENABLED",
                    }
                },
                overrides={"containerOverrides": [{"name": "app", "command": cmd}]},
            )
            return {"started": True, "runner": "ecs"}
        threading.Thread(target=self._do, args=(kind, params), daemon=True).start()
        return {"started": True, "runner": "thread"}
