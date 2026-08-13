# Deploying to Vercel

The repository root holds the **Streamlit** project. This Vercel app lives in
`bpcl-vercel/`. Vercel must therefore be pointed at this folder — that is the
only setting that matters, and getting it wrong produces a confusing error.

## Settings

| Setting | Value |
|---|---|
| **Root Directory** | **`bpcl-vercel`** |
| Framework Preset | **Vite** |
| Build Command | leave default (`npm run build`) |
| Output Directory | leave default (`dist`) |
| Install Command | leave default |
| Environment variables | none required |

Do **not** pick Python as the framework. The app is a Vite site; the Python
files under `api/` are picked up automatically as serverless functions because
they sit in `api/` and `requirements.txt` is present. There is no "language"
to choose beyond the Vite preset.

## Fixing it on an existing project

If the project was already created with the wrong root:

    Vercel dashboard
      -> your project
      -> Settings
      -> Build and Deployment
      -> Root Directory   ->  bpcl-vercel      -> Save
      -> Framework Preset ->  Vite             -> Save
      -> Deployments tab  ->  ... on the latest  ->  Redeploy

Root Directory cannot be changed from `vercel.json`; it is a project setting
only.

## The error this avoids

```
No python entrypoint found in default locations, but found potential
entrypoints: bpcl-vercel/api/air.py (variable: handler) ...
Add this to your pyproject.toml: [tool.vercel] entrypoint = "bpcl-vercel.api.air:handler"
```

Paths beginning `bpcl-vercel/` are the diagnosis: the build is running from the
repository root. There, Vercel sees the Streamlit project's `requirements.txt`
and `.py` files with no `package.json`, decides the whole repository is a single
Python application, and looks for one ASGI/WSGI entrypoint.

**Do not** add the suggested `pyproject.toml`. That would commit the deployment
to being one Python app serving one handler, and the React front end would never
be built at all. Set Root Directory instead.

## The other error this avoids

```
sh: line 1: vite: command not found
Error: Command "vite build" exited with 127
```

Two different causes produce this, and the quoted command tells you which.

**If the quoted command is `vite build`** — that is the Vite preset's *default*.
This repository's `vercel.json` sets `buildCommand` to `npm run build`, so seeing
`vite build` means `vercel.json` was never read. Vercel reads it from the Root
Directory, so the Root Directory is still wrong. Fix it as above; nothing in the
repository can work around it.

**If the quoted command is `npm run build`** — `vercel.json` was read, and the
problem is that `node_modules` is missing the build tools. That happens when
`NODE_ENV=production` is set in the project's Environment Variables, because npm
then skips `devDependencies`. Everything `npm run build` touches is therefore
kept in `dependencies` in `package.json`, not `devDependencies`, so the build
survives it. Verify with:

```bash
rm -rf node_modules
NODE_ENV=production npm install
npm run build          # must succeed
```

## From the CLI

```bash
cd bpcl-vercel
vercel --prod
```

Run from inside this directory and the CLI takes it as the root, so no extra
configuration is needed.

## Verifying a good deployment

- `/api/meta` returns JSON with `"ok": true`
- The masthead shows the BPCL logo, and the five tabs are plain text
- The build log shows Vite output (`dist/assets/...`), not a Python entrypoint search
