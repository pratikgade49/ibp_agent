# Demand Desk UI

The React application is built into `../static/build`, which FastAPI serves at `/`.

## Local development

Start the FastAPI app on port 8080, then run:

```powershell
npm run dev
```

The dev command checks its dependencies and runs `npm ci` automatically when they are missing or incomplete. Vite proxies `/ask` and `/health` to the FastAPI app.

## Production build

Run `npm run build` to create the production frontend. This command also checks dependencies and runs `npm ci` when needed. The generated `static/build` directory must be included in the Cloud Foundry upload; `frontend/node_modules` is excluded.

## Cloud Foundry deploy

Run `cf push` from the repository root. The Node.js buildpack detects the root `package.json`; its `postinstall` hook installs frontend dependencies from `frontend/package-lock.json` and builds `static/build` during staging. The Python buildpack then installs the backend requirements and runs the FastAPI app. No separate frontend build command is required before pushing.