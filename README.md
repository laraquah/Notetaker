# Notetaker
A streamlined Streamlit application that uses AI to automate meeting minutes, extract visual data from video recordings, and sync everything to **Basecamp** and **Google Drive**.

---

## ✨ Features

* **🎥 Visual Metadata Extraction:** Uses **Gemini Vision** to analyze the first frame of a video file and automatically detect the **Meeting Title**, **Date**, **Time**, and **Platform** (e.g., "Microsoft Teams").
* **🎙️ AI Transcription:** Uses **Google Cloud Speech-to-Text** for enterprise-grade, speaker-diarized audio transcription.
* **🧠 Smart Summarization:** Uses **Gemini 2.5 Flash-Lite** to generate:
    * **Executive Summary**
    * **Detailed Discussion Points**
    * **Specific Next Steps** (with assignees and deadlines)
    * **Client Requests**
* **📂 Smart Drive Sync:** Automatically creates and organizes folders in the user's Google Drive:
    * `Meeting Notes/` for generated Word docs.
    * `Chats/` for saved chat logs.
    * `Meeting_Data/` for hidden history storage.
* **⛺ Basecamp Integration:** Posts directly to specific Basecamp projects. Supports:
    * **To-dos** (creates items in specific lists).
    * **Message Boards** (posts new messages).
    * **Docs & Files** (uploads files to the vault).
* **💬 Context-Aware Chat:** Chat with current or *past* meetings. You can save these chat logs to Drive as formatted Word documents.
* **🔐 Secure Access:** The app remains locked until the user authenticates with both Basecamp and Google Drive via OAuth2.

---

## 📚 Libraries Used

Add these to your `requirements.txt` file:

```text
streamlit
google-cloud-speech
google-cloud-storage
google-generativeai
google-auth-oauthlib
google-api-python-client
python-docx
requests
requests-oauthlib
pytz
```

##⚙️ Configuration & Setup Guide
This app requires significant setup in the Google Cloud Console and Basecamp to function.

1. Google Cloud Platform (GCP) Setup
  1. Create a Project: Go to console.cloud.google.com and create a new project.
  2. Enable APIs: Go to "APIs & Services" > "Library" and enable these four APIs:
    - Cloud Speech-to-Text API
    - Google Cloud Storage JSON API
    - Google Drive API
    - Generative Language API (for Gemini)
  3. Create Service Account (For Robot Backend):
    - Go to "IAM & Admin" > "Service Accounts" > "Create Service Account".
    - Grant it the role: **Owner** (or Storage Admin + Speech Admin).
    - Click on the created account > **Keys** > **Add Key** > **Create new key (JSON)**.
      - *Save this file. You will copy its contents into your secrets later.*
  4. Create OAuth Client (For User Login):
    - Go to "APIs & Services" > "Credentials" > "Create Credentials" > OAuth client ID.
    - **Application Type:** Web application.
    - **Authorized redirect URIs:** Add your deployed Streamlit URL exactly (no trailing slash):
      ```
      https://your-app-name.streamlit.app
      ```
      - *Download the JSON. You will copy specific values from this later.*
  6. Configure Consent Screen:
    - Go to "OAuth consent screen".
    - Set User Type to **External** (unless you have a Workspace organization).
    - Add your email as a Test User.

2. Basecamp Setup
  1. Go to launchpad.37signals.com/integrations.
  2. Register a new Application.
  3. Redirect URI: Must match your Streamlit App URL exactly:
    - https://your-app-name.streamlit.app
      - Note down the **Client ID** and **Client Secret**.

4. Streamlit Secrets Configuration
- In your Streamlit Cloud dashboard, go to Advanced Settings -> Secrets and paste the following. Fill in the values from the steps above.

```Ini, TOML

# --- App Configuration ---
# EXACT URL of your deployed app (No trailing slash)
STREAMLIT_APP_URL = "[https://your-app-name.streamlit.app](https://your-app-name.streamlit.app)"

# --- Google Cloud Storage & Gemini ---
GCS_BUCKET_NAME = "your-unique-bucket-name"
GOOGLE_API_KEY = "your-gemini-api-key"  # Get this from aistudio.google.com

# --- Google Service Account (Copy entire content of service_account.json) ---
GCP_SERVICE_ACCOUNT_JSON = """
{
  "type": "service_account",
  "project_id": "...",
  "private_key_id": "...",
  "private_key": "-----BEGIN PRIVATE KEY-----...",
  "client_email": "...",
  "client_id": "...",
  "auth_uri": "[https://accounts.google.com/o/oauth2/auth](https://accounts.google.com/o/oauth2/auth)",
  "token_uri": "[https://oauth2.googleapis.com/token](https://oauth2.googleapis.com/token)",
  "auth_provider_x509_cert_url": "[https://www.googleapis.com/oauth2/v1/certs](https://www.googleapis.com/oauth2/v1/certs)",
  "client_x509_cert_url": "..."
}
"""

# --- Google Drive OAuth Client (Copy from OAuth Client JSON) ---
# Use TRIPLE SINGLE QUOTES (''') to avoid format errors
GDRIVE_CLIENT_SECRET_JSON = '''
{
  "web": {
    "client_id": "your-client-id.apps.googleusercontent.com",
    "project_id": "your-project-id",
    "auth_uri": "[https://accounts.google.com/o/oauth2/auth](https://accounts.google.com/o/oauth2/auth)",
    "token_uri": "[https://oauth2.googleapis.com/token](https://oauth2.googleapis.com/token)",
    "auth_provider_x509_cert_url": "[https://www.googleapis.com/oauth2/v1/certs](https://www.googleapis.com/oauth2/v1/certs)",
    "client_secret": "your-client-secret",
    "redirect_uris": ["[https://your-app-name.streamlit.app](https://your-app-name.streamlit.app)"]
  }
}
'''

# --- Basecamp Integration ---
BASECAMP_ACCOUNT_ID = "your-basecamp-account-id" # Found in your Basecamp URL
BASECAMP_CLIENT_ID = "your-basecamp-client-id"
BASECAMP_CLIENT_SECRET = "your-basecamp-client-secret"
```
