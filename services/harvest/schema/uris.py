"""Object-store URIs to something HTTP can read.

``broker/prober.py`` documents, correctly, that HTTP cannot reach an ``s3://``
URI and that recording one as unreachable would exclude working data from
access plans. The same is true of a schema probe, and it is the reason ERA5 —
whose Zarr distribution is ``s3://era5-pds/zarr/`` — had no readable schema
surface at all despite publishing 273 documented variables.

So this maps the three object-store schemes onto their public HTTPS endpoints.
It is a translation, not a claim: a bucket that is not public still refuses the
request, and the probe treats that as "no schema surface here" rather than as
an error, exactly as it treats a 404.

**The mapping is not universal and does not pretend to be.** A bucket with dots
in its name cannot use S3 virtual-host addressing, a bucket outside us-east-1
may need its region in the host, and a private bucket needs credentials this
process does not have. Each of those yields a URL that simply does not resolve,
which is a case the caller already handles.
"""

from __future__ import annotations

from urllib.parse import urlsplit

#: Buckets whose names contain a dot break virtual-host style addressing —
#: the TLS certificate for `*.s3.amazonaws.com` does not match
#: `my.bucket.s3.amazonaws.com` — so those take path style instead.
_S3_VIRTUAL_HOST_SAFE = str.maketrans("", "", "")


def to_http(uri: str) -> str | None:
    """An ``s3://``, ``gs://`` or ``az://`` URI as an HTTPS URL, or None.

    None means "this is not an object-store URI", not "this failed". An
    ``https://`` URL is returned unchanged, so a caller can pass every
    distribution through without asking what kind it holds.
    """
    text = (uri or "").strip()
    if not text:
        return None
    if text.startswith(("http://", "https://")):
        return text

    parts = urlsplit(text)
    bucket, key = parts.netloc, parts.path.lstrip("/")
    if not bucket:
        return None

    if parts.scheme == "s3":
        # Path style where the bucket name would break the wildcard
        # certificate, virtual-host style otherwise.
        if "." in bucket:
            return f"https://s3.amazonaws.com/{bucket}/{key}"
        return f"https://{bucket}.s3.amazonaws.com/{key}"
    if parts.scheme == "gs":
        return f"https://storage.googleapis.com/{bucket}/{key}"
    if parts.scheme in ("az", "abfs", "abfss"):
        # `az://container@account.dfs.core.windows.net/key`, or the shorter
        # `az://container/key` where the account is not stated and cannot be
        # guessed — the second is not resolvable and says so.
        if "@" not in bucket:
            return None
        container, account = bucket.split("@", 1)
        host = account.replace(".dfs.", ".blob.")
        return f"https://{host}/{container}/{key}"
    return None


def is_object_store(uri: str) -> bool:
    """Whether *uri* names an object store rather than an HTTP endpoint."""
    return (uri or "").startswith(("s3://", "gs://", "az://", "abfs://", "abfss://"))


__all__ = ["is_object_store", "to_http"]
