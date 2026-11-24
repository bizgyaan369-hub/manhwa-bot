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
STYLE = ("high quality 2D Korean manhwa panel, clean sharp lineart, smooth cel shading, vivid bright colors, soft glowing light, "
         "expressive anime-style faces with detailed eyes and flowing hair, cinematic composition, detailed fantasy backgrounds, "
         "East Asian characters, NOT photorealistic, NOT a real photo, NOT a 3D render, "
         "vertical portrait composition, no text, no letters, no speech bubbles, no watermark")


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


def load_series(st, sid):
    path = os.path.join(ROOT, "series", f"{sid}.json")
    if os.path.exists(path):
        return json.load(open(path, encoding="utf-8"))
    return st["custom_series"][sid]


def create_new_series(st):
    """a finished series is replaced by a brand-new original one (made by Gemini)."""
    ids = set(st["parts"].keys())
    new_id = next(c for c in "abcdefghijklmnopqrstuvwxyz" if c not in ids)
    used = []
    for sid in st["parts"]:
        sr = load_series(st, sid)
        used.append(f"{sr['title_hi']} | {sr['cast_hi']}")
    p = f"""Create a BRAND-NEW original manhwa-style series for a Hindi YouTube channel (20 episodes of 1 hour each).
Already used series (never reuse their world, plot or names): {used}
Rules: the hero is a BOY; ALL character names are Korean, Japanese or Chinese style (never Indian names); the world and plot must be
completely different from the used series and from any famous existing manhwa; strong hook; the hero changes look in 3 stages as he grows.
Use popular formulas: regression, weak-to-strong, hidden system, underestimated hero, revenge, survival, dark fantasy.
Return JSON only:
{{"title_hi": "title in Devanagari", "world": "English", "premise": "English, 3 sentences",
 "hero": {{"name_en": "...", "name_hi": "Devanagari", "face": "same face every time: ...",
   "stages": [{{"from_part": 1, "look": "..."}}, {{"from_part": 6, "look": "..."}}, {{"from_part": 14, "look": "..."}}]}},
 "cast_hi": "Devanagari names with a short Hindi role in brackets, comma separated",
 "ending": "how the series ends, English", "outline": ["10 English story beats, 2 sentences each"]}}"""
    sr = gemini_json(p, temperature=1.0)
    for k in ("title_hi", "world", "premise", "hero", "cast_hi", "outline"):
        if k not in sr:
            raise SystemExit(f"STOP: new series is missing {k}")
    sr["id"], sr["seed"], sr["total_parts"] = new_id, random.randint(1000, 9999), 20
    st["custom_series"][new_id] = sr
    st["parts"][new_id] = 0
    st["recaps"][new_id] = ""
    st["extra_outline"][new_id] = []
    log("New series created:", sr["title_hi"])
    return new_id


def get_beat(series, st, part):
    sid = series["id"]
    total = series.get("total_parts", 20)
    extra = st["extra_outline"].setdefault(sid, [])
    beats = series["outline"] + extra
    part = min(part, total)
    while part > len(beats):
        need = min(10, total - len(beats))
        finale = len(beats) + need >= total
        log(f"Outline finished - asking Gemini for {need} more story beats")
        p = f"""You plan an ongoing original Hindi manhwa-style story.
Series: {series['title_hi']}. Premise: {series['premise']}
Cast: {series['cast_hi']}
Ending direction: {series.get('ending', 'a satisfying, earned ending')}
What happened so far (last recap): {st['recaps'].get(sid, '')}
Last story beats: {beats[-3:]}
Write the NEXT {need} story beats (one per part, each part is a 1-hour episode). The hero keeps growing, stakes rise, add new enemies and
mysteries, an emotional motive, a few funny moments. {'The LAST beat must be the satisfying FINALE of the whole series.' if finale else 'Do not end the story yet.'}
Return JSON only: {{"beats": ["beat 1 in English, 2 sentences", "..."]}}"""
        new = gemini_json(p)["beats"][:need]
        if not new:
            raise SystemExit("STOP: Gemini gave no story beats")
        extra.extend(new)
        beats = series["outline"] + extra
    return beats[part - 1]


def clean_text(t):
    t = re.sub(r"[*#_`~>\[\]{}|\\]", "", str(t))
    t = re.sub(r"\s+", " ", t).strip()
    if t and t[-1] not in "।.?!":
        t += "।"
    return t


def plan_part(series, st, part, beat, stage, finale):
    n = CFG["chunks"]
    end_note = ("THIS IS THE FINAL PART OF THE WHOLE SERIES: resolve every main thread, give an emotional farewell and a satisfying ending (no cliffhanger)."
                if finale else "End with a strong cliffhanger.")
    prompt = f"""You are the head writer of a Hindi YouTube channel that tells ORIGINAL manhwa-style stories as long recap videos.
Series: {series['title_hi']}
World: {series['world']}
Premise: {series['premise']}
Ending direction of the whole series: {series.get('ending', 'a satisfying, earned ending')}
Cast (use these Hindi names exactly): {series['cast_hi']}
Hero look right now: {stage['look']}
Part number: {part}
Story so far (recap of the previous part): {st['recaps'].get(series['id'], '') or 'This is part 1, the story begins.'}
STORY BEAT FOR THIS PART: {beat}

Split this part into exactly {n} sections that together make one 1-hour episode. It must feel like a top webtoon: a shocking hook in the
first minute, fast pace (something NEW happens in every section: a reveal, a betrayal, a fight, a power-up, a trick by the hero), a clear
humiliation -> secret strength -> "face-slap" payoff for the people who underestimated the hero, concrete details (skill names, numbers,
levels, rewards, rules of the world and the price of every power-up), at least two surprising twists, a little humor. {end_note}
NEVER be vague or slow, never repeat earlier events. Each section = about 5 minutes of narration.
Return JSON only:
{{"title_hi": "catchy Hindi title in Devanagari, max 70 characters, include 'भाग {part}'",
 "teaser": "the single most shocking moment of this part, 2 English sentences (it will be shown first as a cold open)",
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


def write_chunk(series, stage, part, i, n, sec, tail, plan, prev_recap, finale):
    k = CFG["scenes_per_chunk"]
    if i == n - 1:
        last = ("This is the LAST section of the whole series: finish the story with a satisfying ending and a warm thank-you line to the viewers."
                if finale else
                "This is the LAST section: end on a strong cliffhanger and add one short line asking viewers to subscribe and come for the next part.")
    else:
        last = ""
    if i == 0:
        recap_line = (f" After the cold open add a brief recap in 2 sentences starting with 'पिछले भाग में', based on: {prev_recap}"
                      if prev_recap else "")
        first = (f"This is the FIRST section. Start with a COLD OPEN of 2-3 gripping sentences that show the most shocking moment of this part: {plan.get('teaser', '')}"
                 f" Then continue with 'कुछ समय पहले...' and tell the story from its real beginning.{recap_line}")
    else:
        first = "Continue smoothly from the previous text."
    prompt = f"""You write the narration for a long Hindi manhwa-recap video. Series: {series['title_hi']}, part {part}, section {i + 1} of {n}.
Cast (use these Hindi names exactly): {series['cast_hi']}
Hero look right now: {stage_look(stage)}
SECTION TO WRITE: {sec['heading']}. {sec['events']}
{first} {last}
Previous narration ended with: {tail or '(nothing yet)'}

Write exactly {k} scenes. Each scene has:
- "narration": 65 to 80 words of spoken Hindi written in Devanagari script, like a popular manhwa-recap YouTuber: energetic, simple everyday Hindi, short punchy sentences, third person, lots of suspense and emotion. Mix in 2 to 4 EASY English words per scene that every manhwa fan knows (for example पावर, सिस्टम, लेवल, बॉस, स्किल, गिल्ड, फाइट, मिशन, रैंक, अलर्ट, हीरो, टावर, ट्रैप, सीक्रेट) but write them in Devanagari letters, never hard English. Dialogues are told like: नाम ने कहा, "..." . No Latin letters at all, no emojis, no brackets, no stage directions, write numbers in words, never say 'panel' or 'chapter'. Be specific and dramatic, not generic. Names must be exactly as in the cast list.
- "image": ONE English sentence (max 40 words) describing a single illustrated manhwa panel for this moment. VARY the shot type from scene to scene: extreme close-up of eyes, face close-up with strong emotion, character seen from behind looking at a huge view, wide establishing shot of a fantasy place from above, close-up of an object (a cup, a sword, a glowing screen), dynamic action pose with energy effects, group shot. Say place, who is in it, their expression, mood, lighting. Describe people by looks, not by name.
- "hero": true if the hero boy is visible in that panel, otherwise false.
Return JSON only: {{"scenes": [{{"narration": "...", "image": "...", "hero": true}}]}}"""
    for _ in range(3):
        d = gemini_json(prompt)
        sc = [x for x in d.get("scenes", []) if x.get("narration") and x.get("image")]
        if len(sc) >= max(6, k // 2):
            for x in sc:
                x["narration"] = clean_text(x["narration"])
                x["hero"] = bool(x.get("hero"))
            return sc
        log("Chunk too short, retrying")
    raise SystemExit("STOP: could not write chunk")


def stage_look(stage):
    return stage["look"]


# ------------------------------------------------------------------ images
def fetch_image(prompt, seed, path):
    headers = {}
    if os.environ.get("POLLINATIONS_TOKEN"):
        headers["Authorization"] = "Bearer " + os.environ["POLLINATIONS_TOKEN"]
    models = ["flux", "turbo", "flux", "turbo", "flux"]
    for attempt in range(5):
        p = prompt if attempt < 2 else prompt[:450]          # later tries: shorter prompt
        url = "https://image.pollinations.ai/prompt/" + urllib.parse.quote(p[:800])
        try:
            r = requests.get(url, params={"width": 768, "height": 1024, "seed": seed + attempt, "nologo": "true",
                                          "model": models[attempt]}, headers=headers, timeout=150)
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
                im = Image.open(io.BytesIO(r.content)).convert("RGB")
                if min(im.size) >= 400:
                    im.save(path, quality=92)
                    return True
            log("Image service answered", r.status_code, "- retry")
        except Exception as e:
            log("Image problem:", str(e)[:120])
        time.sleep(6 * (attempt + 1))
    return False


def make_images(scenes, series, stage, part):
    seed_base = series["seed"] + 10 * [s["from_part"] for s in series["hero"]["stages"]].index(stage["from_part"])
    hero = series["hero"]
    fails = 0
    for i, s in enumerate(scenes):
        if time.time() - T0 > CFG["max_minutes"] * 60 * 0.6:
            raise SystemExit("STOP: images are taking too long")
        path = f"{WORK}/img{i:04d}.jpg"
        prompt = f"{STYLE}. {s['image']}"
        if s["hero"]:
            prompt += f". Main character {hero['name_en']} (a boy): {hero['face']}; {stage['look']}"
        seed = seed_base if s["hero"] else random.randint(1, 10**6)
        s["ok"] = fetch_image(prompt, seed, path)
        s["img"] = path
        if not s["ok"]:
            fails += 1
            log(f"Image {i} failed ({fails} fails so far)")
            if i == 4 and fails == 5:
                raise SystemExit("STOP: the free image service is not answering - not making a video without pictures")
            if i >= 20 and fails > 0.25 * (i + 1):
                raise SystemExit("STOP: the free image service keeps failing - not making a junk video")
        if i % 10 == 0:
            log(f"Images: {i + 1}/{len(scenes)}  ({(time.time() - T0) / 60:.0f} min elapsed)")
        time.sleep(CFG["image_sleep"])
    good = [i for i, s in enumerate(scenes) if s["ok"]]
    if not good:
        raise SystemExit("STOP: no picture could be made")
    for i, s in enumerate(scenes):                        # a failed picture takes the nearest good one - never a black screen
        if not s["ok"]:
            j = min(good, key=lambda g: abs(g - i))
            Image.open(scenes[j]["img"]).save(s["img"])
    log(f"Pictures ready: {len(good)} new, {len(scenes) - len(good)} copied from the nearest scene")


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
    st.setdefault("custom_series", {})
    st.setdefault("completed", [])
    ist = datetime.datetime.utcnow() + datetime.timedelta(hours=5, minutes=30)
    today = ist.date().isoformat()
    if st.get("last_date") == today and os.environ.get("FORCE") != "1":
        log("Today's video is already made. Nothing to do. (Use FORCE=1 to make another.)")
        return
    active = st["active"]
    idx = st["next_index"] % len(active)
    sid = active[idx]
    series = load_series(st, sid)
    total = series.get("total_parts", 20)
    if st["parts"][sid] >= total:                       # this series is finished -> a brand-new one takes its place
        log(f"Series '{series['title_hi']}' is complete. Creating a new series.")
        st["completed"].append(sid)
        sid = create_new_series(st)
        active[idx] = sid
        save_state(st)
        series = load_series(st, sid)
        total = series.get("total_parts", 20)
    part = st["parts"][sid] + 1
    finale = part >= total
    stage = stage_for(series, part)
    beat = get_beat(series, st, part)
    log(f"Making: {series['title_hi']}  part {part}/{total}\nBeat: {beat}\nHero stage: {stage['look']}")

    plan = plan_part(series, st, part, beat, stage, finale)
    log("Title:", plan["title_hi"])
    prev_recap = st["recaps"].get(sid, "")
    scenes, tail = [], ""
    n = CFG["chunks"]
    for i, sec in enumerate(plan["sections"]):
        sc = write_chunk(series, stage, part, i, n, sec, tail, plan, prev_recap, finale)
        scenes.extend(sc)
        tail = " ".join(x["narration"] for x in sc[-2:])[-400:]
        log(f"Script: section {i + 1}/{n} done, {len(scenes)} scenes, {sum(len(x['narration'].split()) for x in scenes)} words")
    make_images(scenes, series, stage, part)
    total_len = make_voices(scenes)
    log(f"Total voice length: {total_len / 60:.1f} minutes")
    final = f"{WORK}/part.mp4"
    build_video(scenes, final)
    log(f"Video ready: {final} ({duration(final) / 60:.1f} min, {os.path.getsize(final) / 1e6:.0f} MB)")
    json.dump({"plan": plan, "scenes": [{k: v for k, v in x.items() if k in ("narration", "image", "hero")} for x in scenes]},
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
    st["next_index"] = (idx + 1) % len(active)
    st["last_date"] = today
    st["history"].append({"date": today, "series": sid, "part": part, "title": plan["title_hi"], "video_id": vid})
    save_state(st)
    log("DONE")


if __name__ == "__main__":
    main()
