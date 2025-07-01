from fastapi import FastAPI, HTTPException, Query
import requests, json, os, re
from io import BytesIO
from supabase import create_client, Client

# 🔐 Credentials from environment variables
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

# --- Utilities ---
def extract_video_id(url):
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
    response = requests.get(download_url, stream=True, allow_redirects=True,
                            headers={"User-Agent": "Mozilla/5.0"})
    ct = response.headers.get("Content-Type", "")
    if response.status_code != 200 or not any(x in ct for x in ["audio", "octet-stream"]):
        snippet = response.raw.read(256)
        raise Exception(f"Invalid MP3 content: {response.status_code} {ct} {snippet!r}")
    buffer = BytesIO()
    for chunk in response.iter_content(chunk_size=8192):
        if chunk:
            buffer.write(chunk)
    buffer.seek(0)
    sig = buffer.read(4)
    buffer.seek(0)
    if not sig.startswith((b"ID3", b"\xff\xfb")):
        raise Exception(f"Downloaded file doesn't look like MP3: {sig!r}")
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
    PREDEFINED_TAGS = {...}  # (Omitted for brevity, use full dict from your previous code)

    prompt = f"""
Given the song name \"{song_name}\", identify its primary artist and language.
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

        mp3_stream = download_mp3_stream(download_url)
        thumb_stream = download_thumbnail_stream(video_id)

        song_folder_id = get_or_create_folder(SONGS_FOLDER)
        img_folder_id = get_or_create_folder(IMGS_FOLDER)
        file_id = upload_file_stream(mp3_stream, f"{title}.mp3", song_folder_id)
        img_id = upload_file_stream(thumb_stream, f"{title}.jpg", img_folder_id)

        tag_data = get_tags_from_gemini(title)

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
