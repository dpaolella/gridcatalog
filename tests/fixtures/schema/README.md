# Recorded schema surfaces

Real payloads, captured once, so every extractor is testable with no network.
That matters more here than elsewhere: the build environment cannot reach most
of the sources these read, and an extractor tested only against a payload
somebody wrote by hand is tested against a guess about the format.

| File | Captured from | Why it is here |
|---|---|---|
| `era5-arco.zmetadata.json` | `https://storage.googleapis.com/gcp-public-data-arco-era5/ar/full_37-1h-0p25deg-chunk-1.zarr-v3/.zmetadata` | 273 real variables with CF `long_name` and `units`, plus the `z` short-name collision that `decollide` exists for |

Re-capture with a plain GET. Do not hand-edit: the point is that these are what
the sources actually serve, including the parts that are inconvenient.
