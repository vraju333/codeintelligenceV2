"""
CodeIntelligence clean reset utility for V2.

Run from the CodeIntelligence project root:
    python reset_codeintelligence.py

What it removes:
- scenarios
- saved JIRA knowledge
- scenario baselines + source snapshots
- scenario test baselines
- operation baselines + source snapshots
- persisted source/JIRA/scenario FAISS indexes

What it keeps:
- source code
- project registry/configuration
- database schema
- .env / LLM configuration

Safety:
- requires DATABASE_MODE=postgres
- asks for an explicit RESET confirmation
"""

from pathlib import Path
import shutil
import sys

from sqlalchemy import text

# Import ORM models before using the database so metadata/model mappings are loaded.
from db_models import Scenario, JiraKnowledge
from baseline_models import (
    ScenarioBaseline,
    ScenarioBaselineSourceSnapshot,
    ScenarioTestBaseline,
    OperationBaseline,
    OperationBaselineSourceSnapshot,
)
from config import settings
from database import SessionLocal, PRIMARY_DATABASE_URL


INDEX_DIRS = (
    Path("rag_indexes"),
    Path("jira_rag_index"),
    Path("scenario_rag_indexes"),
)


def remove_index_contents(path: Path) -> None:
    if not path.exists():
        return

    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()

    print(f"  cleared: {path}")


def reset_database() -> None:
    db = SessionLocal()
    try:
        # Delete children before parents to satisfy foreign keys.
        deletions = (
            ("scenario_test_baselines", ScenarioTestBaseline),
            ("scenario_baseline_source_snapshots", ScenarioBaselineSourceSnapshot),
            ("operation_baseline_source_snapshots", OperationBaselineSourceSnapshot),
            ("scenario_baselines", ScenarioBaseline),
            ("operation_baselines", OperationBaseline),
            ("jira_knowledge", JiraKnowledge),
            ("scenarios", Scenario),
        )

        print("\nDatabase cleanup:")
        for label, model in deletions:
            count = db.query(model).count()
            db.query(model).delete(synchronize_session=False)
            print(f"  {label}: deleted {count}")

        db.commit()

        # Reset PostgreSQL identity/sequence values where applicable.
        # This is optional for correctness, but makes a fresh test easier to read.
        table_names = [
            "scenario_test_baselines",
            "scenario_baseline_source_snapshots",
            "operation_baseline_source_snapshots",
            "scenario_baselines",
            "operation_baselines",
            "jira_knowledge",
            "scenarios",
        ]
        for table in table_names:
            try:
                db.execute(
                    text(
                        "SELECT setval("
                        "pg_get_serial_sequence(:table_name, 'id'), "
                        "1, false)"
                    ),
                    {"table_name": table},
                )
            except Exception:
                db.rollback()
                # Sequence reset is cosmetic; deleted rows are already committed.
                break
        else:
            db.commit()

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def main() -> int:
    mode = settings.DATABASE_MODE.strip().lower()

    print("CodeIntelligence V2 - Fresh Test Reset")
    print("--------------------------------------")
    print(f"DATABASE_MODE: {mode}")
    print(f"Primary DB: {PRIMARY_DATABASE_URL.split('://', 1)[0]}")

    if mode != "postgres":
        print(
            "\nSTOP: This reset is intentionally restricted to "
            "DATABASE_MODE=postgres.\n"
            "Update .env first, restart/verify PostgreSQL, then run again."
        )
        return 2

    answer = input(
        "\nThis will DELETE CodeIntelligence test/history data and RAG indexes.\n"
        "Type RESET to continue: "
    ).strip()

    if answer != "RESET":
        print("Cancelled. Nothing was changed.")
        return 0

    reset_database()

    print("\nRAG cleanup:")
    for path in INDEX_DIRS:
        remove_index_contents(path)

    print(
        "\nRESET COMPLETE.\n"
        "Important: disable automatic scenario seeding before starting the app,\n"
        "otherwise seed_scenarios() will recreate the demo scenarios."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
