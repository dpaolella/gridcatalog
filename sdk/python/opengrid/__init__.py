"""``opengrid`` — the OpenGrid Data Hub Python SDK (WP-10.1).

PRD §F9's target: *from zero to first dataset pull in one line.*

```python
from opengrid import DataHub

hub = DataHub()
ds = hub.search(domain="DD5", region="DE", concept="solar_irradiance")[0]
da = ds.open(time=slice("2019-01", "2019-12"), bbox=[5.9, 45.8, 10.5, 47.8])
```

``ds.open()`` fetches an access plan and **executes it in your process**. The
Hub is not in the path: it says where the data is and how to read it, and this
package does the reading. That is what keeps a catalog from becoming an egress
bill, and it is why a slice request transfers only the slice.

The SDK talks to the REST API and nothing else (architecture boundary table).
It holds no SPARQL, no store client, and no second copy of the entitlement
rules — a second copy would eventually disagree with the first, and the one
that disagreed would be the one the user was standing behind.
"""

from opengrid.client import DataHub
from opengrid.errors import AccessPlanUnusable, DataHubError, NotEntitled, NotFound
from opengrid.models import AccessPlan, Dataset, Distribution, Field, Link, ResultSet

__version__ = "1.0.0"

__all__ = [
    "AccessPlan",
    "AccessPlanUnusable",
    "DataHub",
    "DataHubError",
    "Dataset",
    "Distribution",
    "Field",
    "Link",
    "NotEntitled",
    "NotFound",
    "ResultSet",
    "__version__",
]
