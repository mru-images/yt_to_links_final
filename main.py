from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
import requests, json, os
from supabase import create_client, Client
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
load_dotenv()

app = FastAPI()

# Enable CORS for all origins (adjust if needed)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
# Environment Variables
PCLOUD_AUTH_TOKEN = os.getenv("PCLOUD_AUTH_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

SONGS_FOLDER = "songs_test"
IMGS_FOLDER = "imgs_test"
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

app = FastAPI()

# Input model
class SongDownloadData(BaseModel):
    downloadUrl: str
    title: str
    videoId: str

# Helper Functions
def get_or_create_folder(folder_name):
    res = requests.get("https://api.pcloud.com/listfolder", params={"auth": PCLOUD_AUTH_TOKEN, "folderid": 0})
    for item in res.json().get("metadata", {}).get("contents", []):
        if item.get("isfolder") and item.get("name") == folder_name:
            return item["folderid"]
    res = requests.get("https://api.pcloud.com/createfolder", params={"auth": PCLOUD_AUTH_TOKEN, "name": folder_name, "folderid": 0})
    return res.json()["metadata"]["folderid"]

def upload_file(filepath, filename, folder_id):
    with open(filepath, "rb") as f:
        res = requests.post(
            "https://api.pcloud.com/uploadfile",
            params={"auth": PCLOUD_AUTH_TOKEN, "folderid": folder_id},
            files={"file": (filename, f)}
        )
    fileid = res.json()["metadata"][0]["fileid"]
    return fileid

def download_thumbnail(video_id, filename_base):
    qualities = ["maxresdefault", "hqdefault", "mqdefault", "default"]
    for quality in qualities:
        thumb_url = f"https://img.youtube.com/vi/{video_id}/{quality}.jpg"
        response = requests.get(thumb_url)
        if response.status_code == 200:
            thumb_filename = f"/tmp/{filename_base}.jpg"
            with open(thumb_filename, "wb") as f:
                f.write(response.content)
            return thumb_filename
    raise Exception("Thumbnail not found.")

def get_tags_from_gemini(song_name):
    PREDEFINED_TAGS = {
        "genre": ["pop", "rock", "hiphop", "rap", "r&b"],
        "mood": ["happy", "sad", "romantic", "chill", "energetic"],
        "occasion": ["party", "study", "sleep", "travel"],
        "era": ["80s", "90s", "2000s", "2020s"],
        "vocal_instrument": ["male_vocals", "female_vocals", "instrumental"]
    }

    prompt = f"""
Given the song name "{song_name}", return:
{{
  "artist": "Artist Name",
  "language": "Language",
  "genre": [...],
  "mood": [...],
  "occasion": [...],
  "era": [...],
  "vocal_instrument": [...]
}}

Use ONLY from this list:
{json.dumps(PREDEFINED_TAGS, indent=2)}
"""

    response = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}",
        headers={"Content-Type": "application/json"},
        data=json.dumps({"contents": [{"parts": [{"text": prompt}]}]}),
    )

    raw_text = response.json()["candidates"][0]["content"]["parts"][0]["text"]
    if raw_text.startswith("```json"):
        raw_text = raw_text.strip("` \n").replace("json", "", 1).strip()
    parsed = json.loads(raw_text)

    tags = []
    for cat in ["genre", "mood", "occasion", "era", "vocal_instrument"]:
        tags.extend(parsed.get(cat, []))

    return {
        "artist": parsed.get("artist", "Unknown"),
        "language": parsed.get("language", "english"),
        "tags": tags
    }

@app.post("/process-link")
def process_link(data: SongDownloadData):
    mp3_filename = None
    thumb_filename = None
    try:
        title = data.title.replace("/", "-").replace("\\", "-").strip()

        # Download MP3
        mp3_filename = f"/tmp/{title}.mp3"
        mp3_data = requests.get(data.downloadUrl)
        with open(mp3_filename, "wb") as f:
            f.write(mp3_data.content)

        # Download thumbnail
        thumb_filename = download_thumbnail(data.videoId, title)

        # Upload to pCloud
        song_folder_id = get_or_create_folder(SONGS_FOLDER)
        img_folder_id = get_or_create_folder(IMGS_FOLDER)
        file_id = upload_file(mp3_filename, os.path.basename(mp3_filename), song_folder_id)
        img_id = upload_file(thumb_filename, os.path.basename(thumb_filename), img_folder_id)

        # Get tags
        tag_data = get_tags_from_gemini(title)

        # Insert into Supabase
        supabase.table("songs").insert({
            "file_id": file_id,
            "img_id": img_id,
            "name": title,
            "artist": tag_data["artist"],
            "language": tag_data["language"],
            "tags": tag_data["tags"],
            "views": 0,
            "likes": 0
        }).execute()

        return {
            "success": True,
            "file_id": file_id,
            "img_id": img_id,
            "name": title,
            "artist": tag_data["artist"],
            "language": tag_data["language"],
            "tags": tag_data["tags"]
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

    finally:
        for file in [mp3_filename, thumb_filename]:
            if file and os.path.exists(file):
                try:
                    os.remove(file)
                except Exception as cleanup_error:
                    print(f"Failed to delete {file}: {cleanup_error}")

