from fastapi import FastAPI, HTTPException, Query
import requests, json, os, io
from supabase import create_client, Client

# 🔐 Environment Variables
PCLOUD_AUTH_TOKEN = os.getenv("PCLOUD_AUTH_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
RAPIDAPI_HOST = os.getenv("RAPIDAPI_HOST")
RAPIDAPI_KEY = os.getenv("RAPIDAPI_KEY")

SONGS_FOLDER = "songs_test"
IMGS_FOLDER = "imgs_test"

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
app = FastAPI()


# --- Helpers ---
def extract_video_id(url):
    if "youtu.be/" in url:
        return url.split("youtu.be/")[-1].split("?")[0]
    elif "watch?v=" in url:
        return url.split("watch?v=")[-1].split("&")[0]
    else:
        raise ValueError("Invalid YouTube URL")


def get_or_create_folder(folder_name):
    res = requests.get("https://api.pcloud.com/listfolder", params={"auth": PCLOUD_AUTH_TOKEN, "folderid": 0})
    for item in res.json().get("metadata", {}).get("contents", []):
        if item.get("isfolder") and item.get("name") == folder_name:
            return item["folderid"]
    res = requests.get("https://api.pcloud.com/createfolder", params={"auth": PCLOUD_AUTH_TOKEN, "name": folder_name, "folderid": 0})
    return res.json()["metadata"]["folderid"]


def upload_file(file_stream, filename, folder_id):
    file_stream.seek(0)
    res = requests.post(
        "https://api.pcloud.com/uploadfile",
        params={"auth": PCLOUD_AUTH_TOKEN, "folderid": folder_id},
        files={"file": (filename, file_stream)}
    )
    if res.status_code != 200:
        raise Exception(f"Upload failed: {res.text}")
    return res.json()["metadata"][0]["fileid"]


def download_thumbnail(video_id):
    qualities = ["maxresdefault", "hqdefault", "mqdefault", "default"]
    for quality in qualities:
        url = f"https://img.youtube.com/vi/{video_id}/{quality}.jpg"
        res = requests.get(url)
        if res.status_code == 200:
            return io.BytesIO(res.content), f"{video_id}_{quality}.jpg"
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
        raise Exception(f"Gemini error: {res.text}")
    
    raw = res.json()["candidates"][0]["content"]["parts"][0]["text"]
    if raw.startswith("```json"):
        raw = raw.strip("` \n").replace("json", "", 1).strip()
    parsed = json.loads(raw)
    tags = []
    for key in ["genre", "mood", "occasion", "era", "vocal_instrument"]:
        tags.extend(parsed.get(key, []))
    return {
        "artist": parsed.get("artist", "Unknown"),
        "language": parsed.get("language", "english"),
        "tags": tags
    }

@app.get("/process")
def process_song(link: str = Query(..., description="YouTube video URL")):
    try:
        video_id = extract_video_id(link.strip())

        # 🔗 Get MP3 link from RapidAPI
        res = requests.get(
            f"https://{RAPIDAPI_HOST}/dl",
            headers={
                "X-RapidAPI-Key": RAPIDAPI_KEY,
                "X-RapidAPI-Host": RAPIDAPI_HOST
            },
            params={"id": video_id}
        )
        data = res.json()
        download_url = data.get("link")
        title = data.get("title", "downloaded_song").replace("/", "-").replace("\\", "-").strip()

        if not download_url:
            raise HTTPException(status_code=400, detail="MP3 download link not found.")

        # 🎧 Download MP3 into memory
        mp3_res = requests.get(download_url)
        if mp3_res.status_code != 200:
            raise HTTPException(status_code=400, detail="MP3 download failed.")
        mp3_stream = io.BytesIO(mp3_res.content)
        mp3_filename = f"{title}.mp3"

        # 🖼️ Download thumbnail into memory
        thumb_stream, thumb_filename = download_thumbnail(video_id)

        # ☁️ Upload both to pCloud
        song_folder = get_or_create_folder(SONGS_FOLDER)
        img_folder = get_or_create_folder(IMGS_FOLDER)
        file_id = upload_file(mp3_stream, mp3_filename, song_folder)
        img_id = upload_file(thumb_stream, thumb_filename, img_folder)

        # 🤖 Gemini metadata
        meta = get_tags_from_gemini(title)

        # 📦 Insert into Supabase
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
