/** Only a catalog path may be used as a return destination. Next's Link adds
 * basePath itself; keep the validated value relative to the application. */
export function catalogReturn(value: string | null | undefined): string {
  if (!value || /[\\\u0000-\u001f]/.test(value)) return "/datasets";
  try {
    const url = new URL(value, "https://catalog.invalid");
    if (!value.startsWith("/") || url.origin !== "https://catalog.invalid" ||
        !["/datasets", "/datasets/"].includes(url.pathname)) return "/datasets";
    return `/datasets${url.search}`;
  } catch {
    return "/datasets";
  }
}

export function catalogUrl(params: URLSearchParams): string {
  const query = params.toString();
  return query ? `/datasets?${query}` : "/datasets";
}

export function pageOffset(value: string | null): number {
  const number = Number(value);
  return Number.isSafeInteger(number) && number >= 0 ? number : 0;
}
