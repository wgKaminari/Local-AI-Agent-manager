"""Shared guards for tests that run a collection.

Market-wide job sites are enabled by default for real users. Tests must never
reach them, nor NAV's live feed, unless a test replaces those calls itself.
"""

import unittest
from unittest.mock import patch

from norway_job_agent import nav_index
from norway_job_agent.market import PROVIDERS
from norway_job_agent.sources import SourceError

# The real feed reader, for tests that drive it against a mocked feed.
REAL_SYNC = nav_index.sync


def offline_collection():
    """Start module-wide patches; call from setUpModule."""
    patchers = [
        patch.dict("norway_job_agent.market.DEFAULT_ENABLED", {name: False for name in PROVIDERS}),
        patch("norway_job_agent.nav_index.sync", side_effect=SourceError("NAV is offline in tests.")),
    ]
    for patcher in patchers:
        patcher.start()
        unittest.addModuleCleanup(patcher.stop)


def nav_fetch(jobs=(), withdrawn=(), *, active=None, has_more=False, remaining=0, marks=()):
    """A _fetch_nav result in the shape the NAV index collector returns."""
    return {"sync": {"pages": 1, "events": len(jobs), "has_more": has_more, "experimental_token": True,
                     "active": len(jobs) if active is None else active, "inactive": len(withdrawn)},
            "candidates": len(jobs), "jobs": list(jobs), "withdrawn": [], "remaining": remaining,
            "marks": list(marks), "pending_withdrawals": list(withdrawn)}
