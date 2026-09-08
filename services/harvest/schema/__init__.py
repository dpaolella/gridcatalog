"""Reading a dataset's own description of its shape (WP-11.4).

The catalog held 24 field descriptions across 66 records. ERA5 alone publishes
273 variables, each with a long name and a unit, in one 130 KB request to its
own store. The gap was never that the metadata does not exist — it was that no
stage of the pipeline ever looked at the dataset, only at catalog records
*about* datasets.

See :mod:`datahub.harvest.schema.prober` for the four rules this stage holds,
and :mod:`datahub.harvest.schema.surfaces` for what each format states.
"""

from datahub.harvest.schema.prober import (
    MAX_BYTES,
    ProbeOutcome,
    SchemaProber,
    Surface,
    apply,
    merge_fields,
    surfaces_for,
)
from datahub.harvest.schema.surfaces import ProbedField
from datahub.harvest.schema.uris import is_object_store, to_http

__all__ = [
    "MAX_BYTES",
    "ProbeOutcome",
    "ProbedField",
    "SchemaProber",
    "Surface",
    "apply",
    "is_object_store",
    "merge_fields",
    "surfaces_for",
    "to_http",
]
