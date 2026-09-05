import createNextIntlPlugin from "next-intl/plugin";
import type { NextConfig } from "next";

const withNextIntl = createNextIntlPlugin("./src/i18n/request.ts");

const nextConfig: NextConfig = {
  // Deliberately no `env` block. Next inlines `env` values into the bundle at
  // *build* time, so `DATAHUB_API_URL` would be frozen to whatever the image
  // was built with — and the symptom is a production deployment quietly
  // calling localhost. The API URL is read from the process environment at
  // request time instead, in `src/lib/api.ts`.
  poweredByHeader: false,
};

export default withNextIntl(nextConfig);
