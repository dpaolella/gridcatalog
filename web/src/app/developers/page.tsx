import { apiUrl } from "@/lib/api";
import { perRequest } from "@/lib/rendering";
import Link from "next/link";

// Rendered per request, not prerendered: this page reads the API URL from the
// environment, and a statically generated copy would bake in whatever the
// build machine had — the same trap the `env` config option sets.

export default async function DevelopersPage() {
  await perRequest();
  const api = apiUrl();
  return (
    <div className="max-w-3xl space-y-8">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">Developers</h1>
        <p className="mt-2 text-[color:var(--muted)]">
          One REST API. The web UI, the Python SDK and the MCP server all call it and none of them
          reaches past it into the store, so a rule enforced there is enforced for all three.
        </p>
      </header>

      <section className="space-y-2">
        <h2 className="font-medium">REST</h2>
        <ul className="space-y-1 text-sm">
          <li>
            <a href={`${api}/docs`} className="font-medium underline decoration-[color:var(--rule)] underline-offset-2 hover:decoration-2">
              Interactive documentation
            </a>
          </li>
          <li>
            <a href={`${api}/openapi.json`} className="font-medium underline decoration-[color:var(--rule)] underline-offset-2 hover:decoration-2">
              OpenAPI 3.1 document
            </a>{" "}
            <span className="text-[color:var(--muted)]">— generate a client from this</span>
          </li>
        </ul>
      </section>

      <section className="space-y-3">
        <h2 className="font-medium">Python</h2>
        <pre
          className="og-card overflow-x-auto p-4 font-mono text-xs">
{`pip install "opengrid-datahub[all]"

from opengrid import DataHub

hub = DataHub()
ds = hub.search(domain="DD5", region="DE", concept="solar_irradiance")[0]
da = ds.open(time=slice("2019-01", "2019-12"), bbox=[5.9, 45.8, 10.5, 47.8])`}
        </pre>
        <p className="text-sm text-[color:var(--muted)]">
          <code>ds.open()</code> fetches an access plan and executes it in your process. The Hub is
          not in the path, which is why slicing a 4 TB Zarr to one month moves a few megabytes.
        </p>
      </section>

      <section className="space-y-2">
        <h2 className="font-medium">Agents</h2>
        <p className="text-sm">
          <Link href="/connect" className="font-medium underline decoration-[color:var(--rule)] underline-offset-2 hover:decoration-2">
            MCP connection details
          </Link>{" "}
          <span className="text-[color:var(--muted)]">
            — seven tools, entitlement-scoped, payload-capped, and unable to invent a dataset.
          </span>
        </p>
      </section>

      <section
        className="og-card space-y-2 p-5 text-sm">
        <h2 className="font-medium">Three things that will surprise you once</h2>
        <ul className="space-y-1.5 text-[color:var(--muted)]">
          <li>
            <strong className="text-[color:var(--foreground)]">Nothing here returns data.</strong>{" "}
            <code>/download</code> is a 302 and <code>/access-plan</code> is a document.
          </li>
          <li>
            <strong className="text-[color:var(--foreground)]">
              A 404 for a record you may not see is identical to a 404 for one that does not exist.
            </strong>{" "}
            Deliberately: a distinguishable refusal is an existence oracle.
          </li>
          <li>
            <strong className="text-[color:var(--foreground)]">Absent means not captured.</strong>{" "}
            A missing licence field is not a dataset without a licence.
          </li>
        </ul>
      </section>
    </div>
  );
}
