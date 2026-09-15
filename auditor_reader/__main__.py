"""Print a reviewed table fingerprint using the dedicated reader identity."""
import argparse
import json

from .policy import InspectionError, load_policy
from .postgres import profile_sql
from .service import reader_config


def main():
    parser = argparse.ArgumentParser(description="Read the safety-checked fingerprint of one approved dataset. This command saves nothing.")
    parser.add_argument("dataset_id")
    args = parser.parse_args()
    try:
        dataset = load_policy().dataset(args.dataset_id)
        result = profile_sql(dataset, str(reader_config().get("reader_dsn") or ""), b"unused", lambda: False, discover=True)
        print(json.dumps({"dataset_id": dataset.id, **result}))
        return 0
    except InspectionError as exc:
        print(json.dumps({"error": str(exc)}))
    except Exception:
        print(json.dumps({"error": "reader_inspection_unavailable"}))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
