"""Loading curated fixtures into the store.

The Hub's records are authored, not crawled. A record arrives here because a
person wrote it down in ``data/`` and it passed validation — there is no
discovery step, no reconciliation against a remote catalog, and no adapter
fleet. What survives from the catalog's harvest pipeline is exactly the part
that was never about harvesting: a YAML inventory, a record shape, and a
loader that refuses anything unverified.

:mod:`datahub.fixtures.curated` reads the inventory; :mod:`datahub.fixtures.seed`
turns a row into a record and puts it in the store.
"""

from datahub.fixtures.base import HarvestedRecord, slugify

__all__ = ["HarvestedRecord", "slugify"]
