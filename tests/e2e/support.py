"""Fixtures and read-back assertions for a disposable local E2E database.

Invoked explicitly by the browser suite, never exposed as an API endpoint.
"""

import json
import sys
from datetime import timedelta

from datahub.api.models.base import session_scope
from datahub.api.models.repositories import Repositories


def main() -> None:
    command, *args = sys.argv[1:]
    with session_scope() as session:
        repos = Repositories(session)
        if command == "session":
            user = repos.users.upsert_federated("local", "e2e-member", email="e2e@example.org")
            # The request-time entitlement uses the same user as the grants
            # seeded before the API starts. Creating a fresh session per test
            # means signing out cannot invalidate a concurrent test.
            row = repos.sessions.open(user.id, ttl=timedelta(hours=1))
            print(json.dumps({"session": row.id}))
        elif command == "grant":
            user = repos.users.upsert_federated("local", "e2e-member", email="e2e@example.org")
            for slug in ("caiso-nodal-lmp-restricted", "utility-load-shapes-allowlisted"):
                repos.allowlist.grant(
                    f"https://catalog.opengrid.org/ds/{slug}",
                    granted_by="e2e-fixture",
                    principal_id=user.id,
                )
        elif command == "report":
            row = repos.reports.get(args[0])
            assert row is not None
            print(
                json.dumps(
                    {
                        "dataset_id": row.dataset_id,
                        "target_kind": row.target_kind,
                        "target_id": row.target_id,
                        "issue_type": row.issue_type,
                        "reporter_contact": row.reporter_contact,
                        "comment": row.comment,
                    }
                )
            )
        else:
            raise ValueError(command)


if __name__ == "__main__":
    main()
