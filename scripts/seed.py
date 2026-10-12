"""Load the sample registry, deployment history and metrics through the real services.

    python scripts/seed.py --tenant acme

Uses the service layer (not raw SQL) so seeding exercises the same validation and audit path
as the API. Requires the schema to exist (`alembic upgrade head`).
"""

import argparse
import csv
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from sqlalchemy import select  # noqa: E402

from app.bootstrap import build_container  # noqa: E402
from app.db import utcnow  # noqa: E402
from app.domain.enums import Role  # noqa: E402
from app.domain.rbac import Principal  # noqa: E402
from app.orm import Model, ModelVersion  # noqa: E402
from app.services.metrics_service import MetricsService  # noqa: E402
from app.services.registry_service import RegistryService  # noqa: E402

ARTIFACTS = ROOT / "artifacts"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tenant", default="acme")
    args = parser.parse_args()

    c = build_container()
    admin = Principal("seed-admin", Role.ADMIN, args.tenant)
    approver = Principal("seed-approver", Role.APPROVER, args.tenant)
    registry = json.loads((ARTIFACTS / "sample_model_registry.json").read_text())

    with c.session_factory() as s:
        svc = RegistryService(s, c.settings, c.runtimes)
        for m in registry:
            if s.get(Model, (args.tenant, m["model_id"])):
                continue
            svc.create_model(admin, model_id=m["model_id"], name=m["name"], owner=m["owner"],
                             tags=[m["framework"]], metadata_={})  # fmt: skip
            for v in m["versions"]:
                mv = svc.register_version(
                    admin, m["model_id"], version=v["version"], runtime=None,
                    framework=m["framework"], algorithm=None, artifact_uri=v["artifact_uri"],
                    training_data_ref=f"s3://datasets/{m['model_id']}/train", tags=[], metadata_={},
                )  # fmt: skip
                if v["approved"] or v["stage"] == "VALIDATED":
                    svc.validate(admin, m["model_id"], v["version"])
                if v["approved"]:
                    svc.approve(approver, m["model_id"], v["version"], "seed data")
                row = s.scalar(select(ModelVersion).where(ModelVersion.id == mv.id))
                if row is not None and v["stage"] in ("PRODUCTION", "ARCHIVED", "STAGING"):
                    row.stage = v["stage"]  # historical state predates this platform
                    row.updated_at = utcnow()
                    s.commit()

        rows = [
            {
                "model_id": r["model_id"], "version": r["version"], "environment": r["environment"],
                "ts": datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00")),
                **{k: float(r[k]) for k in ("latency_ms", "throughput_rpm", "error_rate",
                                             "quality_score", "drift_score", "availability")},
            }
            for r in csv.DictReader((ARTIFACTS / "sample_model_metrics.csv").open())
        ]  # fmt: skip
        known = {m["model_id"] for m in registry}
        metrics = MetricsService(s, c.settings)
        for model_id in sorted({r["model_id"] for r in rows} & known):
            batch = [{k: v for k, v in r.items() if k != "model_id"} for r in rows if r["model_id"] == model_id]
            print(f"{model_id}: inserted {metrics.ingest(admin, model_id, batch)} metric points")  # noqa: T201
        skipped = {r["model_id"] for r in rows} - known
        if skipped:
            print(f"skipped metrics for unregistered models: {sorted(skipped)}")  # noqa: T201


if __name__ == "__main__":
    main()
