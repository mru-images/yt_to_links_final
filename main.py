@app.get("/process")
def process_song(link: str = Query(..., description="YouTube video URL")):
    try:
        video_id = extract_video_id(link.strip())

        # 🎧 Fetch video details from RapidAPI
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

        # 🎵 Download MP3 to memory
        mp3_response = requests.get(download_url)
        if mp3_response.status_code != 200:
            raise HTTPException(status_code=400, detail="Failed to download MP3.")
        mp3_stream = io.BytesIO(mp3_response.content)
        mp3_filename = f"{title}.mp3"

        # 🖼️ Download thumbnail to memory
        thumb_stream, thumb_filename = download_thumbnail(video_id)

        # ☁️ Upload to pCloud from memory
        song_folder_id = get_or_create_folder(SONGS_FOLDER)
        img_folder_id = get_or_create_folder(IMGS_FOLDER)
        file_id = upload_file(mp3_stream, mp3_filename, song_folder_id)
        img_id = upload_file(thumb_stream, thumb_filename, img_folder_id)

        # 🤖 Get metadata using Gemini
        tag_data = get_tags_from_gemini(title)

        # 🧾 Insert metadata into Supabase
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
