# BizChat static frontend

This is the dependency-free BizChat dashboard. It uses only `index.html`, `styles.css`, and `app.js`; there is no npm, Node.js, React, or build step.

## Run locally

From the repository root:

```bash
python -m http.server 3000 --directory frontend
```

Open <http://localhost:3000>. The dashboard uses the deployed Render API by default. For local development, configure the API before loading `app.js` with `window.BIZCHAT_API_URL`, for example:

```html
<script>window.BIZCHAT_API_URL = 'http://localhost:8000/api';</script>
<script src="app.js"></script>
```

If the API is unavailable or returns no jobs, the bundled sample dataset is rendered automatically and labelled as illustrative. Public dashboard and Bengaluru Jobs results are current-only by default: listings older than the configured freshness window, or without a posting date, are hidden. Bengaluru Jobs has an explicit **Include older or unknown-date listings** opt-in; it sends `include_stale=true` and leaves the default current-only scope unchanged.

The UI displays freshness status, listing age, posted date, and last verified/collected date when those fields are returned. The `/data-quality` metadata is used to show the configured freshness window and available dataset/source coverage. Salary copy describes employer disclosure signals and coverage counts rather than promising market value. Learn next forecast values are labelled deterministic baselines over a stated recent sample window, not validated predictions.

## Deploy to Vercel without Node.js

1. Push this repository to GitHub.
2. In Vercel, select **Add New Project** and import the repository.
3. Select **Other** as the framework.
4. Leave the build command empty.
5. Set the output directory to `frontend`.
6. Deploy.

After the Render API is deployed, update the API URL near the top of `app.js`:

```js
const API_URL = window.BIZCHAT_API_URL || 'https://your-api.onrender.com/api';
```

Then redeploy the frontend. The app still renders its bundled sample data if the API is temporarily unavailable.
