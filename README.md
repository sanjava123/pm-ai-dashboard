# PM AI Pulse - Deployed PM Dashboard

### 🚀 One-Click Deploy to Streamlit Cloud (Free)

1.  **Push this folder to a NEW GitHub Repo** (Public or Private).
2.  Go to **[share.streamlit.io](https://share.streamlit.io/)** -> **New App**.
3.  Connect GitHub -> Select Repo -> Branch `main` -> File `app.py`.
4.  **Click "Advanced Settings" -> "Secrets"** -> Paste the TOML below.
5.  **Deploy!** You get a public `https://your-app-name.streamlit.app` URL.

---

### 🔐 Required Secrets (TOML Format)
Paste this in **Streamlit Cloud -> Settings -> Secrets**. 
**NEVER put this in GitHub.**

```toml
# .streamlit/secrets.toml (Local) OR Streamlit Cloud Secrets UI
JIRA_URL = "https://your-domain.atlassian.net"
JIRA_EMAIL = "your-email@company.com"
JIRA_API_TOKEN = "your-jira-api-token-from-id.atlassian.com"
JIRA_PROJECT_KEY = "YOUR_PROJECT_KEY" # e.g., "PROJ"
GROQ_API_KEY = "gsk_..." # Get from console.groq.com
