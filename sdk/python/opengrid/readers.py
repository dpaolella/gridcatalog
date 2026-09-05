"""Executing an access plan (WP-10.1).

PRD §F9: *lazy xarray and pandas readers that consume access plans.* And PRD
§F8: *the Hub is a control plane and never returns data.* Put together, those
two say the reading happens **here**, in the caller's process, against the
source the plan names.

That is what keeps a slice cheap. A 4 TB Zarr sliced to one month is a few
megabytes because xarray reads the chunks the slice touches, directly from
object storage. Route the same request through the Hub and it becomes 4 TB
across somebody's egress bill, twice.

**Every reader is imported lazily, and a missing one names its package.** The
SDK's base install has no xarray and no pandas: a user who only wants to search
the catalog should not install a numerical stack, and a user who wants to read a
Zarr will be told which package to add rather than seeing an ImportError from
three frames down.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from opengrid.errors import AccessPlanUnusable
from opengrid.models import AccessPlan

#: Format token → the reader that handles it. Longest match first at lookup, so
#: ``geoparquet`` does not resolve to the ``parquet`` reader — the two need
#: different libraries and the wrong one loses the geometry column.
READERS: dict[str, str] = {
    "zarr": "_read_zarr",
    "netcdf": "_read_netcdf",
    "hdf5": "_read_netcdf",
    "geoparquet": "_read_geoparquet",
    "parquet": "_read_parquet",
    "geopackage": "_read_geo",
    "shapefile": "_read_geo",
    "geojson": "_read_geo",
    "cog": "_read_raster",
    "geotiff": "_read_raster",
    "csv": "_read_csv",
    "tsv": "_read_csv",
    "json": "_read_json",
}


def execute(plan: AccessPlan, **kwargs: Any) -> Any:
    """Read the data the plan points at, in this process.

    Raises :class:`AccessPlanUnusable` rather than guessing when the format is
    unknown. A reader that fell back to "try pandas" would hand a user a
    DataFrame of gibberish for a Zarr store, which is worse than an error,
    because they would plot it.
    """
    reader_name = _reader_for(plan)
    if reader_name is None:
        raise AccessPlanUnusable(
            f"no reader for format {plan.format!r} (mode {plan.mode}). The plan is valid; this "
            f"package does not know how to read it. Its location is {plan.location} and its "
            "read instructions are in plan.read_instructions."
        )
    reader: Callable[..., Any] = globals()[reader_name]
    return reader(plan, **kwargs)


def _reader_for(plan: AccessPlan) -> str | None:
    haystack = " ".join(
        str(part).lower()
        for part in (plan.format, plan.read_instructions.get("format"), plan.location)
        if part
    )
    for token in sorted(READERS, key=len, reverse=True):
        if token in haystack:
            return READERS[token]
    return None


# ---------------------------------------------------------------------------
# Readers
# ---------------------------------------------------------------------------


def _read_zarr(plan: AccessPlan, **kwargs: Any) -> Any:
    """Lazy by construction: ``open_zarr`` reads metadata, not chunks.

    The slice in the plan is applied *after* opening and still transfers only
    what it touches, because nothing has been read until something is computed.
    """
    xr = _require("xarray", "zarr")
    storage = _storage_options(plan)
    dataset = xr.open_zarr(plan.location, storage_options=storage or None, **kwargs)
    return _apply_slice(dataset, plan)


def _read_netcdf(plan: AccessPlan, **kwargs: Any) -> Any:
    xr = _require("xarray", "netCDF4")
    dataset = xr.open_dataset(plan.location, **kwargs)
    return _apply_slice(dataset, plan)


def _read_parquet(plan: AccessPlan, **kwargs: Any) -> Any:
    pd = _require("pandas", "pyarrow")
    return pd.read_parquet(plan.location, **kwargs)


def _read_geoparquet(plan: AccessPlan, **kwargs: Any) -> Any:
    gpd = _require("geopandas")
    return gpd.read_parquet(plan.location, **kwargs)


def _read_geo(plan: AccessPlan, **kwargs: Any) -> Any:
    gpd = _require("geopandas")
    frame = gpd.read_file(plan.location, **kwargs)
    box = plan.requested_slice.get("bbox")
    return frame.cx[box[0] : box[2], box[1] : box[3]] if box else frame


def _read_raster(plan: AccessPlan, **kwargs: Any) -> Any:
    rioxarray = _require("rioxarray")
    array = rioxarray.open_rasterio(plan.location, **kwargs)
    box = plan.requested_slice.get("bbox")
    return array.rio.clip_box(*box) if box else array


def _read_csv(plan: AccessPlan, **kwargs: Any) -> Any:
    pd = _require("pandas")
    return pd.read_csv(plan.location, **kwargs)


def _read_json(plan: AccessPlan, **kwargs: Any) -> Any:
    pd = _require("pandas")
    return pd.read_json(plan.location, **kwargs)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _apply_slice(dataset: Any, plan: AccessPlan) -> Any:
    """Narrow an xarray object to the slice the plan carries.

    Applied here rather than assumed to have been applied upstream: the plan
    *states* the slice, and whether it was pushed down to the source depends on
    the format. Applying it again is cheap and idempotent; not applying it
    returns more data than the caller asked for, silently.
    """
    wanted = plan.requested_slice
    if not wanted:
        return dataset

    variables = wanted.get("variables")
    if variables:
        present = [v for v in variables if v in getattr(dataset, "variables", {})]
        if present:
            dataset = dataset[present]

    time = wanted.get("time")
    if time and "time" in getattr(dataset, "dims", {}):
        dataset = dataset.sel(time=slice(time.get("start"), time.get("end")))

    box = wanted.get("bbox")
    if box and {"latitude", "longitude"} <= set(getattr(dataset, "dims", {})):
        dataset = dataset.sel(
            longitude=slice(box[0], box[2]),
            latitude=slice(box[3], box[1]),  # descending, as reanalyses store it
        )
    return dataset


def _storage_options(plan: AccessPlan) -> dict[str, Any]:
    options = dict(plan.read_instructions.get("storage_options") or {})
    if plan.location.startswith("s3://") and "anon" not in options:
        # Anonymous unless the plan says otherwise. A plan for a public bucket
        # that picked up a developer's ambient AWS credentials would work on
        # their machine and fail in CI, and the failure would look like a
        # catalog bug.
        options["anon"] = not plan.read_instructions.get("credentials_required", False)
    return options


def _require(package: str, *extras: str) -> Any:
    """Import a reader, or explain what to install.

    "Unusable" with no remedy is a dead end, and the remedy here is one
    ``pip install``.
    """
    import importlib

    try:
        return importlib.import_module(package)
    except ImportError as exc:
        wanted = " ".join((package, *extras))
        raise AccessPlanUnusable(
            f"reading this plan needs {package}, which is not installed. "
            f"Install it with: pip install {wanted}"
        ) from exc


__all__ = ["READERS", "execute"]
