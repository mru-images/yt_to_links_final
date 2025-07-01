from fastapi import FastAPI, HTTPException, Query
import requests, json, os, re
from io import BytesIO
from supabase import create_client, Client

# --- Load from environment variables ---
PCLOUD_AUTH_TOKEN = os.getenv("PCLOUD_AUTH_TOKEN")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
RAPIDAPI_HOST = os.getenv("RAPIDAPI_HOST")
RAPIDAPI_KEY = os.getenv("RAPIDAPI_KEY")

SONGS_FOLDER = "songs_render"
IMGS_FOLDER = "imgs_render"

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
    return re.sub(r'\s+', ' ', safe_title).strip()[:100]

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
    res = requests.get(download_url, stream=True, allow_redirects=True)
    if res.status_code != 200 or 'audio' not in res.headers.get("Content-Type", ""):
        raise Exception("Invalid MP3 content received.")
    buf = BytesIO()
    for chunk in res.iter_content(chunk_size=8192):
        if chunk:
            buf.write(chunk)
    buf.seek(0)
    return buf

def download_thumbnail_stream(video_id):
    for quality in ["maxresdefault", "hqdefault", "mqdefault", "default"]:
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

    prompt = f"""Given the song name "{song_name}", identify its primary artist and language...
Return in this JSON format...
{json.dumps(PREDEFINED_TAGS, indent=2)}"""

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

        mp3_stream = download_mp3_stream(download_url)
        thumb_stream = download_thumbnail_stream(video_id)

        song_folder_id = get_or_create_folder(SONGS_FOLDER)
        img_folder_id = get_or_create_folder(IMGS_FOLDER)
        file_id = upload_file_stream(mp3_stream, f"{title}.mp3", song_folder_id)
        img_id = upload_file_stream(thumb_stream, f"{title}.jpg", img_folder_id)

        meta = get_tags_from_gemini(title)

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
