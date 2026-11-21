#!/usr/bin/env python3
"""Hindi manhwa-recap video maker (all free tools). One run = one ~1 hour part.

Flow: pick series (A/B/C rotation) -> Gemini writes the part -> Pollinations draws one image per scene
      -> edge-tts reads Hindi narration -> ffmpeg builds the video -> (optional) YouTube upload -> save state.

Run:   python scripts/make_part.py              normal
       python scripts/make_part.py --selftest   no internet needed, only tests the video builder
"""
import asyncio, datetime, io, json, os, random, re, subprocess, sys, time, urllib.parse
import requests
from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CFG = json.load(open(os.path.join(ROOT, "config.json"), encoding="utf-8"))
WORK = os.path.join(ROOT, "work")
STATE_PATH = os.path.join(ROOT, "state", "state.json")
T0 = time.time()
W, H, FPS = CFG["width"], CFG["height"], CFG["fps"]
FG_W, FG_H = 540, 720                      # the portrait "panel" in the middle (like the sample video)
STYLE = ("Korean manhwa webtoon illustration, detailed digital painting, cinematic dramatic lighting, "
         "vertical portrait composition, no text, no speech bubbles, no watermark")


def log(*a):
    print(*a, flush=True)


# ------------------------------------------------------------------ Gemini
_MODELS = None


def gemini_models(key):
    global _MODELS
    if _MODELS:
        return _MODELS
    names = list(CFG["gemini_models"])
    found = []
    try:
        r = requests.get("https://generativelanguage.googleapis.com/v1beta/models?pageSize=200",
                         headers={"x-goog-api-key": key}, timeout=30)
        for m in r.json().get("models", []):
            n = m["name"].split("/")[-1]
            if ("generateContent" in m.get("supportedGenerationMethods", []) and "flash" in n
                    and not any(x in n for x in ("image", "tts", "audio", "live", "embedding", "robotics", "preview-"))):
                found.append(n)
        found.sort(key=lambda n: ("lite" in n, n))
        log("Models this key can use:", ", ".join(found) or "none found")
    except Exception as e:
        log("Could not list models:", e)
    for n in found:
        if n not in names:
            names.append(n)
    _MODELS = names
    return names


def parse_json(text):
    text = text.strip()
    text = re.sub(r"^```(?:json)?", "", text).strip()
    text = re.sub(r"```$", "", text).strip()
    try:
        return json.loads(text)
    except Exception:
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            return json.loads(m.group(0))
        raise


def gemini_json(prompt, temperature=0.9):
    key = os.environ["GEMINI_API_KEY"]
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {"responseMimeType": "application/json", "temperature": temperature}}
    last = None
    for model in gemini_models(key):
        for attempt in range(5):
            try:
                url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
                r = requests.post(url, headers={"x-goog-api-key": key}, json=body, timeout=180)
                if r.status_code in (400, 403, 404):
                    last = f"{model} HTTP {r.status_code}: {r.text[:200]}"
                    log("Skipping model:", last)
                    break
                if r.status_code != 200:                      # 429 / 5xx: wait and try again
                    last = f"{model} HTTP {r.status_code}: {r.text[:200]}"
                    log("Gemini busy:", last[:120])
                    time.sleep(20 * (attempt + 1))
                    continue
                text = r.json()["candidates"][0]["content"]["parts"][0]["text"]
                return parse_json(text)
            except Exception as e:
                last = f"{model}: {e}"
                log("Gemini retry:", last[:150])
                time.sleep(8)
    raise SystemExit(f"STOP: Gemini failed. Last error: {last}")


# ------------------------------------------------------------------ story
def load_state():
    return json.load(open(STATE_PATH, encoding="utf-8"))


def save_state(st):
    json.dump(st, open(STATE_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2)


def stage_for(series, part):
    cur = series["hero"]["stages"][0]
    for s in series["hero"]["stages"]:
        if s["from_part"] <= part:
            cur = s
    return cur


def get_beat(series, st, part):
    sid = series["id"]
    beats = series["outline"] + st["extra_outline"].get(sid, [])
    while part > len(beats):
        log("Outline finished - asking Gemini for 10 more story beats")
        p = f"""You plan an ongoing original Hindi manhwa-style story.
Series: {series['title_hi']}. Premise: {series['premise']}
Cast: {series['cast_hi']}
What happened so far (last recap): {st['recaps'].get(sid, '')}
Last story beats: {beats[-3:]}
Write the NEXT 10 story beats (one per part, each part is a 1-hour episode). Keep the hero a boy who keeps growing stronger,
raise stakes slowly, add new enemies and mysteries, a emotional motive, a few funny moments. Never end the story.
Return JSON only: {{"beats": ["beat 1 in English, 2 sentences", "..."]}}"""
        new = gemini_json(p)["beats"]
        st["extra_outline"].setdefault(sid, []).extend(new)
        beats = series["outline"] + st["extra_outline"][sid]
    return beats[part - 1]


def clean_text(t):
    t = re.sub(r"[*#_`~>\[\]{}|\\]", "", str(t))
    t = re.sub(r"\s+", " ", t).strip()
    if t and t[-1] not in "।.?!":
        t += "।"
    return t


def plan_part(series, st, part, beat, stage):
    n = CFG["chunks"]
    prompt = f"""You are the head writer of a Hindi YouTube channel that tells ORIGINAL manhwa-style stories as long recap videos.
Series: {series['title_hi']}
World: {series['world']}
Premise: {series['premise']}
Cast (use these Hindi names exactly): {series['cast_hi']}
Hero look right now: {stage['look']}
Part number: {part}
Story so far (recap of the previous part): {st['recaps'].get(series['id'], '') or 'This is part 1, the story begins.'}
STORY BEAT FOR THIS PART: {beat}

Split this part into exactly {n} sections that together make one 1-hour episode with a strong hook at the start, rising tension,
one reversal, a satisfying fight or payoff, and a cliffhanger at the end. Each section = about 5 minutes of narration.
Return JSON only:
{{"title_hi": "catchy Hindi title in Devanagari, max 70 characters, include 'भाग {part}'",
 "description_hi": "2-3 short Hindi sentences about this part. Do not spoil the ending.",
 "tags": ["manhwa", "hindi", "story", "...up to 10 more"],
 "recap": "6 sentence English summary of everything that happens in this part, for future memory",
 "sections": [{{"heading": "short English heading", "events": "3-4 English sentences of what happens in this section"}}]}}"""
    plan = gemini_json(prompt)
    secs = plan.get("sections", [])
    if len(secs) < n:
        raise SystemExit("STOP: plan had too few sections")
    plan["sections"] = secs[:n]
    return plan


def write_chunk(series, stage, part, i, n, sec, tail):
    k = CFG["scenes_per_chunk"]
    last = "This is the LAST section: end on a strong cliffhanger and add one short line asking viewers to subscribe and come for the next part." if i == n - 1 else ""
    first = "This is the FIRST section: start with a shocking hook line in the very first sentence." if i == 0 else "Continue smoothly from the previous text."
    prompt = f"""You write the narration for a long Hindi manhwa-recap video. Series: {series['title_hi']}, part {part}, section {i + 1} of {n}.
Cast (use these Hindi names exactly): {series['cast_hi']}
Hero look right now: {stage_look(stage)}
SECTION TO WRITE: {sec['heading']}. {sec['events']}
{first} {last}
Previous narration ended with: {tail or '(nothing yet)'}

Write exactly {k} scenes. Each scene has:
- "narration": 55 to 70 words of spoken Hindi in Devanagari script. Simple everyday Hindi, short dramatic sentences, like a storyteller (third person). Dialogues are told like: आरव ने कहा, "..." . No English letters, no emojis, no brackets, no stage directions, no numbers in digits (write them in words), never say 'panel' or 'chapter'.
- "image": ONE English sentence (max 40 words) describing a single illustrated manhwa panel for this moment: place, who is in it, their action and expression, mood, camera angle. Describe people by looks, not by name.
- "hero": true if the hero boy is visible in that panel, otherwise false.
Return JSON only: {{"scenes": [{{"narration": "...", "image": "...", "hero": true}}]}}"""
    for _ in range(3):
        d = gemini_json(prompt)
        sc = [s for s in d.get("scenes", []) if s.get("narration") and s.get("image")]
        if len(sc) >= max(6, k // 2):
            for s in sc:
                s["narration"] = clean_text(s["narration"])
                s["hero"] = bool(s.get("hero"))
            return sc
        log("Chunk too short, retrying")
    raise SystemExit("STOP: could not write chunk")


def stage_look(stage):
    return stage["look"]


# ------------------------------------------------------------------ images
def fetch_image(prompt, seed, path):
    url = "https://image.pollinations.ai/prompt/" + urllib.parse.quote(prompt[:800])
    headers = {}
    if os.environ.get("POLLINATIONS_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["POLLINATIONS_TOKEN"]
    for attempt in range(4):
        try:
            r = requests.get(url, params={"width": 768, "height": 1024, "seed": seed, "nologo": "true", "model": "flux"},
                             headers=headers, timeout=150)
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
                im = Image.open(io.BytesIO(r.content)).convert("RGB")
                if min(im.size) >= 400:
                    im.save(path, quality=92)
                    return True
            log("Image service answered", r.status_code, "- retry")
        except Exception as e:
            log("Image problem:", str(e)[:120])
        time.sleep(8 * (attempt + 1))
    return False


def make_images(scenes, series, stage, part):
    seed_base = series["seed"] + 10 * [s["from_part"] for s in series["hero"]["stages"]].index(stage["from_part"])
    hero = series["hero"]
    prev, fails = None, 0
    for i, s in enumerate(scenes):
        if time.time() - T0 > CFG["max_minutes"] * 60 * 0.6:
            raise SystemExit("STOP: images are taking too long")
        path = f"{WORK}/img{i:04d}.jpg"
        prompt = f"{STYLE}. {s['image']}"
        if s["hero"]:
            prompt += f". Main character {hero['name_en']}: {hero['face']}; {stage['look']}"
        seed = seed_base if s["hero"] else random.randint(1, 10**6)
        ok = fetch_image(prompt, seed, path)
        if ok:
            prev = path
        else:
            fails += 1
            log(f"Image {i} failed ({fails} fails so far)")
            if fails > max(15, 0.4 * (i + 1)):
                raise SystemExit("STOP: the free image service keeps failing - not making a junk video")
            if prev is None:
                Image.new("RGB", (768, 1024), (20, 20, 30)).save(path)
            else:
                Image.open(prev).save(path)
        s["img"] = path
        if i % 10 == 0:
            log(f"Images: {i + 1}/{len(scenes)}  ({(time.time() - T0) / 60:.0f} min elapsed)")
        time.sleep(CFG["image_sleep"])


# ------------------------------------------------------------------ voice
async def _tts(text, path, voice, rate):
    import edge_tts
    await edge_tts.Communicate(text, voice, rate=rate).save(path)


def duration(path):
    out = subprocess.check_output(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                   "-of", "default=nw=1:nk=1", path], text=True)
    return float(out.strip())


def make_voices(scenes):
    total = 0.0
    for i, s in enumerate(scenes):
        p = f"{WORK}/a{i:04d}.mp3"
        ok = False
        for a in range(4):
            try:
                asyncio.run(_tts(s["narration"], p, CFG["voice"], CFG["voice_rate"]))
                ok = os.path.getsize(p) > 1500
                if ok:
                    break
            except Exception as e:
                log("Voice problem:", str(e)[:150])
            time.sleep(3 * (a + 1))
        if not ok:
            raise SystemExit("STOP: edge-tts could not make a voice")
        s["audio"] = p
        s["dur"] = duration(p) + 0.35
        total += s["dur"]
        if i % 20 == 0:
            log(f"Voices: {i + 1}/{len(scenes)}  total so far {total / 60:.1f} min")
    return total


# ------------------------------------------------------------------ video
def build_clip(i, s, out):
    dur = s["dur"]
    D = max(2, int(dur * FPS))
    z = f"1+0.10*on/{D}" if i % 2 == 0 else f"1.10-0.10*on/{D}"
    fc = (f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=28:6,"
          f"eq=brightness=-0.12:saturation=1.1[bg];"
          f"[1:v]scale={FG_W * 2}:{FG_H * 2},zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
          f"d={D}:s={FG_W}x{FG_H}:fps={FPS}[fg];"
          f"[bg][fg]overlay=(W-w)/2:(H-h)/2[v]")
    cmd = ["ffmpeg", "-y", "-loglevel", "error",
           "-loop", "1", "-framerate", str(FPS), "-t", f"{dur:.3f}", "-i", s["img"],
           "-i", s["img"], "-i", s["audio"],
           "-filter_complex", fc, "-map", "[v]", "-map", "2:a", "-af", "apad",
           "-t", f"{dur:.3f}", "-c:v", "libx264", "-preset", "veryfast", "-crf", "27", "-pix_fmt", "yuv420p",
           "-r", str(FPS), "-c:a", "aac", "-b:a", "96k", "-ar", "44100", "-ac", "2", out]
    subprocess.run(cmd, check=True)


def build_video(scenes, final):
    lst = f"{WORK}/list.txt"
    with open(lst, "w") as f:
        for i, s in enumerate(scenes):
            clip = f"{WORK}/clip{i:04d}.mp4"
            build_clip(i, s, clip)
            f.write(f"file 'clip{i:04d}.mp4'\n")
            if i % 20 == 0:
                log(f"Video clips: {i + 1}/{len(scenes)}  ({(time.time() - T0) / 60:.0f} min elapsed)")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst,
                    "-c", "copy", "-movflags", "+faststart", final], check=True)
    for i in range(len(scenes)):                      # free disk space
        for p in (f"{WORK}/clip{i:04d}.mp4", f"{WORK}/img{i:04d}.jpg", f"{WORK}/a{i:04d}.mp3"):
            if os.path.exists(p):
                os.remove(p)


# ------------------------------------------------------------------ upload
def upload(video, meta):
    cid, sec, ref = (os.environ.get(k) for k in ("YT_CLIENT_ID", "YT_CLIENT_SECRET", "YT_REFRESH_TOKEN"))
    if not (cid and sec and ref):
        log("Upload skipped: YouTube secrets not added yet.")
        return None
    tok = requests.post("https://oauth2.googleapis.com/token", data={
        "client_id": cid, "client_secret": sec, "refresh_token": ref, "grant_type": "refresh_token"}, timeout=60)
    if tok.status_code != 200:
        raise SystemExit(f"STOP: YouTube login failed: {tok.text[:300]}")
    access = tok.json()["access_token"]
    size = os.path.getsize(video)
    init = requests.post("https://www.googleapis.com/upload/youtube/v3/videos?uploadType=resumable&part=snippet,status",
                         headers={"Authorization": f"Bearer {access}", "Content-Type": "application/json",
                                  "X-Upload-Content-Type": "video/mp4", "X-Upload-Content-Length": str(size)},
                         json=meta, timeout=60)
    if init.status_code != 200:
        raise SystemExit(f"STOP: upload start failed: {init.text[:400]}")
    loc = init.headers["Location"]
    chunk = 8 * 1024 * 1024
    pos = 0
    with open(video, "rb") as f:
        while pos < size:
            f.seek(pos)
            data = f.read(chunk)
            end = pos + len(data) - 1
            for attempt in range(6):
                try:
                    r = requests.put(loc, data=data, timeout=300, headers={
                        "Content-Length": str(len(data)), "Content-Range": f"bytes {pos}-{end}/{size}"})
                    if r.status_code in (200, 201):
                        vid = r.json().get("id")
                        log("UPLOADED! video id:", vid, "privacy:", meta["status"]["privacyStatus"])
                        return vid
                    if r.status_code == 308:
                        break
                    log("Upload chunk problem", r.status_code, r.text[:200])
                except Exception as e:
                    log("Upload chunk error:", str(e)[:150])
                time.sleep(10 * (attempt + 1))
            else:
                raise SystemExit("STOP: upload failed")
            pos = end + 1
    raise SystemExit("STOP: upload ended without a video id")


# ------------------------------------------------------------------ self test (no internet)
def selftest():
    os.makedirs(WORK, exist_ok=True)
    scenes = []
    cols = [(40, 30, 90), (90, 40, 40), (30, 80, 70)]
    for i in range(3):
        im = Image.new("RGB", (768, 1024), cols[i])
        d = ImageDraw.Draw(im)
        for y in range(1024):
            d.line([(0, y), (768, y)], fill=(cols[i][0] + y // 12, cols[i][1] + y // 14, cols[i][2] + y // 10))
        d.ellipse([234, 300, 534, 600], fill=(230, 210, 190))
        f = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 60)
        d.text((384, 760), f"TEST PANEL {i + 1}", font=f, fill=(255, 255, 255), anchor="mm")
        p = f"{WORK}/img{i:04d}.jpg"
        im.save(p)
        a = f"{WORK}/a{i:04d}.mp3"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i",
                        "sine=frequency=220:duration=3,tremolo=f=5:d=0.8", "-c:a", "libmp3lame", a], check=True)
        scenes.append({"img": p, "audio": a, "dur": duration(a) + 0.35})
    out = f"{WORK}/selftest.mp4"
    build_video(scenes, out)
    log("SELFTEST OK:", out, f"{duration(out):.1f}s")


# ------------------------------------------------------------------ main
def main():
    os.makedirs(WORK, exist_ok=True)
    if "--selftest" in sys.argv:
        return selftest()
    st = load_state()
    ist = datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)
    today = ist.date().isoformat()
    if st.get("last_date") == today and os.environ.get("FORCE") != "1":
        log("Today's video is already made. Nothing to do. (Use FORCE=1 to make another.)")
        return
    order = CFG["series_order"]
    sid = order[st["next_index"] % len(order)]
    series = json.load(open(os.path.join(ROOT, "series", f"{sid}.json"), encoding="utf-8"))
    part = st["parts"][sid] + 1
    stage = stage_for(series, part)
    beat = get_beat(series, st, part)
    log(f"Making: {series['title_hi']}  part {part}\nBeat: {beat}\nHero stage: {stage['look']}")

    plan = plan_part(series, st, part, beat, stage)
    log("Title:", plan["title_hi"])
    scenes, tail = [], ""
    n = CFG["chunks"]
    for i, sec in enumerate(plan["sections"]):
        sc = write_chunk(series, stage, part, i, n, sec, tail)
        scenes.extend(sc)
        tail = " ".join(x["narration"] for x in sc[-2:])[-400:]
        log(f"Script: section {i + 1}/{n} done, {len(scenes)} scenes, {sum(len(x['narration'].split()) for x in scenes)} words")
    make_images(scenes, series, stage, part)
    total = make_voices(scenes)
    log(f"Total voice length: {total / 60:.1f} minutes")
    final = f"{WORK}/part.mp4"
    build_video(scenes, final)
    log(f"Video ready: {final} ({duration(final) / 60:.1f} min, {os.path.getsize(final) / 1e6:.0f} MB)")
    json.dump({"plan": plan, "scenes": [{k: v for k, v in s.items() if k in ("narration", "image", "hero")} for s in scenes]},
              open(f"{WORK}/script.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    desc = (plan["description_hi"] + "\n\nयह कहानी पूरी तरह मौलिक (original) है। कहानी, चित्र और आवाज़ AI की मदद से बनाए गए हैं।\n\n"
            "#manhwa #hindi #kahani")
    meta = {"snippet": {"title": plan["title_hi"][:95], "description": desc, "categoryId": "24",
                        "tags": [str(t) for t in plan.get("tags", [])][:15]},
            "status": {"privacyStatus": os.environ.get("PRIVACY", "private"), "selfDeclaredMadeForKids": False,
                       "containsSyntheticMedia": True}}
    vid = None
    if os.environ.get("UPLOAD") == "1":
        vid = upload(final, meta)

    st["parts"][sid] = part
    st["recaps"][sid] = plan["recap"]
    st["next_index"] = (st["next_index"] + 1) % len(order)
    st["last_date"] = today
    st["history"].append({"date": today, "series": sid, "part": part, "title": plan["title_hi"], "video_id": vid})
    save_state(st)
    log("DONE")


if __name__ == "__main__":
    main()
