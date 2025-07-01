from fastapi import FastAPI, HTTPException, Query
import requests, json, os
from supabase import create_client, Client

# 🔐 Credentials
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
    response = requests.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_API_KEY}",
        headers={"Content-Type": "application/json"},
        data=json.dumps({"contents": [{"parts": [{"text": prompt}]}]})
    )
    if response.status_code != 200:
        raise Exception(f"Gemini API failed: {response.text}")
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

# --- API Endpoint ---
@app.get("/")
def root():
    return {"message": "✅ Render is loading... please wait or use /process?link=YOUR_YOUTUBE_URL"}

@app.get("/process")
def process_song(link: str = Query(..., description="YouTube video URL")):
    mp3_filename = None
    thumb_filename = None
    try:
        video_id = extract_video_id(link.strip())

        # Fetch video details from RapidAPI
        response = requests.get(
            f"https://{RAPIDAPI_HOST}/dl",
            headers={"X-RapidAPI-Key": RAPIDAPI_KEY, "X-RapidAPI-Host": RAPIDAPI_HOST},
            params={"id": video_id}
        )
        data = response.json()
        download_url = data.get("link")
        title = data.get("title", "downloaded_song").replace("/", "-").replace("\\", "-").strip()
        if not download_url:
            raise HTTPException(status_code=400, detail="MP3 download link not found.")

        # Download MP3
        mp3_filename = f"/tmp/{title}.mp3"
        mp3_data = requests.get(download_url)
        with open(mp3_filename, "wb") as f:
            f.write(mp3_data.content)

        # Download thumbnail
        thumb_filename = download_thumbnail(video_id, title)

        # Upload to pCloud
        song_folder_id = get_or_create_folder(SONGS_FOLDER)
        img_folder_id = get_or_create_folder(IMGS_FOLDER)
        file_id = upload_file(mp3_filename, os.path.basename(mp3_filename), song_folder_id)
        img_id = upload_file(thumb_filename, os.path.basename(thumb_filename), img_folder_id)
        
        # Make MP3 public
        pub = requests.get("https://api.pcloud.com/getfilepublink", params={
            "auth": PCLOUD_AUTH_TOKEN,
            "fileid": file_id
        })
        if pub.status_code != 200 or pub.json().get("result") != 0:
            raise Exception(f"Failed to make MP3 public: {pub.text}")
        public_link = pub.json()["hosts"][0] + pub.json()["path"]
        
        # Make image public
        pub_img = requests.get("https://api.pcloud.com/getfilepublink", params={
            "auth": PCLOUD_AUTH_TOKEN,
            "fileid": img_id
        })
        if pub_img.status_code != 200 or pub_img.json().get("result") != 0:
            raise Exception(f"Failed to make image public: {pub_img.text}")
        img_link = pub_img.json()["hosts"][0] + pub_img.json()["path"]
        

        # Get metadata from Gemini
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
        # ✅ Cleanup temp files
        for file in [mp3_filename, thumb_filename]:
            if file and os.path.exists(file):
                try:
                    os.remove(file)
                except Exception as cleanup_error:
                    print(f"⚠️ Failed to delete {file}: {cleanup_error}")
