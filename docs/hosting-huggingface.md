# Hosting FloodCheck on Hugging Face Spaces

Hugging Face Spaces runs the FloodCheck Docker container for free and gives it a public HTTPS link such as `https://<your-account>-floodcheck.hf.space`. The project is already set up for it:

- the settings block at the top of `README.md` tells the Space to use Docker on port 7860;
- the `Dockerfile` runs the app as user 1000 on port 7860, as Spaces requires.

## What you get on the free tier

| | |
|---|---|
| Hardware | "CPU basic": 2 vCPUs and 16 GB memory, enough for full-size areas |
| Link | Public HTTPS address, works on phones |
| Sleep | The Space sleeps after a period without visitors (about two days) and takes a minute or two to wake on the next visit |
| Memory between runs | Cached areas and GeoTIFFs are lost when the Space sleeps or restarts. The first run in an area after a restart takes a few minutes again |

Upgrading the hardware in the Space settings removes the sleep, if you need that later.

## Step 1 — Create the Space

1. Sign in at huggingface.co (create a free account if needed). An organisation account for the company keeps it separate from personal accounts.
2. Choose **New Space**.
3. Fill in:
   - **Space name:** `floodcheck`
   - **License:** your choice (for example `other` until you decide)
   - **SDK:** **Docker**, then template **Blank**
   - **Hardware:** **CPU basic (free)**
   - **Visibility:** **Public** if community users should open it without an account. **Private** means only signed-in members can see it.
4. Choose **Create Space**. It opens an empty Space.

## Step 2 — Upload the project

Upload the **contents** of the `floodcheck/` folder (so `README.md` and `Dockerfile` sit at the top of the Space, not inside a subfolder).

**Option A — in the browser (simplest)**

1. In the Space, open **Files** → **Add file** → **Upload files**.
2. Drag in everything inside `floodcheck/`: `README.md`, `Dockerfile`, `requirements.txt`, `run.py`, `.dockerignore`, and the `backend/` folder. `docs/`, `tests/` and `notebooks/` are optional; the app does not need them.
3. Write a short message (for example "First upload") and choose **Commit changes to main**.

**Option B — from the command line (best for updates)**

```bash
pip install -U huggingface_hub
hf auth login                      # paste an access token with "write" permission
cd floodcheck
hf upload <your-account>/floodcheck . --repo-type=space --exclude "results/*" ".numba_cache/*" "*.pyc"
```

Use the upload tool rather than plain `git push`: the Space rejects binary files such as the logo PNGs unless they are stored as large files, and the upload tool handles that automatically.

## Step 3 — Wait for the build

The Space shows **Building**, then **Running**. The first build takes several minutes because it installs the Python packages. Open the **Logs** tab to follow it. When it says **Running**, the **App** tab shows the dashboard.

Test it: press **Use an example area**, choose **Choose myself** and **Heavy** rain, then **Check for floods**. The first run takes a few minutes while it downloads data.

## Step 4 — Share the link

Use the direct link (`https://<your-account>-floodcheck.hf.space`) rather than the Space page: it opens the dashboard full screen, without the Hugging Face frame. You can find it under the **⋮** menu → **Embed this Space** → **Direct URL**.

## Optional settings

In the Space, open **Settings** → **Variables and secrets** and add variables (not secrets) as needed. The Space restarts with them:

| Variable | When to set it |
|---|---|
| `FLOOD_CORS_ORIGINS` | When a separate frontend (for example the Angular app) must call this API, e.g. `http://localhost:4200,https://your-team.github.io` |
| `FLOOD_STREET_TILES` and `FLOOD_STREET_ATTRIBUTION` | When you have a keyed street-map provider |

Put API keys under **Secrets** if a provider's key must stay private. Note that a street-map tile URL is sent to every visitor's browser, so a key inside it is visible to users anyway; use a key restricted to your domain.

## Updating the app

Upload the changed files again (Option A or B). The Space rebuilds and restarts automatically. Changes to the page, styles or script still need a rebuild on Spaces, because the files live inside the container.

## If something goes wrong

| What you see | What to do |
|---|---|
| Build fails | Open **Logs**, scroll to the first red error. A package install error usually means a temporary download problem: choose **Factory rebuild** in Settings |
| "Runtime error" or app never starts | Check that `README.md` (with the settings block at the very top) and `Dockerfile` are at the top level of the Space, not in a subfolder |
| Page loads but runs fail | Open **Logs** while running a check; the error from the model or a data service appears there |
| Slow first visit | The Space was asleep. It wakes within a minute or two |
