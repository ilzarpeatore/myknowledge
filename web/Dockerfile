FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci

FROM node:22-alpine AS build
WORKDIR /app
ARG KOS_API_INTERNAL_URL=http://api:8000
ENV KOS_API_INTERNAL_URL=$KOS_API_INTERNAL_URL NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
RUN npm run build

FROM node:22-alpine AS run
WORKDIR /app
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 HOSTNAME=0.0.0.0 PORT=3000
RUN adduser -S -u 10001 kos
COPY --from=build --chown=kos /app/.next/standalone ./
COPY --from=build --chown=kos /app/.next/static ./.next/static
# the PDF viewer's engine, worker and fonts (copied there by `npm run build`)
COPY --from=build --chown=kos /app/public ./public
USER kos
EXPOSE 3000
CMD ["node", "server.js"]
