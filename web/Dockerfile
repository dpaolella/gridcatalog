# The web UI, built for the server-rendered deployment.
#
# Not the static export: that one is files, and `pages.yml` builds it. This is
# the Node process that renders per request against a live API — the mode
# `ops/docker-compose.yml` runs, and the reason `DATAHUB_API_URL` is read at
# request time rather than baked in at build time (see `next.config.ts`).

FROM node:22-slim AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:22-slim AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# No DATAHUB_SNAPSHOT, so this is the server build: `output: "export"` stays off
# and the API routes under app/api survive into the image.
RUN npm run build

FROM node:22-slim AS run
WORKDIR /app
ENV NODE_ENV=production
# Read at request time, so one image serves any deployment. Overridden by
# compose; the default is only useful when the API shares a host.
ENV DATAHUB_API_URL=http://api:8000
# The same API as a *browser* should address it, for the URLs rendered into the
# page: the sign-in link, the OpenAPI and docs links, the MCP server URL.
# Deliberately left unset — `publicApiUrl()` then falls back to the line above,
# which is right when the API really does share a host, and every other
# deployment must say. `DATAHUB_` and not `NEXT_PUBLIC_`: a `NEXT_PUBLIC_` value
# is inlined by `npm run build` above, so it could only be a build argument, and
# then this image would serve exactly one deployment.
COPY --from=build /app/.next ./.next
COPY --from=build /app/public ./public
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/package.json ./package.json
EXPOSE 3000
CMD ["npm", "start", "--", "-p", "3000", "-H", "0.0.0.0"]
