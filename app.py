import streamlit as st
import tempfile
import os
from docx import Document
import io
import time
import subprocess
import pickle
import json # New import

# Import Google Cloud Libraries
from google.cloud import speech
from google.cloud import storage
import google.generativeai as genai

# Import Google Auth & Drive Libraries
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from google.oauth2 import service_account

# --- NEW: Import Basecamp & formatting tools ---
import requests
from requests_oauthlib import OAuth2Session
from docx.shared import Pt, Inches

# -----------------------------------------------------
# 1. CONSTANTS & CONFIGURATION
#    (We now load these from st.secrets)
# -----------------------------------------------------

# --- Google Config ---
GCS_BUCKET_NAME = st.secrets.get("GCS_BUCKET_NAME", "default-bucket-name")
DRIVE_FOLDER_ID = st.secrets.get("DRIVE_FOLDER_ID", "default-folder-id")
GOOGLE_API_KEY = st.secrets["GOOGLE_API_KEY"]

# --- Basecamp Config ---
BASECAMP_ACCOUNT_ID = st.secrets["BASECAMP_ACCOUNT_ID"]
BASECAMP_CLIENT_ID = st.secrets["BASECAMP_CLIENT_ID"]
BASECAMP_CLIENT_SECRET = st.secrets["BASECAMP_CLIENT_SECRET"]
YOUR_PERMANENT_REFRESH_TOKEN = st.secrets["BASECAMP_REFRESH_TOKEN"]

# Basecamp API URLs
BASECAMP_TOKEN_URL = "https://launchpad.37signals.com/authorization/token"
BASECAMP_API_BASE = f"https://3.basecampapi.com/{BASECAMP_ACCOUNT_ID}"
BASECAMP_USER_AGENT = {"User-Agent": "AI Meeting Notes App (your-email@example.com)"}


# -----------------------------------------------------
# 2. API CLIENTS SETUP (NOW USING ST.SECRETS)
# -----------------------------------------------------
try:
    # Get the service account JSON from secrets
    # st.secrets["GCP_SERVICE_ACCOUNT_JSON"] will be the full text of your credentials.json
    sa_creds_info = json.loads(st.secrets["GCP_SERVICE_ACCOUNT_JSON"])
    sa_creds = service_account.Credentials.from_service_account_info(sa_creds_info)
    
    storage_client = storage.Client(credentials=sa_creds)
    speech_client = speech.SpeechClient(credentials=sa_creds)
except Exception as e:
    st.error(f"FATAL ERROR: Could not load Google Cloud credentials from secrets. Error: {e}")
    st.stop()

try:
    genai.configure(api_key=GOOGLE_API_KEY)
    gemini_model = genai.GenerativeModel('gemini-flash-latest')
except Exception as e:
    st.error(f"Error initializing Gemini. Is your GOOGLE_API_KEY correct? Error: {e}")
    st.stop()

# --- NEW Google Drive Service (using Refresh Token) ---
@st.cache_resource
def get_drive_service():
    """
    This function uses a permanent refresh token from st.secrets to get
    Google Drive credentials.
    """
    try:
        # Get client secret info and refresh token from secrets
        client_config_str = st.secrets["GDRIVE_CLIENT_SECRET_JSON"] # This will be the full text of client_secret.json
        client_config = json.loads(client_config_str)
        refresh_token = st.secrets["GDRIVE_REFRESH_TOKEN"]

        # Create credentials object
        creds = Credentials.from_authorized_user_info(
            {
                "client_id": client_config["web"]["client_id"],
                "client_secret": client_config["web"]["client_secret"],
                "refresh_token": refresh_token,
                "token_uri": client_config["web"]["token_uri"],
            }
        )
        
        # Refresh the credentials if they are expired
        if not creds.valid:
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                st.error("Error with Google Drive credentials. Please re-generate refresh token.")
                st.stop()

        return build("drive", "v3", credentials=creds)
    except Exception as e:
        st.error(f"FATAL ERROR: Could not load Google Drive credentials. Error: {e}")
        st.stop()

drive_service = get_drive_service()


# --- NEW: Basecamp Service (Refresh Token Method) ---
@st.cache_resource
def get_basecamp_session():
    """
    This function uses a permanent refresh token from st.secrets to get a new
    access token every time the app starts.
    """
    # We use a temporary file path for the pickle in the cloud
    token_pickle_path = "/tmp/basecamp_token.pickle"
    token = None
    
    if os.path.exists(token_pickle_path):
        with open(token_pickle_path, "rb") as f:
            token = pickle.load(f)

    if token and time.time() < token.get("expires_at", 0):
        session = OAuth2Session(BASECAMP_CLIENT_ID, token=token)
        session.headers.update(BASECAMP_USER_AGENT)
        return session

    st.info("Refreshing Basecamp authorization...")
    try:
        oauth = OAuth2Session(BASECAMP_CLIENT_ID)
        
        new_token = oauth.refresh_token(
            BASECAMP_TOKEN_URL, 
            client_id=BASECAMP_CLIENT_ID,
            client_secret=BASECAMP_CLIENT_SECRET,
            refresh_token=YOUR_PERMANENT_REFRESH_TOKEN,
            type="refresh"
        )
        
        with open(token_pickle_path, "wb") as f:
            pickle.dump(new_token, f)
            
        session = OAuth2Session(BASECAMP_CLIENT_ID, token=new_token)
        session.headers.update(BASECAMP_USER_AGENT)
        st.success("Basecamp is connected.")
        return session
        
    except Exception as e:
        st.error(f"Error refreshing Basecamp token: {e}")
        st.stop()

# -----------------------------------------------------
# 3. HELPER FUNCTIONS (Unchanged)
# -----------------------------------------------------

def upload_to_gcs(file_path, destination_blob_name):
    try:
        bucket = storage_client.bucket(GCS_BUCKET_NAME)
        blob = bucket.blob(destination_blob_name)
        blob.upload_from_filename(file_path, timeout=3600)
        return f"gs://{GCS_BUCKET_NAME}/{destination_blob_name}"
    except Exception as e:
        st.error(f"GCS Upload Error: {e}")
        return None

def upload_to_drive(file_stream, file_name):
    try:
        file_metadata = {"name": file_name, "parents": [DRIVE_FOLDER_ID]}
        media = MediaIoBaseUpload(
            file_stream, mimetype="application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        )
        file = drive_service.files().create(
            body=file_metadata, media_body=media, fields="id"
        ).execute()
        return file.get("id")
    except Exception as e:
        st.error(f"Google Drive Upload Error: {e}")
        return None

@st.cache_data(ttl=600)
def get_basecamp_projects(_session):
    if not _session: return []
    try:
        response = _session.get(f"{BASECAMP_API_BASE}/projects.json")
        response.raise_for_status()
        projects = response.json()
        return sorted([(p['name'], p['id']) for p in projects if p['status'] == 'active'], key=lambda x: x[0])
    except Exception as e:
        st.error(f"Error fetching Basecamp projects: {e}")
        return []

@st.cache_data(ttl=600)
def get_basecamp_todolists(_session, project_id):
    if not _session or not project_id: return []
    try:
        project_response = _session.get(f"{BASECAMP_API_BASE}/projects/{project_id}.json")
        project_response.raise_for_status()
        project_data = project_response.json()
        
        todoset_id = None
        for tool in project_data.get('dock', []):
            if tool.get('name') == 'todoset' and tool.get('enabled') == True:
                todoset_id = tool.get('id')
                break
        
        if not todoset_id:
            st.error("Could not find an enabled 'To-do' list tool in this project.")
            return []

        todolists_url = f"{BASECAMP_API_BASE}/buckets/{project_id}/todosets/{todoset_id}/todolists.json"
        
        response = _session.get(todolists_url)
        response.raise_for_status()
        todolists = response.json()
        
        return sorted([(tl['title'], tl['id']) for tl in todolists], key=lambda x: x[0])
    
    except Exception as e:
        st.error(f"Error fetching Basecamp to-do lists: {e}")
        return []

def upload_bc_attachment(_session, file_bytes, file_name):
    if not _session: return None
    try:
        headers = _session.headers.copy()
        headers['Content-Type'] = 'application/octet-stream'
        headers['Content-Length'] = str(len(file_bytes))

        upload_response = _session.post(
            f"{BASECAMP_API_BASE}/attachments.json?name={file_name}",
            data=file_bytes,
            headers=headers
        )
        upload_response.raise_for_status()
        response_json = upload_response.json()
        return response_json['attachable_sgid']
    
    except KeyError:
        st.error("Basecamp Upload Error: 'attachable_sgid' key not found in response.")
        st.json(upload_response.json()) 
        return None
    except Exception as e:
        st.error(f"Basecamp Upload Error: {e}")
        return None

def create_bc_todo(_session, project_id, todolist_id, title, attachment_sgid):
    if not _session: return False
    try:
        content_html = title
        description_html = "" 
        
        if attachment_sgid:
            attachment_html = f'<bc-attachment sgid="{attachment_sgid}"></bc-attachment>'
            description_html = attachment_html 

        payload = {
            "content": content_html,
            "description": description_html,
        }
        
        url = f"{BASECAMP_API_BASE}/buckets/{project_id}/todolists/{todolist_id}/todos.json"
        
        response = _session.post(url, json=payload)
        response.raise_for_status()
        return True
    except Exception as e:
        st.error(f"Basecamp To-Do Creation Error: {e}")
        st.error("Full Error:" + str(e))
        return False

def add_formatted_text(cell, text):
    """
    Parses the AI's text and applies formatting (underline, bullets)
    to the Word document cell.
    """
    cell.text = ""
    
    lines = text.split('\n')
    
    for line in lines:
        line = line.strip()
        if not line:
            continue

        p = cell.add_paragraph()
        p.paragraph_format.space_before = Pt(0)
        p.paragraph_format.space_after = Pt(0)
        
        # Check for Section Title (e.g., ## DISCUSSION ##)
        if line.startswith('##') and line.endswith('##'):
            clean_title = line.strip('#').strip()
            run = p.add_run(clean_title)
            run.underline = True
            run.bold = False # Per your request
            p.paragraph_format.space_before = Pt(12)
            p.paragraph_format.space_after = Pt(4)
        
        # Check for a bullet point (e.g., * The point...)
        elif line.startswith('*'):
            clean_text = line.lstrip('*').lstrip("•").strip() # This is good
            
            # --- THIS IS THE FIX for **Title:** ---
            # Check for the bold prefix format (e.g., "**Content:** Text...")
            if clean_text.startswith('**') and ':**' in clean_text:
                try:
                    # Split at the ':**'
                    parts = clean_text.split(':**', 1)
                    # Title is part[0], removing the opening '**'
                    title = parts[0].lstrip('**').strip()
                    # Text is the rest
                    text = parts[1].strip()
                    
                    # Add the bullet symbol first, without indentation
                    p.text = "•\t"
                    
                    # Add the BOLD title
                    run = p.add_run(f"{title}: ")
                    run.bold = True
                    
                    # Add the rest of the text (not bold)
                    p.add_run(text)
                    
                    # Indent the whole paragraph
                    p.paragraph_format.left_indent = Inches(0.25)

                except Exception as e:
                    # Fallback for weird formatting
                    p.text = f"•\t{clean_text}"
                    p.paragraph_format.left_indent = Inches(0.25)
            
            # Original logic for plain bullets
            elif clean_text:
                p.text = f"•\t{clean_text}" 
                p.paragraph_format.left_indent = Inches(0.25)
            # --- END OF FIX ---
        
        # Handle plain text
        else:
            p.add_run(line)


# -----------------------------------------------------
# 4. THE MAIN AI FUNCTION
# -----------------------------------------------------
def get_structured_notes_google(audio_file_path, file_name, participants_context):
    flac_file_path = ""
    flac_blob_name = ""
    try:
        # 1. Convert to FLAC
        with st.spinner(f"Converting {file_name} to audio-only FLAC..."):
            base_name = os.path.splitext(audio_file_path)[0]
            flac_file_path = f"{base_name}.flac"
            command = ["ffmpeg", "-i", audio_file_path, "-vn", "-acodec", "flac", "-y", flac_file_path]
            subprocess.run(command, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # 2. Upload to GCS
        with st.spinner(f"Uploading converted audio to Google Cloud Storage..."):
            flac_blob_name = f"{os.path.splitext(file_name)[0]}.flac"
            gcs_uri = upload_to_gcs(flac_file_path, flac_blob_name)
            if not gcs_uri: return {"error": "Upload failed."}

        # 3. Transcribe
        progress_text = "Transcribing & identifying speakers: 0% Complete"
        progress_bar = st.progress(0, text=progress_text)
        
        audio = speech.RecognitionAudio(uri=gcs_uri)
        config = speech.RecognitionConfig(
            encoding=speech.RecognitionConfig.AudioEncoding.FLAC,
            language_code="en-US",
            enable_automatic_punctuation=True,
            use_enhanced=True,
            model="video",
            diarization_config=speech.SpeakerDiarizationConfig(
                enable_speaker_diarization=True,
                min_speaker_count=2,
                max_speaker_count=6
            )
        )
        operation = speech_client.long_running_recognize(config=config, audio=audio)
        
        while not operation.done():
            metadata = operation.metadata
            if metadata and metadata.progress_percent:
                percent_complete = metadata.progress_percent
                progress_text = f"Transcribing: {percent_complete}% Complete"
                progress_bar.progress(percent_complete, text=progress_text)
            time.sleep(5)

        progress_bar.progress(100, text="Transcription: 100% Complete")
        response = operation.result(timeout=3600)
        progress_bar.empty()

        if not response.results:
             return {"error": "Transcription failed. The audio might be silent."}

        result = response.results[-1]
        words_info = result.alternatives[0].words
        full_transcript_text = ""
        current_speaker = -1
        for word_info in words_info:
            if word_info.speaker_tag != current_speaker:
                current_speaker = word_info.speaker_tag
                full_transcript_text += f"\n\nSpeaker {current_speaker}: "
            full_transcript_text += word_info.word + " "
        if not full_transcript_text.strip():
            full_transcript_text = " ".join([result.alternatives[0].transcript for result in response.results])

        # 4. Summarize (The Smart Part)
        with st.spinner("Analyzing conversation & matching names..."):
            
            prompt = f"""
            You are an expert meeting secretary. 
            
            Here is the context of who was in the meeting:
            {participants_context}
            
            The transcript below uses "Speaker 1", "Speaker 2", etc.
            Your job is to figure out which Speaker matches which Name from the list above.
            
            Transcript:
            {full_transcript_text}
            
            ---
            YOUR TASKS:
            
            1. RECONSTRUCTION: When writing the notes, DO NOT use "Speaker 1". Use their REAL NAMES (e.g., "John said...").
            
            2. EXTRACTION:
            
            ## DISCUSSION ##
            Summarize main points using the real names.
            FORMAT:
            ## Section Title (e.g., ## Content and Grammar)
            * **Wording & Tone:** John requested avoiding the casual use of "You are".
            * **Navigation:** John insisted that the entire product image be clickable.
            * Bullet point 3.
            
            (Leave a blank line between sections)
            
            ## NEXT STEPS ##
            List action items using the real names.
            FORMAT:
            * Bullet point 1.
            * Bullet point 2.
            
            ## CLIENT REQUESTS ##
            List specific questions or requests asked BY the Client.
            FORMAT:
            * Bullet point 1.
            """
            
            response = gemini_model.generate_content(prompt)
            text = response.text
            
            # Robust Parsing
            discussion = ""
            next_steps = ""
            client_reqs = ""
            try:
                if "## DISCUSSION ##" in text:
                    discussion_start = text.find("## DISCUSSION ##")
                    if "## NEXT STEPS ##" in text:
                        discussion_end = text.find("## NEXT STEPS ##")
                        discussion = text[discussion_start:discussion_end].strip()
                    else:
                        discussion = text[discussion_start:].strip()
                
                if "## NEXT STEPS ##" in text:
                    next_steps_start = text.find("## NEXT STEPS ##")
                    if "## CLIENT REQUESTS ##" in text:
                        next_steps_end = text.find("## CLIENT REQUESTS ##")
                        next_steps = text[next_steps_start:next_steps_end].strip()
                    else:
                        next_steps = text[next_steps_start:].strip()
                
                if "## CLIENT REQUESTS ##" in text:
                    client_reqs_start = text.find("## CLIENT REQUESTS ##")
                    client_reqs = text[client_reqs_start:].strip()
                
                if not discussion and not next_steps:
                    discussion = text
                    
            except Exception as e:
                st.error(f"Error parsing AI response: {e}")
                discussion = text
                next_steps = "Parsing failed."
                client_reqs = "Parsing failed."

            return {
                "discussion": discussion,
                "next_steps": next_steps,
                "client_reqs": client_reqs,
                "full_transcript": full_transcript_text
            }
    
    except Exception as e:
        if 'progress_bar' in locals(): progress_bar.empty()
        return {"error": str(e)}
    finally:
        # --- 5. Clean up ---
        try:
            if os.path.exists(audio_file_path): os.remove(audio_file_path)
            if os.path.exists(flac_file_path): os.remove(flac_file_path)
            bucket = storage_client.bucket(GCS_BUCKET_NAME)
            blob = bucket.blob(flac_blob_name)
            blob.delete()
        except: pass

# -----------------------------------------------------
# 5. STREAMLIT UI
# -----------------------------------------------------
st.set_page_config(layout="wide")
st.title("🤖 AI Meeting Minutes (Smart Name Recognition)")

if 'ai_results' not in st.session_state:
    st.session_state.ai_results = {"discussion": "", "next_steps": "", "client_reqs": "", "full_transcript": ""}

col1, col2 = st.columns([1, 2])

with col1:
    st.header("1. Analyze Audio")
    
    participants_input = st.text_area(
        "Known Participants (Teach the AI)", 
        value="Client's Exact Name (Client)\niFoundries Exact Name (iFoundries)",
        help="The AI will read this to match 'Speaker 1' to these names."
    )
    
    uploaded_file = st.file_uploader("Upload Meeting (MP3/MP4)", type=["mp3", "mp4", "m4a", "wav"])
    
    if st.button("Analyze Audio"):
        if uploaded_file:
            with tempfile.NamedTemporaryFile(delete=False, suffix=f".{uploaded_file.name}") as tmp:
                tmp.write(uploaded_file.getvalue())
                path = tmp.name
            
            res = get_structured_notes_google(path, uploaded_file.name, participants_input)
            
            if "error" in res:
                st.error(res["error"])
            else:
                st.session_state.ai_results = res
                st.success("Done! Review the named notes on the right.")
        else:
            st.warning("Please upload a file first.")

with col2:
    st.header("2. Review Notes")
    
    st.subheader("Manual Fields (For .docx Template)")
    row1_col1, row1_col2 = st.columns(2)
    with row1_col1:
        date = st.text_input("Date :red[*]")
        venue = st.text_input("Venue")
        client_rep = st.text_area("Client Reps :red[*]", height=70)
        absent = st.text_input("Absent")
    with row1_col2:
        time_str = st.text_input("Time") 
        prepared_by = st.text_input("Prepared by :red[*]")
        ifoundries_rep = st.text_input("iFoundries Reps")

    st.subheader("AI Generated Content")
    st.caption("Review and edit the AI's draft below. This content will be formatted in the Word doc.")
    
    discussion_text = st.text_area(
        "Discussion", 
        value=st.session_state.ai_results.get("discussion", ""), 
        height=300
    )
    next_steps_text = st.text_area(
        "Next Steps", 
        value=st.session_state.ai_results.get("next_steps", ""), 
        height=200
    )
    
    with st.expander("View Specific Client Requests (For Reference)"):
        st.text_area("Client Requests", value=st.session_state.ai_results.get("client_reqs", ""), height=150)
    
    st.header("3. Generate & Upload")
    
    # --- NEW: Basecamp UI ---
    
    # We create variables to hold the selected Basecamp values
    bc_session = None
    bc_project_id = None
    bc_todolist_id = None
    bc_todo_title = ""

    do_drive = st.checkbox("Upload to Drive", value=True)
    do_basecamp = st.checkbox("Upload to Basecamp") # <-- NEW

    if do_basecamp:
        # This will call the new refresh token function
        bc_session = get_basecamp_session()
        
        if bc_session:
            try:
                projects_list = get_basecamp_projects(bc_session)
                if not projects_list:
                    st.warning("No active Basecamp projects found.")
                else:
                    selected_project_name = st.selectbox(
                        "Select Basecamp Project",
                        options=[p[0] for p in projects_list],
                        index=None,
                        placeholder="Choose a project..."
                    )
                    
                    if selected_project_name:
                        bc_project_id = next(p[1] for p in projects_list if p[0] == selected_project_name)
                        todolists_list = get_basecamp_todolists(bc_session, bc_project_id)
                        
                        if not todolists_list:
                            st.warning("No to-do lists found in this project. (Or the tool is disabled)")
                        else:
                            selected_todolist_name = st.selectbox(
                                "Select To-Do List",
                                options=[tl[0] for tl in todolists_list], # Use title for name
                                index=None,
                                placeholder="Choose a to-do list..."
                            )
                            
                            if selected_todolist_name:
                                bc_todolist_id = next(tl[1] for tl in todolists_list if tl[0] == selected_todolist_name) # Use id
                                bc_todo_title = st.text_input("To-Do Title :red[*]")
                                
                                # --- THIS IS THE STATIC NOTIFICATION YOU REQUESTED ---
                                if date:
                                    default_fname = f"Minutes_{date}.docx"
                                    st.info(f"📎 {default_fname} will be attached to the 'Notes' of this to-do.")
                                else:
                                    st.info("📎 The generated .docx will be attached to the 'Notes' of this to-do. (Please fill in the 'Date' field to see the filename).")
            
            except Exception as e:
                st.error(f"Error loading Basecamp data: {e}")
                st.info("This may be the network error. Please try again.")

    # --- END NEW UI ---

    if st.button("Generate Word Doc"):
        
        # --- NEW: Basecamp Validation ---
        basecamp_ready = True
        if do_basecamp:
            if not bc_session:
                st.error("Basecamp is not connected. Please check your refresh token and network.")
                basecamp_ready = False
            if not bc_project_id or not bc_todolist_id:
                st.error("Please select a Basecamp project and to-do list.")
                basecamp_ready = False
            if not bc_todo_title:
                st.error("Please enter a Basecamp To-Do Title.")
                basecamp_ready = False
        # --- END NEW VALIDATION ---

        if not date or not prepared_by or not client_rep:
            st.error("Missing required fields (*)")
        elif not do_basecamp or basecamp_ready: # <-- MODIFIED Check
            try:
                # We must have the .docx template file in the same folder as app.py
                doc = Document("Minutes Of Meeting - Template.docx")
                t0 = doc.tables[0]
                t0.cell(1,1).text = date
                t0.cell(2,1).text = time_str
                t0.cell(3,1).text = venue
                
                # --- THIS IS THE FIX for Rep Names ---
                t0.cell(4,1).text = f"{client_rep} (Client)" if client_rep else ""
                t0.cell(4,2).text = f"{ifoundries_rep} (iFoundries)" if ifoundries_rep else ""
                # --- END OF FIX ---
                
                t0.cell(5,1).text = absent

                t1 = doc.tables[1]
                
                # --- Use the smart formatting function ---
                add_formatted_text(t1.cell(2,1), discussion_text)
                add_formatted_text(t1.cell(4,1), next_steps_text)
                
                doc.paragraphs[-1].text = f"Prepared by: {prepared_by}"
                
                bio = io.BytesIO()
                doc.save(bio)
                bio.seek(0)
                fname = f"Minutes_{date}.docx"
                
                if do_drive:
                    with st.spinner("Uploading to Drive..."):
                        if upload_to_drive(bio, fname):
                            st.success("Uploaded to Drive!")
                        else:
                            st.error("Drive upload failed.")
                    bio.seek(0) # Rewind buffer for next upload

                # --- NEW: Basecamp Upload Logic ---
                if do_basecamp and basecamp_ready and bc_session:
                    with st.spinner(f"Uploading {fname} to Basecamp..."):
                        # 1. Upload attachment
                        file_bytes = bio.getvalue()
                        sgid = upload_bc_attachment(bc_session, file_bytes, fname)
                    
                    if sgid:
                        # --- REMOVED the dynamic "Attached" notification ---
                        
                        with st.spinner("Creating To-Do in Basecamp..."):
                            # 2. Create to-do
                            if create_bc_todo(bc_session, bc_project_id, bc_todolist_id, bc_todo_title, sgid):
                                # This is now the only success message
                                st.success("Created To-Do in Basecamp!")
                            else:
                                st.error("Basecamp to-do creation failed.")
                    else:
                        st.error("Basecamp file upload failed.")
                    bio.seek(0) # Rewind for download button
                # --- END NEW UPLOAD LOGIC ---

                st.download_button("Download .docx", bio, fname)
                
                with st.expander("Full Transcript"):
                    st.text(st.session_state.ai_results.get("full_transcript", ""))

            except Exception as e:
                st.error(f"Error generating document: {e}")
