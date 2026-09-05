import { getTranslations } from "next-intl/server";
import { apiUrl } from "@/lib/api";

/**
 * MCP connection details, formatted for common clients (PRD §F3).
 *
 * The two paragraphs above the config block are the part that earns its place:
 * an operator wiring an assistant into a data catalog should know, before they
 * do it, that the server cannot invent a dataset and does not return data.
 * Both are properties of the server, not promises — which is why they can be
 * stated here as facts.
 */
// Rendered per request, not prerendered: this page reads the API URL from the
// environment, and a statically generated copy would bake in whatever the
// build machine had — the same trap the `env` config option sets.
export const dynamic = "force-dynamic";

export default async function ConnectPage() {
  const t = await getTranslations("connect");
  const url = `${apiUrl()}/mcp`;

  const tools = [
    ["search_datasets", "Search the catalog"],
    ["get_dataset", "One record in full"],
    ["get_dataset_schema", "Fields, units, concepts — and the gaps, with reasons"],
    ["explain_connection", "Why two datasets are linked, including correlation warnings"],
    ["preview_dataset", "The dataset's shape. Not its data"],
    ["get_access_plan", "Where the data is and how to read it, under your identity"],
    ["author_workflow", "An inert specification. Nothing executes. Requires tier 1"],
  ];

  const config = JSON.stringify(
    {
      mcpServers: {
        "opengrid-datahub": {
          command: "datahub-mcp",
          env: { DATAHUB_API_URL: apiUrl() },
        },
      },
    },
    null,
    2,
  );

  return (
    <div className="max-w-3xl space-y-8">
      <header>
        <h1 className="text-2xl font-semibold tracking-tight">{t("title")}</h1>
        <p className="mt-2 text-[color:var(--muted)]">{t("subtitle")}</p>
      </header>

      <section className="space-y-2">
        <h2 className="font-medium">{t("url")}</h2>
        <code
          className="block overflow-x-auto rounded border p-3 text-sm"
          style={{ borderColor: "var(--border)", background: "var(--surface)" }}
        >
          {url}
        </code>
      </section>

      <section className="space-y-2">
        <h2 className="font-medium">Configuration</h2>
        <pre
          className="overflow-x-auto rounded border p-3 text-xs"
          style={{ borderColor: "var(--border)", background: "var(--surface)" }}
        >
          {config}
        </pre>
      </section>

      <section className="space-y-3">
        <h2 className="font-medium">{t("tools")}</h2>
        <ul className="space-y-1.5 text-sm">
          {tools.map(([name, description]) => (
            <li key={name}>
              <code className="text-xs">{name}</code>
              <span className="text-[color:var(--muted)]"> — {description}</span>
            </li>
          ))}
        </ul>
      </section>

      <section
        className="space-y-3 rounded-lg border p-4 text-sm"
        style={{ borderColor: "var(--border)", background: "var(--surface)" }}
      >
        <p>{t("grounding")}</p>
        <p>{t("noData")}</p>
      </section>
    </div>
  );
}
