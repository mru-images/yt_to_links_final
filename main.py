from fastapi import FastAPI, HTTPException, Query
import requests, json, os, re
from io import BytesIO
from supabase import create_client, Client
import yt_dlp

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

# --- Utility Functions ---
def extract_video_id(url: str):
    if "youtu.be/" in url:
        return url.split("youtu.be/")[-1].split("?")[0]
    elif "watch?v=" in url:
        return url.split("watch?v=")[-1].split("&")[0]
    raise ValueError("Invalid YouTube URL")

def sanitize_title(title: str):
    return re.sub(r'[\\/*?:"<>|#]', '', title).strip()[:100]

def get_or_create_folder(name):
    res = requests.get("https://api.pcloud.com/listfolder", params={"auth": PCLOUD_AUTH_TOKEN, "folderid": 0})
    for item in res.json().get("metadata", {}).get("contents", []):
        if item.get("isfolder") and item.get("name") == name:
            return item["folderid"]
    res = requests.get("https://api.pcloud.com/createfolder", params={"auth": PCLOUD_AUTH_TOKEN, "name": name, "folderid": 0})
    return res.json()["metadata"]["folderid"]

def upload_file_stream(file_stream, filename, folder_id):
    res = requests.post(
        "https://api.pcloud.com/uploadfile",
        params={"auth": PCLOUD_AUTH_TOKEN, "folderid": folder_id},
        files={"file": (filename, file_stream)}
    )
    return res.json()["metadata"][0]["fileid"]

def download_audio_from_url_via_ytdlp(download_url):
    ydl_opts = {
        'format': 'bestaudio/best',
        'quiet': True,
        'outtmpl': '-',
        'noplaylist': True,
    }

    buffer = BytesIO()
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        result = ydl.extract_info(download_url, download=False)
        stream_url = result["url"]
        res = requests.get(stream_url, stream=True)
        if res.status_code != 200 or "audio" not in res.headers.get("Content-Type", ""):
            raise Exception(f"Invalid MP3 content received. Status: {res.status_code}, Content-Type: {res.headers.get('Content-Type')}")

        for chunk in res.iter_content(chunk_size=8192):
            buffer.write(chunk)
        buffer.seek(0)
        return buffer

def download_thumbnail_stream(video_id):
    for quality in ["maxresdefault", "hqdefault", "mqdefault", "default"]:
        url = f"https://img.youtube.com/vi/{video_id}/{quality}.jpg"
        res = requests.get(url)
        if res.status_code == 200:
            return BytesIO(res.content)
    raise Exception("Thumbnail not found.")

def get_tags_from_gemini(song_name):
    PREDEFINED_TAGS = {
        "genre": ["pop", "rock", "hiphop", "rap", "r&b", "jazz", "classical", "edm", "indie", "instrumental"],
        "mood": ["happy", "sad", "romantic", "chill", "energetic", "motivational", "relaxing"],
        "occasion": ["party", "workout", "study", "sleep", "travel", "wedding", "gaming"],
        "era": ["80s", "90s", "2000s", "2010s", "2020s", "retro", "trending"],
        "vocal_instrument": ["female_vocals", "male_vocals", "duet", "instrumental_only", "live"]
    }

    prompt = f"""
Given the song name "{song_name}", identify its primary artist and language.
Then suggest tags using only the predefined tags below.
Return in JSON:
{{
  "artist": "...",
  "language": "...",
  "genre": [...],
  "mood": [...],
  "occasion": [...],
  "era": [...],
  "vocal_instrument": [...]
}}
Predefined tags:
{json.dumps(PREDEFINED_TAGS)}
"""

    res = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}",
        headers={"Content-Type": "application/json"},
        data=json.dumps({"contents": [{"parts": [{"text": prompt}]}]})
    )

    if res.status_code != 200:
        raise Exception("Gemini API failed")

    text = res.json()["candidates"][0]["content"]["parts"][0]["text"]
    if text.startswith("```json"):
        text = text.replace("```json", "").replace("```", "").strip()
    parsed = json.loads(text)

    tags = []
    for key in ["genre", "mood", "occasion", "era", "vocal_instrument"]:
        tags.extend(parsed.get(key, []))

    return {
        "artist": parsed.get("artist", "Unknown"),
        "language": parsed.get("language", "english"),
        "tags": tags
    }

# --- Main Endpoint ---
@app.get("/process")
def process_song(link: str = Query(..., description="YouTube video URL")):
    try:
        video_id = extract_video_id(link)

        # Step 1: RapidAPI fetch
        rapid = requests.get(
            f"https://{RAPIDAPI_HOST}/dl",
            headers={"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": RAPIDAPI_HOST},
            params={"id": video_id}
        )
        if rapid.status_code != 200:
            raise Exception("RapidAPI failed")

        data = rapid.json()
        download_url = data.get("link")
        title_raw = data.get("title", "downloaded_song")
        if not download_url:
            raise Exception("No MP3 link found")

        title = sanitize_title(title_raw)

        # Step 2: Download MP3 using yt_dlp
        mp3_stream = download_audio_from_url_via_ytdlp(download_url)

        # Step 3: Download thumbnail
        thumb_stream = download_thumbnail_stream(video_id)

        # Step 4: Upload to pCloud
        song_folder_id = get_or_create_folder(SONGS_FOLDER)
        img_folder_id = get_or_create_folder(IMGS_FOLDER)

        file_id = upload_file_stream(mp3_stream, f"{title}.mp3", song_folder_id)
        img_id = upload_file_stream(thumb_stream, f"{title}.jpg", img_folder_id)

        # Step 5: Make files public
        for fid in [file_id, img_id]:
            pub = requests.get("https://api.pcloud.com/getfilepublink", params={"auth": PCLOUD_AUTH_TOKEN, "fileid": fid})
            if pub.status_code != 200 or pub.json().get("result") != 0:
                raise Exception("Failed to make file public")

        # Step 6: Get tags
        meta = get_tags_from_gemini(title)

        # Step 7: Save to Supabase
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
