# Deploying AI Research Backend to Railway

Railway is a great platform for deploying containerized applications with minimal configuration. It automatically detects your `Dockerfile` and handles the build and deployment process.

## Prerequisites

1. A [Railway](https://railway.app/) account.
2. Your code pushed to a GitHub repository.

## Deployment Steps

### 1. Create a New Project

1. Go to your Railway dashboard and click **New Project**.
2. Select **Deploy from GitHub repo**.
3. Choose your `AI-Research-Assistant-Backend` repository.
4. Railway will automatically detect the `Dockerfile` in the root of your repository and begin building the image.

### 2. Configure Environment Variables

While the initial build is running, you should set up your environment variables:

1. Click on your newly created service in the Railway project canvas.
2. Go to the **Variables** tab.
3. Add your required environment variables. For example:
   - `API_KEY` = `your-demo-api-key`
   - `OLLAMA_API_BASE` = `your-ollama-url` (if applicable)
   - `OLLAMA_API_KEY` = `your-ollama-key`
   - `RATE_LIMIT_PER_MINUTE` = `10`
   - `MAX_CONCURRENT_RESEARCH_JOBS` = `1`

*Note: Railway automatically injects a `PORT` variable, which our `Dockerfile` is already configured to use.*

### 3. Generate a Public Domain

To make your backend accessible to your frontend:

1. Go to the **Settings** tab of your service.
2. Scroll down to the **Networking** section.
3. Click **Generate Domain** (or add a custom domain). 
4. This will give you a URL like `ai-research-backend-production.up.railway.app`. Use this URL as the `API_BASE_URL` in your frontend.

### 4. CORS Configuration

Ensure that your frontend's domain is allowed in the backend's CORS settings. If your frontend is deployed to a new domain (like Vercel or Netlify), you may need to update the CORS configuration in `src/ai_research_backend/api.py` and push the changes to GitHub. Railway will automatically rebuild and redeploy your service.
