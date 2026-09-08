FROM node:22-alpine AS build
WORKDIR /workspace
RUN corepack enable
COPY package.json pnpm-workspace.yaml pnpm-lock.yaml ./
COPY apps/web/package.json apps/web/package.json
COPY packages/contracts/package.json packages/contracts/package.json
COPY packages/embed/package.json packages/embed/package.json
COPY packages/sdk/package.json packages/sdk/package.json
COPY packages/sse-client/package.json packages/sse-client/package.json
RUN pnpm install --frozen-lockfile --filter @alcuin/web...
COPY apps/web apps/web
COPY packages/contracts packages/contracts
COPY packages/embed packages/embed
COPY packages/sdk packages/sdk
COPY packages/sse-client packages/sse-client
ARG NEXT_PUBLIC_ALCUIN_API_URL=http://localhost:8000
ARG NEXT_PUBLIC_ALCUIN_WORKSPACE_ID=ws_demo
RUN pnpm --filter @alcuin/web build

FROM node:22-alpine AS runtime
WORKDIR /workspace
ENV NODE_ENV=production
COPY --from=build /workspace ./
EXPOSE 3000
CMD ["node", "apps/web/node_modules/next/dist/bin/next", "start", "apps/web"]
