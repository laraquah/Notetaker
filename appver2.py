import streamlit as st
import streamlit.components.v1 as components
import os
import shutil

# --- FIX: ALLOW OAUTH TO RUN ON STREAMLIT CLOUD ---
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

import tempfile
from docx import Document
from docx.shared import Pt, Inches, RGBColor
import io
import time
import subprocess
import pickle
import json
import datetime
import pytz
import re

# Import Google Cloud Libraries
from google.cloud import speech
from google.cloud import storage
import google.generativeai as genai

# Import Google Auth & Drive Libraries
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload, MediaIoBaseDownload
from google.oauth2 import service_account

# --- Import Basecamp & formatting tools ---
import requests
from requests_oauthlib import OAuth2Session

# -----------------------------------------------------
# 1. CONSTANTS & CONFIGURATION
# -----------------------------------------------------
st.set_page_config(layout="wide", page_title="AI Meeting Manager", page_icon="🤖")

# --- Load App Keys from Secrets ---
try:
    GCS_BUCKET_NAME = st.secrets["GCS_BUCKET_NAME"]
    GOOGLE_API_KEY = st.secrets["GOOGLE_API_KEY"]
    GCP_SERVICE_ACCOUNT_JSON = json.loads(st.secrets["GCP_SERVICE_ACCOUNT_JSON"])
    GDRIVE_CLIENT_CONFIG = json.loads(st.secrets["GDRIVE_CLIENT_SECRET_JSON"])
    BASECAMP_CLIENT_ID = st.secrets["BASECAMP_CLIENT_ID"]
    BASECAMP_CLIENT_SECRET = st.secrets["BASECAMP_CLIENT_SECRET"]
    BASECAMP_ACCOUNT_ID = st.secrets["BASECAMP_ACCOUNT_ID"]
    
    # --- AUTO-LOGIN LOGIC ---
    STREAMLIT_APP_URL = st.secrets.get("STREAMLIT_APP_URL", None)
    
    if STREAMLIT_APP_URL:
        BASECAMP_REDIRECT_URI = STREAMLIT_APP_URL.rstrip("/")
        AUTO_LOGIN_MODE = True
    else:
        BASECAMP_REDIRECT_URI = "https://www.google.com"
        AUTO_LOGIN_MODE = False

except Exception as e:
    st.error(f"Secrets Configuration Error: {e}")
    st.stop()

# URLs
BASECAMP_AUTH_URL = "https://launchpad.37signals.com/authorization/new"
BASECAMP_TOKEN_URL = "https://launchpad.37signals.com/authorization/token"
BASECAMP_API_BASE = f"https://3.basecampapi.com/{BASECAMP_ACCOUNT_ID}"
BASECAMP_USER_AGENT = {"User-Agent": "AI Meeting Notes App (external-user)"}

# -----------------------------------------------------
# 2. HELPER: GET USER IDENTITY
# -----------------------------------------------------
def fetch_basecamp_name(token_dict):
    try:
        identity_url = "https://launchpad.37signals.com/authorization.json"
        headers = {
            "Authorization": f"Bearer {token_dict['access_token']}",
            "User-Agent": "AI Meeting Notes App"
        }
        response = requests.get(identity_url, headers=headers)
        if response.status_code == 200:
            data = response.json()
            first = data.get('identity', {}).get('first_name', '')
            last = data.get('identity', {}).get('last_name', '')
            return f"{first} {last}".strip()
    except Exception:
        return ""
    return ""

# -----------------------------------------------------
# 3. STATE & AUTO-LOGIN HANDLER
# -----------------------------------------------------
if 'gdrive_creds' not in st.session_state:
    st.session_state.gdrive_creds = None
if 'basecamp_token' not in st.session_state:
    st.session_state.basecamp_token = None
if 'user_real_name' not in st.session_state:
    st.session_state.user_real_name = ""
if 'detected_date' not in st.session_state:
    st.session_state.detected_date = None
if 'detected_time' not in st.session_state:
    st.session_state.detected_time = None
if 'detected_title' not in st.session_state:
    st.session_state.detected_title = "Meeting_Minutes"
if 'detected_venue' not in st.session_state:
    st.session_state.detected_venue = ""

# --- FIX: IMMEDIATE GOOGLE RE-LOGIN ---
if 'gdrive_creds_json' in st.session_state and st.session_state.gdrive_creds_json and not st.session_state.gdrive_creds:
    try:
        creds = Credentials.from_authorized_user_info(
            json.loads(st.session_state.gdrive_creds_json)
        )
        st.session_state.gdrive_creds = creds
    except Exception as e:
        st.session_state.gdrive_creds_json = None

# --- AUTO-LOGIN HANDLER (BASECAMP) ---
if AUTO_LOGIN_MODE and "code" in st.query_params and not st.session_state.basecamp_token:
    auth_code = st.query_params["code"]
    try:
        payload = {
            "type": "web_server",
            "client_id": BASECAMP_CLIENT_ID,
            "client_secret": BASECAMP_CLIENT_SECRET,
            "redirect_uri": BASECAMP_REDIRECT_URI,
            "code": auth_code
        }
        response = requests.post(BASECAMP_TOKEN_URL, data=payload)
        response.raise_for_status()
        token = response.json()
        
        st.session_state.basecamp_token = token
        
        real_name = fetch_basecamp_name(token)
        if real_name:
            st.session_state.user_real_name = real_name
            
        st.query_params.clear()
        st.toast("✅ Basecamp Login Successful!", icon="🎉")
        time.sleep(1)
        st.rerun()
    except Exception as e:
        st.error(f"Auto-login failed: {e}")

# -----------------------------------------------------
# 4. SIDEBAR UI
# -----------------------------------------------------
with st.sidebar:
    st.title("🔐 Login")
    
    # --- STEP 1: BASECAMP ---
    st.markdown("### Step 1: Basecamp")
    
    if st.session_state.basecamp_token:
        st.success(f"✅ Connected as {st.session_state.user_real_name}")
        if st.button("Logout Basecamp"):
            st.session_state.basecamp_token = None
            st.session_state.user_real_name = ""
            st.rerun()
    else:
        bc_oauth = OAuth2Session(BASECAMP_CLIENT_ID, redirect_uri=BASECAMP_REDIRECT_URI)
        bc_auth_url, _ = bc_oauth.authorization_url(BASECAMP_AUTH_URL, type="web_server")
        
        if AUTO_LOGIN_MODE:
            st.link_button("Login to Basecamp", bc_auth_url, type="primary")
            st.caption("Opens in a new tab. Close it after logging in.")
        else:
            st.markdown(f"👉 [**Authorize Basecamp**]({bc_auth_url})")
            bc_code = st.text_input("Paste Basecamp Code:", key="bc_code")
            if bc_code:
                try:
                    payload = {
                        "type": "web_server",
                        "client_id": BASECAMP_CLIENT_ID,
                        "client_secret": BASECAMP_CLIENT_SECRET,
                        "redirect_uri": BASECAMP_REDIRECT_URI,
                        "code": bc_code
                    }
                    response = requests.post(BASECAMP_TOKEN_URL, data=payload)
                    response.raise_for_status()
                    token = response.json()
                    st.session_state.basecamp_token = token
                    real_name = fetch_basecamp_name(token)
                    if real_name: st.session_state.user_real_name = real_name
                    st.rerun()
                except Exception as e:
                    st.error(f"Login failed: {e}")

    st.divider()

    # --- STEP 2: GOOGLE DRIVE ---
    st.markdown("### Step 2: Google Drive")
    
    if not st.session_state.basecamp_token:
        st.info("🔒 Please complete Step 1 first.")
    else:
        if st.session_state.gdrive_creds:
            st.success("✅ Connected")
            if st.button("Logout Drive"):
                st.session_state.gdrive_creds = None
                st.session_state.gdrive_creds_json = None
                st.rerun()
        else:
            try:
                flow = Flow.from_client_config(
                    GDRIVE_CLIENT_CONFIG,
                    scopes=["https://www.googleapis.com/auth/drive"],
                    redirect_uri="urn:ietf:wg:oauth:2.0:oob"
                )
                auth_url, _ = flow.authorization_url(prompt='consent')
                
                st.link_button("Login to Google Drive", auth_url)
                g_code = st.text_input("Paste Google Code:", key="g_code")
                
                if g_code:
                    flow.fetch_token(code=g_code)
                    st.session_state.gdrive_creds = flow.credentials
                    st.session_state.gdrive_creds_json = flow.credentials.to_json()
                    st.rerun()
            except Exception as e:
                st.error(f"Config Error: {e}")

# -----------------------------------------------------
# 5. SECURITY LOCK
# -----------------------------------------------------
if not (st.session_state.basecamp_token and st.session_state.gdrive_creds):
    st.title("🔒 Access Restricted")
    st.warning("Please log in to **Basecamp** and **Google Drive** in the sidebar.")
    st.stop() 

# =====================================================
#     MAIN APP LOGIC
# =====================================================

# --- API CLIENTS ---
try:
    sa_creds = service_account.Credentials.from_service_account_info(GCP_SERVICE_ACCOUNT_JSON)
    storage_client = storage.Client(credentials=sa_creds)
    speech_client = speech.SpeechClient(credentials=sa_creds)
    
    genai.configure(api_key=GOOGLE_API_KEY)
    gemini_model = genai.GenerativeModel('gemini-2.5-flash-lite')
except Exception as e:
    st.error(f"System Error (AI Services): {e}")
    st.stop()

# -----------------------------------------------------
# 6. HELPER FUNCTIONS (UPDATED FOR FORMATTING)
# -----------------------------------------------------

def _add_rich_text(paragraph, text):
    """Parses markdown-style **bold** text and applies Word formatting."""
    # Regex to split by **text**
    parts = re.split(r'(\*\*.*?\*\*)', text)
    for part in parts:
        if part.startswith('**') and part.endswith('**'):
            # Remove asterisks and make bold
            clean_text = part[2:-2]
            run = paragraph.add_run(clean_text)
            run.bold = True
        else:
            # Regular text
            paragraph.add_run(part)

def add_formatted_text(cell, text):
    """Enhanced parser to handle bullets and bold text correctly in Word table cells."""
    cell.text = ""
    lines = text.split('\n')
    for line in lines:
        line = line.strip()
        if not line: continue
        
        p = cell.add_paragraph()
        p.paragraph_format.space_before = Pt(0)
        p.paragraph_format.space_after = Pt(2) # Slight spacing for readability
        
        if line.startswith('##'):
            # Heading style within cell
            clean_title = line.lstrip('#').strip()
            run = p.add_run(clean_title)
            run.underline = True
            run.bold = True
            p.paragraph_format.space_before = Pt(8)
        elif line.startswith('*') or line.startswith('-'):
            # Bullet point with rich text support
            clean_text = line.lstrip('*- ').strip()
            p.style = 'List Bullet' # Use Word's native bullet style if available
            # If style fails, manual bullet:
            if not p.style: p.text = "• "
            _add_rich_text(p, clean_text)
        else:
            # Normal paragraph with rich text support
            _add_rich_text(p, line)

# --- Markown to Doc (For Chat) ---
def add_markdown_to_doc(doc, text):
    lines = text.split('\n')
    table_row_pattern = re.compile(r"^\|(.+)\|")
    table_sep_pattern = re.compile(r"^\|[-:| ]+\|")
    table_data = []
    in_table = False

    for line in lines:
        stripped = line.strip()
        if table_row_pattern.match(stripped):
            if not table_sep_pattern.match(stripped):
                cells = [c.strip() for c in stripped.strip('|').split('|')]
                table_data.append(cells)
            in_table = True
            continue
        elif in_table and not table_row_pattern.match(stripped) and stripped == "":
            if table_data:
                _render_word_table(doc, table_data)
                table_data = []
            in_table = False
            continue
        elif in_table:
             if table_data:
                _render_word_table(doc, table_data)
                table_data = []
             in_table = False

        if stripped.startswith('##'):
            p = doc.add_heading(stripped.lstrip('#').strip(), level=2)
        elif stripped.startswith('*') or stripped.startswith('-'):
            clean_text = stripped.lstrip('*- ').strip()
            p = doc.add_paragraph(style='List Bullet')
            _add_rich_text(p, clean_text)
        elif stripped:
            p = doc.add_paragraph()
            _add_rich_text(p, stripped)

    if table_data: _render_word_table(doc, table_data)

def _render_word_table(doc, rows):
    if not rows: return
    num_cols = max(len(r) for r in rows)
    table = doc.add_table(rows=len(rows), cols=num_cols)
    table.style = 'Table Grid'
    for i, row_data in enumerate(rows):
        row_cells = table.rows[i].cells
        for j, text in enumerate(row_data):
            if j < len(row_cells):
                p = row_cells[j].paragraphs[0]
                _add_rich_text(p, text)
                if i == 0: 
                    for run in p.runs: run.bold = True
    doc.add_paragraph("")

# --- METADATA EXTRACTION ---
def get_visual_metadata(file_path):
    if shutil.which("ffmpeg") is None: return None
    thumbnail_path = "temp_thumb.jpg"
    result_data = {"datetime_sg": None, "duration": 0, "title": "Meeting_Minutes", "venue": ""}
    try:
        cmd = ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1", file_path]
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode == 0: result_data["duration"] = float(res.stdout.strip())

        subprocess.run(['ffmpeg', '-i', file_path, '-ss', '00:00:01', '-vframes', '1', '-q:v', '2', '-y', thumbnail_path], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        if os.path.exists(thumbnail_path):
            vision = genai.GenerativeModel('gemini-2.5-flash-lite')
            with open(thumbnail_path, "rb") as img: img_data = img.read()
            
            prompt = """Analyze this meeting screenshot. Return JSON:
            { "datetime": "YYYY-MM-DD HH:MM", "title": "Center Text", "venue": "Corner Text" }
            If not found, use "None"."""
            
            resp = vision.generate_content([{'mime_type': 'image/jpeg', 'data': img_data}, prompt])
            try:
                data = json.loads(resp.text.strip().replace("```json", "").replace("```", ""))
                if data.get("title") != "None": result_data["title"] = data["title"].replace(" ", "_")
                if data.get("venue") != "None": result_data["venue"] = data["venue"]
                if data.get("datetime") != "None":
                    dt = datetime.datetime.strptime(data["datetime"], "%Y-%m-%d %H:%M").replace(tzinfo=datetime.timezone.utc)
                    result_data["datetime_sg"] = dt.astimezone(pytz.timezone('Asia/Singapore'))
            except: pass
    except: pass
    finally:
        if os.path.exists(thumbnail_path): os.remove(thumbnail_path)
    return result_data

# --- Drive & Cloud Helpers ---
def upload_to_gcs(file_path, blob_name):
    try:
        bucket = storage_client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(blob_name)
        blob.upload_from_filename(file_path, timeout=3600)
        return f"gs://{GCS_BUCKET_NAME}/{blob_name}"
    except: return None

def get_or_create_folder(service, name):
    try:
        q = f"mimeType='application/vnd.google-apps.folder' and name='{name}' and trashed=false"
        res = service.files().list(q=q, fields="files(id)").execute()
        items = res.get('files', [])
        if items: return items[0]['id']
        else:
            meta = {'name': name, 'mimeType': 'application/vnd.google-apps.folder'}
            return service.files().create(body=meta, fields='id').execute().get('id')
    except: return None

def upload_to_drive_user(file, name, folder):
    if not st.session_state.gdrive_creds: return None
    try:
        service = build("drive", "v3", credentials=st.session_state.gdrive_creds)
        fid = get_or_create_folder(service, folder)
        meta = {"name": name, "parents": [fid] if fid else []}
        media = MediaIoBaseUpload(file, mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document")
        return service.files().create(body=meta, media_body=media, fields="id").execute().get("id")
    except: return None

def save_analysis_data_to_drive(data, filename):
    if not st.session_state.gdrive_creds: return None
    try:
        service = build("drive", "v3", credentials=st.session_state.gdrive_creds)
        fid = get_or_create_folder(service, "Meeting_Data")
        fh = io.BytesIO(json.dumps(data, indent=2).encode('utf-8'))
        meta = {"name": filename, "parents": [fid] if fid else []}
        media = MediaIoBaseUpload(fh, mimetype='application/json')
        return service.files().create(body=meta, media_body=media, fields="id").execute().get("id")
    except: return None

def list_past_meetings():
    if not st.session_state.gdrive_creds: return []
    try:
        service = build("drive", "v3", credentials=st.session_state.gdrive_creds)
        fid = get_or_create_folder(service, "Meeting_Data")
        if not fid: return []
        q = f"'{fid}' in parents and mimeType='application/json' and trashed=false"
        return service.files().list(q=q, fields="files(id, name, createdTime)", orderBy="createdTime desc").execute().get('files', [])
    except: return []

def load_meeting_data(file_id):
    if not st.session_state.gdrive_creds: return None
    try:
        service = build("drive", "v3", credentials=st.session_state.gdrive_creds)
        fh = io.BytesIO()
        downloader = MediaIoBaseDownload(fh, service.files().get_media(fileId=file_id))
        done = False
        while not done: _, done = downloader.next_chunk()
        fh.seek(0)
        return json.load(fh)
    except: return None

# --- Basecamp Helpers ---
def get_bc_session():
    if not st.session_state.basecamp_token: return None
    s = OAuth2Session(BASECAMP_CLIENT_ID, token=st.session_state.basecamp_token)
    s.headers.update(BASECAMP_USER_AGENT)
    return s

def get_bc_projects(sess):
    try: return sorted([(p['name'], p['id']) for p in sess.get(f"{BASECAMP_API_BASE}/projects.json").json() if p['status']=='active'], key=lambda x:x[0])
    except: return []

def get_bc_dock(sess, pid):
    try: return sess.get(f"{BASECAMP_API_BASE}/projects/{pid}.json").json().get('dock', [])
    except: return []

def get_bc_lists(sess, tid, pid):
    try: return sorted([(t['title'], t['id']) for t in sess.get(f"{BASECAMP_API_BASE}/buckets/{pid}/todosets/{tid}/todolists.json").json()], key=lambda x:x[0])
    except: return []

def upload_bc_file(sess, data, name):
    try:
        h = sess.headers.copy(); h.update({'Content-Type': 'application/octet-stream', 'Content-Length': str(len(data))})
        return sess.post(f"{BASECAMP_API_BASE}/attachments.json?name={name}", data=data, headers=h).json()['attachable_sgid']
    except: return None

def post_to_bc(sess, pid, tool, tid, subid, title, body, sgid):
    try:
        att = f'<bc-attachment sgid="{sgid}"></bc-attachment>' if sgid else ""
        if tool == "To-dos":
            url, pl = f"{BASECAMP_API_BASE}/buckets/{pid}/todolists/{subid}/todos.json", {"content": title, "description": body + att}
        elif tool == "Message Board":
            url, pl = f"{BASECAMP_API_BASE}/buckets/{pid}/message_boards/{tid}/messages.json", {"subject": title, "content": body + att, "status": "active"}
        elif tool == "Docs & Files":
            url, pl = f"{BASECAMP_API_BASE}/buckets/{pid}/vaults/{tid}/uploads.json", {"attachable_sgid": sgid, "base_name": title, "content": body}
        sess.post(url, json=pl).raise_for_status()
        return True
    except: return False

def analyze_audio_gemini(path, participants):
    try:
        with st.spinner("Converting..."):
            subprocess.run(['ffmpeg', '-i', path, '-vn', '-acodec', 'flac', '-y', f"{path}.flac"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        
        with st.spinner("Uploading..."):
            uri = upload_to_gcs(f"{path}.flac", f"{os.path.basename(path)}.flac")
            if not uri: return {"error": "Upload failed"}

        with st.spinner("Transcribing..."):
            client = speech.SpeechClient(credentials=service_account.Credentials.from_service_account_info(GCP_SERVICE_ACCOUNT_JSON))
            audio = speech.RecognitionAudio(uri=uri)
            config = speech.RecognitionConfig(encoding=speech.RecognitionConfig.AudioEncoding.FLAC, language_code="en-US", enable_automatic_punctuation=True, use_enhanced=True, model="video", diarization_config=speech.SpeakerDiarizationConfig(enable_speaker_diarization=True, min_speaker_count=2, max_speaker_count=6))
            op = client.long_running_recognize(config=config, audio=audio)
            res = op.result(timeout=3600)
            
            transcript = ""
            cur_spk = -1
            for w in res.results[-1].alternatives[0].words:
                if w.speaker_tag != cur_spk:
                    cur_spk = w.speaker_tag
                    transcript += f"\n\nSpeaker {cur_spk}: "
                transcript += w.word + " "
            
        with st.spinner("Analyzing..."):
            prompt = f"""
            Role: Expert Meeting Secretary.
            Context: {participants}
            Transcript: {transcript}
            
            Tasks:
            1. Identify speakers.
            2. Create structured sections:
               ## OVERVIEW ##
               [Brief summary of WHO met and WHAT was discussed - 2-3 sentences]
               
               ## DISCUSSION ##
               [Detailed bullet points with headers]
               
               ## NEXT STEPS ##
               [Specific actions: * **Name**: Action - Deadline]
               
               ## CLIENT REQUESTS ##
               [Requests from client]
            """
            resp = gemini_model.generate_content(prompt).text
            
            # Parsing
            ov, disc, next_s, creq = "", "", "", ""
            try:
                if "## OVERVIEW ##" in resp: ov = resp.split("## OVERVIEW ##")[1].split("## DISCUSSION ##")[0].strip()
                if "## DISCUSSION ##" in resp: disc = resp.split("## DISCUSSION ##")[1].split("## NEXT STEPS ##")[0].strip()
                if "## NEXT STEPS ##" in resp: next_s = resp.split("## NEXT STEPS ##")[1].split("## CLIENT REQUESTS ##")[0].strip()
                if "## CLIENT REQUESTS ##" in resp: creq = resp.split("## CLIENT REQUESTS ##")[1].strip()
            except: disc = resp

            return {"overview": ov, "discussion": disc, "next_steps": next_s, "client_reqs": creq, "full_transcript": transcript}
            
    except Exception as e: return {"error": str(e)}
    finally:
        try: storage_client.bucket(GCS_BUCKET_NAME).blob(f"{os.path.basename(path)}.flac").delete()
        except: pass

# -----------------------------------------------------
# 8. MAIN UI
# -----------------------------------------------------
if 'ai_results' not in st.session_state: st.session_state.ai_results = {}
if 'chat_history' not in st.session_state: st.session_state.chat_history = []
if 'saved_participants' not in st.session_state: st.session_state.saved_participants = ""

st.title("🤖 AI Meeting Manager")

tab1, tab2, tab3, tab4 = st.tabs(["1. Analyze", "2. Review & Export", "3. Chat", "4. History"])

with tab1:
    st.header("1. Analyze")
    participants = st.text_area("Participants", "Client (Client)\niFoundries (iFoundries)")
    up = st.file_uploader("Upload", type=['mp3','mp4','m4a','wav'])
    
    if st.button("Analyze"):
        if up:
            with tempfile.NamedTemporaryFile(delete=False, suffix=f".{up.name.split('.')[-1]}") as tmp:
                tmp.write(up.getvalue()); path = tmp.name
            
            # 1. Get Metadata
            with st.spinner("Extracting Metadata..."):
                meta = get_visual_metadata(path)
                st.session_state.detected_date = meta['datetime_sg'].date() if meta['datetime_sg'] else datetime.date.today()
                
                # Time Range logic
                if meta['datetime_sg'] and meta['duration']:
                    end = meta['datetime_sg'] + datetime.timedelta(seconds=meta['duration'])
                    st.session_state.detected_time = f"{meta['datetime_sg'].strftime('%I:%M %p')} - {end.strftime('%I:%M %p')}"
                else: st.session_state.detected_time = "Unknown"
                
                st.session_state.detected_title = meta['title'] if meta['title'] else "Meeting"
                st.session_state.detected_venue = meta['venue'] if meta['venue'] else ""
            
            # 2. Analyze
            st.session_state.saved_participants = participants
            res = analyze_audio_gemini(path, participants)
            
            if "error" in res: st.error(res["error"])
            else:
                st.session_state.ai_results = res
                # Auto-Save to History (Include Chat History if any, usually empty here)
                if st.session_state.gdrive_creds:
                    save_data = {
                        "ai_results": res, 
                        "participants": participants, 
                        "date": str(datetime.datetime.now()),
                        "chat_history": [] # Init empty chat history
                    }
                    save_analysis_data_to_drive(save_data, f"Data_{up.name}_{datetime.datetime.now().strftime('%Y%m%d')}.json")
                
                # Auto-Fill Reps
                cl = [l for l in participants.split('\n') if "(Client)" in l]
                il = [l for l in participants.split('\n') if "(iFoundries)" in l]
                st.session_state.crep = "\n".join(cl)
                st.session_state.irep = "\n".join(il)
                
                st.success("Done! Go to Review tab.")

with tab2:
    st.header("2. Review")
    # Defaults
    d_date = st.session_state.get('detected_date', datetime.date.today())
    d_time = st.session_state.get('detected_time', "")
    d_venue = st.session_state.get('detected_venue', "")
    
    c1, c2 = st.columns(2)
    date = c1.date_input("Date", d_date)
    venue = c1.text_input("Venue", d_venue)
    crep = c1.text_input("Client Reps", st.session_state.get('crep',''))
    absent = c1.text_input("Absent")
    
    time_str = c2.text_input("Time", d_time)
    prep = c2.text_input("Prepared By", st.session_state.user_real_name)
    irep = c2.text_input("iFoundries Reps", st.session_state.get('irep',''))
    
    st.subheader("Content")
    overview = st.text_area("Overview", st.session_state.ai_results.get("overview", ""))
    disc = st.text_area("Discussion", st.session_state.ai_results.get("discussion", ""), height=300)
    next_s = st.text_area("Next Steps", st.session_state.ai_results.get("next_steps", ""), height=150)
    
    st.divider()
    do_d = st.checkbox("Drive", True)
    do_b = st.checkbox("Basecamp")
    
    pid, tool, tid, subid, btitle, bdesc = None, None, None, None, "", ""
    
    if do_b:
        sess = get_basecamp_session_user()
        projs = get_basecamp_projects(sess)
        pname = st.selectbox("Project", [p[0] for p in projs])
        if pname:
            pid = next(p[1] for p in projs if p[0]==pname)
            tool = st.selectbox("Tool", ["To-dos", "Message Board", "Docs & Files"])
            dock = get_project_tools(sess, pid)
            
            if tool == "To-dos":
                tid = next((t['id'] for t in dock if t['name']=='todoset'), None)
                lists = get_todolists(sess, tid, pid)
                lname = st.selectbox("List", [l[0] for l in lists])
                if lname: subid = next(l[1] for l in lists if l[0]==lname)
                btitle = st.text_input("Title", f"Minutes - {date}")
                bdesc = st.text_area("Desc", "Attached.")
            
            elif tool == "Message Board":
                tid = next((t['id'] for t in dock if t['name']=='message_board'), None)
                btitle = st.text_input("Subject", f"Minutes - {date}")
                bdesc = st.text_area("Body", "Attached.")
                
            elif tool == "Docs & Files":
                tid = next((t['id'] for t in dock if t['name']=='vault'), None)
                btitle = st.text_input("File Name", f"Minutes_{date}.docx")

    if st.button("Generate"):
        doc = Document("Minutes Of Meeting - Template.docx")
        
        # 1. Title Replace
        for p in doc.paragraphs: 
            if "[Title]" in p.text: p.text = p.text.replace("[Title]", st.session_state.get("detected_title", "Meeting"))
            
        # 2. Header Table
        t = doc.tables[0]
        t.cell(1,1).text = str(date)
        t.cell(2,1).text = str(time_str)
        t.cell(3,1).text = venue
        t.cell(4,1).text = crep
        t.cell(4,2).text = irep
        t.cell(5,1).text = absent
        
        # 3. Content Table
        t2 = doc.tables[1]
        t2.cell(1,1).text = overview # Overview in Green Box
        add_formatted_text(t2.cell(2,1), disc)
        add_formatted_text(t2.cell(4,1), next_s)
        
        # 4. Adjourned Time
        try: end_t = time_str.split('-')[1].strip()
        except: end_t = "Unknown"
        t2.cell(5,1).text = f"Meeting adjourned at {end_t}"
        
        doc.paragraphs[-1].text = f"Prepared by: {prep}"
        
        b = io.BytesIO(); doc.save(b); b.seek(0)
        fn = f"{st.session_state.get('detected_title','Minutes')}_{date}.docx"
        
        if do_d: upload_to_drive_user(b, fn, "Meeting Notes")
        if do_b and pid:
            sgid = upload_bc_attachment(sess, b.getvalue(), fn)
            post_to_bc(sess, pid, tool, tid, subid, btitle, bdesc, sgid)
            
        st.success("Done!")

with tab3:
    st.header("Chat")
    
    # Save Chat Logic
    if st.button("💾 Save Chat to Drive"):
        d = Document()
        d.add_heading(f"Chat Log - {datetime.date.today()}", 0)
        for m in st.session_state.chat_history:
            p = d.add_paragraph()
            role = "AI" if m['role'] == 'assistant' else "User"
            p.add_run(f"{role}: ").bold = True
            add_markdown_to_doc(d, m['content']) # Smart Markdown Parsing
            d.add_paragraph("_"*30)
        
        b = io.BytesIO(); d.save(b); b.seek(0)
        upload_to_drive_user(b, f"Chat_{st.session_state.get('detected_title','Log')}.docx", "Chats")
        st.success("Saved!")

    # Chat Loop
    for m in st.session_state.chat_history:
        st.chat_message(m["role"]).markdown(m["content"])
        
    if p := st.chat_input():
        st.session_state.chat_history.append({"role":"user", "content":p})
        st.chat_message("user").markdown(p)
        
        with st.chat_message("assistant"):
            # Stream logic
            def str_gen():
                prompt = f"""
                Context: {st.session_state.saved_participants}
                Transcript: {st.session_state.ai_results.get('full_transcript','')}
                Question: {p}
                Rules: Be professional. Use names. Be concise.
                """
                for chunk in gemini_model.generate_content(prompt, stream=True):
                    if chunk.text: yield chunk.text
            
            full_resp = st.write_stream(str_gen())
            st.session_state.chat_history.append({"role":"assistant", "content":full_resp})

with tab4:
    st.header("History")
    if st.button("Refresh"): st.rerun()
    
    files = list_past_meetings()
    if files:
        sel = st.selectbox("Select Meeting", [f['name'] for f in files])
        if st.button("Load"):
            fid = next(f['id'] for f in files if f['name'] == sel)
            data = load_meeting_data(fid)
            if data:
                st.session_state.ai_results = data.get("ai_results", {})
                st.session_state.saved_participants_input = data.get("participants", "")
                
                # RESTORE CHAT HISTORY
                st.session_state.chat_history = data.get("chat_history", [])
                
                # Restore Reps
                p_input = data.get("participants", "")
                c_list = [l.replace("(Client)","").strip() for l in p_input.split('\n') if "(Client)" in l]
                i_list = [l.replace("(iFoundries)","").strip() for l in p_input.split('\n') if "(iFoundries)" in l]
                st.session_state.auto_client_reps = "\n".join(c_list)
                st.session_state.auto_ifoundries_reps = ", ".join(i_list)
                
                st.success("Loaded! Check Tab 2 and 3.")
