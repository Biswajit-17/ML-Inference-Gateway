# HarvestGate — Production Deployment Guide

This guide outlines the step-by-step instructions for deploying the HarvestGate ML Inference Gateway to **Railway** or **Render** using the standardized Docker image.

---

## 🛠️ Step 1: Push Code to GitHub

Ensure all local files, including the updated security configurations and dependencies, are pushed to your remote repository:

```bash
# Verify modified and new files (Dockerfile, docker-compose.yml, requirements.txt, static files)
git status

# Stage all files
git add .

# Commit changes
git commit -m "Configure containerized setup and patch dependencies"

# Push to your GitHub repository
git push
```

---

## 🚂 Option A: Deploying on Railway (Recommended)

Railway is highly recommended because it offers rapid compilation, easy Redis provisioning, and native Docker healthcheck integration.

### 1. Provision Redis on Railway
1. Go to the [Railway Dashboard](https://railway.app/) and click **New Project** ➔ **Provision Redis**.
2. Railway will create a standalone Redis container.
3. Click on the newly created **Redis** service, navigate to the **Variables** tab, and copy the value of `REDIS_URL` (it will look like `redis://default:password@host:port`).

### 2. Deploy the Gateway Service
1. In the same project dashboard, click **New** ➔ **GitHub Repo**.
2. Select your **HarvestGate** repository.
3. Railway will immediately detect the `Dockerfile` in the root folder and begin building the container.

### 3. Bind Environment Variables
Click on the **Gateway** service, go to the **Variables** tab, and add the following variables:
*   `ENV = production` (Disables API docs `/docs`)
*   `PORT = 8000`
*   `REDIS_URL` (Reference the Redis container privately to avoid egress fees and optimize speed: `${{ Redis.REDIS_PRIVATE_URL }}`)
*   `OPENROUTER_API_KEY` (Your OpenRouter token for advisory explanations)

### 4. Domain & Verification
1. Under the Gateway service **Settings** tab, click **Generate Domain** to get a public URL (e.g., `https://harvestgate-production.up.railway.app`).
2. Navigate to your URL in the browser to load your web dashboard!
3. Railway will automatically use the `HEALTHCHECK` block inside the `Dockerfile` to monitor the service health.

---

## ☁️ Option B: Deploying on Render

Render is a robust, clean alternative that fully supports custom Docker runtimes.

### 1. Provision Redis on Render
1. Go to the [Render Dashboard](https://dashboard.render.com/) and click **New** ➔ **Redis**.
2. Set a name (e.g., `harvestgate-cache`) and select the region.
3. Click **Create Redis**. Once created, copy the **Internal Redis URL** (e.g., `redis://red-xxxx:6379`).

### 2. Deploy the Gateway Web Service
1. Click **New** ➔ **Web Service**.
2. Connect your GitHub repository.
3. Set the **Runtime** to `Docker` (Render will automatically compile using your root `Dockerfile`).

### 3. Configure Variables & Health Checks
1. Scroll down to **Advanced** and add the following environment variables:
    *   `ENV = production`
    *   `PORT = 8000`
    *   `REDIS_URL` (Paste your **Internal Redis URL** copied in Step 1)
    *   `OPENROUTER_API_KEY` (Your OpenRouter token)
2. In the **Health Check Path** input field, enter `/health`.
3. Click **Create Web Service**.

---

## 📡 Step 3: Verify Deployed Gateway

Once the build finishes and transitions to `Active`/`Healthy`, you can verify the deployment by running a raw health check:

```bash
# Verify deployed gateway health status
curl -f https://your-public-url.com/health
```

Expected Response:
```json
{
  "status": "healthy",
  "onnx_model": "loaded",
  "redis_cache": "connected",
  "llm_circuit_breaker": "closed"
}
```

---

## 📊 Step 4: Connecting Production to Grafana Cloud

To see your production metrics inside your Grafana Cloud dashboard, you have two options:

### Option A: Hosted Scraping (Recommended — Zero Extra Costs)
Since your deployed gateway's `/metrics` endpoint is public, Grafana Cloud can pull metrics directly from your live server without running a local collector container.
1. Log in to [Grafana Cloud](https://grafana.com/).
2. Under **Connections**, select **Prometheus**.
3. Choose **Hosted Scrape** or setup an **Integration**.
4. Configure a new static target pointing directly to your public URL:
   * Target URL: `https://your-public-url.com/metrics`
5. Save the configuration. Grafana Cloud's hosted collectors will pull data directly into your remote cloud database.

### Option B: Deploying Grafana Alloy on Railway
If you prefer pushing metrics securely from your container network:
1. In your Railway project, click **New** ➔ **GitHub Repo** and import the project again.
2. Select **Settings** ➔ **Docker** ➔ and specify the target to build the Alloy service using the config file `./monitoring/alloy/config.alloy`.
3. Provide your `GRAFANA_CLOUD_PROM_URL`, `GRAFANA_CLOUD_USER`, and `GRAFANA_CLOUD_TOKEN` as environment variables.
4. Alloy will boot as a companion container, scrape your gateway privately, and remote-write to Grafana Cloud.
