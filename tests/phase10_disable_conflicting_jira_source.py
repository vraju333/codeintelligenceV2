"""Safely disable legacy source 1 only if its configuration matches the known CAS conflict.
Default dry-run; --apply commits. Does not delete Jira records or checkpoints.
"""
import argparse
import json
from database import SessionLocal
from db_models import KnowledgeSyncSource


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    db = SessionLocal()
    try:
        legacy = db.query(KnowledgeSyncSource).filter(KnowledgeSyncSource.id == 1).first()
        current = db.query(KnowledgeSyncSource).filter(KnowledgeSyncSource.id == 5).first()
        if not legacy or not current:
            raise RuntimeError('Expected source IDs 1 and 5 not found. No changes made.')
        old_cfg = json.loads(legacy.config_json or '{}')
        new_cfg = json.loads(current.config_json or '{}')
        old_scope = str(legacy.project_path or '').replace('\\', '/').rstrip('/').casefold()
        new_scope = str(current.project_path or '').replace('\\', '/').rstrip('/').casefold()
        if not (legacy.source_type == current.source_type == 'JIRA'
                and old_scope.endswith('/student-employee-project')
                and new_scope.endswith('/customer-account-service')
                and 'project = CAS' in str(old_cfg.get('jql') or '').upper()
                and str(new_cfg.get('project_key') or '').upper() == 'CAS'):
            # Match case-insensitively, including the JQL.
            if not (legacy.source_type == current.source_type == 'JIRA'
                    and old_scope.endswith('/student-employee-project')
                    and new_scope.endswith('/customer-account-service')
                    and 'PROJECT = CAS' in str(old_cfg.get('jql') or '').upper()
                    and str(new_cfg.get('project_key') or '').upper() == 'CAS'):
                raise RuntimeError('Sources do not match expected conflict. No changes made.')
        print('Legacy source:', legacy.id, legacy.name, legacy.project_path, old_cfg)
        print('Current source:', current.id, current.name, current.project_path, new_cfg)
        if args.apply:
            legacy.enabled = 'false'
            db.commit()
            print('DISABLED source 1. Source 5 remains unchanged. No indexed data deleted.')
        else:
            print('DRY RUN ONLY. Re-run with --apply to disable legacy source 1.')
    finally:
        db.close()

if __name__ == '__main__':
    main()
