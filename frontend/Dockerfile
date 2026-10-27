# Multi-stage build: the dev-server CMD this used to end with served every
# request unminified/non-tree-shaken and shipped devDependencies at runtime.
# `output: "standalone"` (next.config.js) traces only the prod dependencies
# a running server actually needs into .next/standalone, so the runtime
# stage below needs neither `npm install` nor a copied node_modules at all.
FROM node:18-alpine AS build

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm ci

COPY . .
RUN npm run build

FROM node:18-alpine AS runtime

WORKDIR /app
ENV NODE_ENV=production

# Standalone output doesn't bundle public/ or the static assets - Next.js
# expects those copied in alongside the traced server. See
# https://nextjs.org/docs/pages/api-reference/next-config-js/output
COPY --from=build /app/public ./public
COPY --from=build /app/.next/standalone ./
COPY --from=build /app/.next/static ./.next/static

EXPOSE 3000

CMD ["node", "server.js"]
