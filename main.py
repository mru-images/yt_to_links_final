from fastapi import FastAPI, HTTPException, Query
import requests, json, os, re
from io import BytesIO
from supabase import create_client, Client

# --- 🔐 HARDCODED CREDENTIALS ---
PCLOUD_AUTH_TOKEN = "fE93KkZMjhg7ZtHMudQY9CHj5m8MDH3CFxLEKsw1y"
SUPABASE_URL = "https://yssrurhhizdcmctxrxec.supabase.co"
SUPABASE_KEY = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Inlzc3J1cmhoaXpkY21jdHhyeGVjIiwicm9sZSI6ImFub24iLCJpYXQiOjE3NTEyOTUzNTUsImV4cCI6MjA2Njg3MTM1NX0.h3x6OjrCWKaKR7CHNfA7dl_bnmmMj6AmmNWhWW6mpo4"
GEMINI_API_KEY = "AIzaSyCGcpIzYiPhFIB8YiQtZNmGYTUtXQCFOoE"
RAPIDAPI_HOST = "youtube-mp36.p.rapidapi.com"
RAPIDAPI_KEY = "0204f09445msh6e8d74df8ff070bp1b4c6ejsn8a38abc65dfc"

SONGS_FOLDER = "songs_test"
IMGS_FOLDER = "imgs_test"

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
app = FastAPI()

# --- Utilities ---
def extract_video_id(url: str):
    url = url.split("?")[0]
    if "youtu.be/" in url:
        return url.split("youtu.be/")[-1]
    elif "watch?v=" in url:
        return url.split("watch?v=")[-1]
    else:
        raise ValueError("Invalid YouTube URL")

def sanitize_title(title: str):
    safe_title = re.sub(r'[\\/*?:"<>|#]', '', title)
    safe_title = re.sub(r'\s+', ' ', safe_title).strip()
    return safe_title[:100]

def get_or_create_folder(folder_name):
    res = requests.get("https://api.pcloud.com/listfolder", params={"auth": PCLOUD_AUTH_TOKEN, "folderid": 0})
    for item in res.json().get("metadata", {}).get("contents", []):
        if item.get("isfolder") and item.get("name") == folder_name:
            return item["folderid"]
    res = requests.get("https://api.pcloud.com/createfolder", params={"auth": PCLOUD_AUTH_TOKEN, "name": folder_name, "folderid": 0})
    return res.json()["metadata"]["folderid"]

def upload_file_stream(file_stream, filename, folder_id):
    res = requests.post(
        "https://api.pcloud.com/uploadfile",
        params={"auth": PCLOUD_AUTH_TOKEN, "folderid": folder_id},
        files={"file": (filename, file_stream)}
    )
    return res.json()["metadata"][0]["fileid"]

def download_mp3_stream(download_url):
    response = requests.get(download_url, stream=True, allow_redirects=True)
    
    print("Status Code:", response.status_code)
    print("Content-Type:", response.headers.get("Content-Type"))
    print("First 100 bytes:", response.content[:100])  # Caution: Don't print full binary

    if response.status_code != 200 or 'audio' not in response.headers.get("Content-Type", ""):
        raise Exception(f"Invalid MP3 content received. Status: {response.status_code}, Content-Type: {response.headers.get('Content-Type')}")
    
    buffer = BytesIO()
    for chunk in response.iter_content(chunk_size=8192):
        if chunk:
            buffer.write(chunk)
    buffer.seek(0)
    return buffer


def download_thumbnail_stream(video_id):
    qualities = ["maxresdefault", "hqdefault", "mqdefault", "default"]
    for quality in qualities:
        url = f"https://img.youtube.com/vi/{video_id}/{quality}.jpg"
        res = requests.get(url)
        if res.status_code == 200:
            return BytesIO(res.content)
    raise Exception("Thumbnail not found.")

def get_tags_from_gemini(song_name):
    PREDEFINED_TAGS = {
        "genre": ["pop", "rock", "hiphop", "rap", "r&b", "jazz", "blues", "classical", "electronic", "edm", "house", "techno", "trance", "dubstep", "lofi", "indie", "folk", "country", "metal", "reggae", "latin", "kpop", "jpop", "bhajan", "devotional", "sufi", "instrumental", "soundtrack", "acoustic", "chillstep", "ambient"],
        "mood": ["happy", "sad", "romantic", "chill", "energetic", "dark", "peaceful", "motivational", "angry", "nostalgic", "dreamy", "emotional", "fun", "relaxing", "aggressive", "uplifting", "sensual", "dramatic", "lonely", "hopeful", "spiritual"],
        "occasion": ["party", "workout", "study", "sleep", "meditation", "travel", "roadtrip", "driving", "wedding", "breakup", "background", "cooking", "cleaning", "gaming", "focus", "night", "morning", "rainy_day", "summer_vibes", "monsoon_mood"],
        "era": ["80s", "90s", "2000s", "2010s", "2020s", "oldschool", "vintage", "retro", "modern", "trending", "classic", "timeless", "underground", "viral"],
        "vocal_instrument": ["female_vocals", "male_vocals", "duet", "group", "instrumental_only", "beats_only", "piano", "guitar", "violin", "flute", "drums", "orchestra", "bass", "live", "remix", "acoustic_version", "cover_song", "mashup", "karaoke"]
    }

    prompt = f"""
Given the song name "{song_name}", identify its primary artist and language.
Then, suggest appropriate tags from the predefined categories below.
Use ONLY tags from these predefined lists.
Return in this JSON format:
{{
  "artist": "Artist Name",
  "language": "Language",
  "genre": [...],
  "mood": [...],
  "occasion": [...],
  "era": [...],
  "vocal_instrument": [...]
}}
Predefined:
{json.dumps(PREDEFINED_TAGS, indent=2)}
"""
    res = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}",
        headers={"Content-Type": "application/json"},
        data=json.dumps({"contents": [{"parts": [{"text": prompt}]}]})
    )
    if res.status_code != 200:
        raise Exception("Gemini API failed.")
    text = res.json()["candidates"][0]["content"]["parts"][0]["text"]
    if text.startswith("```json"):
        text = text.strip("` \n").replace("json", "", 1).strip()
    parsed = json.loads(text)
    tags = []
    for key in ["genre", "mood", "occasion", "era", "vocal_instrument"]:
        tags += parsed.get(key, [])
    return {
        "artist": parsed.get("artist", "Unknown"),
        "language": parsed.get("language", "english"),
        "tags": tags
    }

# --- API Endpoint ---
@app.get("/process")
def process_song(link: str = Query(..., description="YouTube video URL")):
    try:
        video_id = extract_video_id(link)
        rapid = requests.get(
            f"https://{RAPIDAPI_HOST}/dl",
            headers={"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": RAPIDAPI_HOST},
            params={"id": video_id}
        ).json()

        title_raw = rapid.get("title", "downloaded_song")
        title = sanitize_title(title_raw)
        download_url = rapid.get("link")
        if not download_url:
            raise Exception("MP3 link not found.")

        # Download MP3 and thumbnail to memory
        mp3_stream = download_mp3_stream(download_url)
        thumb_stream = download_thumbnail_stream(video_id)

        # Upload to pCloud
        song_folder_id = get_or_create_folder(SONGS_FOLDER)
        img_folder_id = get_or_create_folder(IMGS_FOLDER)
        file_id = upload_file_stream(mp3_stream, f"{title}.mp3", song_folder_id)
        img_id = upload_file_stream(thumb_stream, f"{title}.jpg", img_folder_id)
        
        pub = requests.get("https://api.pcloud.com/getfilepublink", params={
            "auth": PCLOUD_AUTH_TOKEN,
            "fileid": file_id
        })
        if pub.status_code != 200 or pub.json().get("result") != 0:
            raise Exception(f"Failed to make MP3 public: {pub.text}")
        
        pub_img = requests.get("https://api.pcloud.com/getfilepublink", params={
            "auth": PCLOUD_AUTH_TOKEN,
            "fileid": img_id
        })
        if pub_img.status_code != 200 or pub_img.json().get("result") != 0:
            raise Exception(f"Failed to make image public: {pub_img.text}")

        # Gemini Metadata
        meta = get_tags_from_gemini(title)

        # Insert into Supabase
        supabase.table("songs").insert({
            "file_id": file_id,
            "img_id": img_id,
            "name": title,
            "artist": meta["artist"],
            "language": meta["language"],
            "tags": meta["tags"],
            "views": 0,
            "likes": 0
        }).execute()

        return {
            "success": True,
            "file_id": file_id,
            "img_id": img_id,
            "name": title,
            "artist": meta["artist"],
            "language": meta["language"],
            "tags": meta["tags"]
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
