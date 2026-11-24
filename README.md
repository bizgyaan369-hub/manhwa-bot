# Manhwa Part Bot (free)

Roz ek bhaag (~1 ghanta) banata hai, 4 series ghoomti rehti hain: A -> B -> C -> D -> A ... (series khatam hone par nayi series apne aap banti hai)

## Kya karta hai
1. `state/state.json` se dekhta hai aaj kaunsi series aur kaun sa bhaag.
2. Gemini (free key) kahani likhta hai: 12 hisse x 12 scene.
3. Pollinations (free) har scene ki alag image banata hai. Hero ke 3 roop bhaag-number se badalte hain.
4. edge-tts (free) Hindi awaaz.
5. ffmpeg video banata hai: beech mein panel + dhundhla background + dheere zoom.
6. UPLOAD=1 ho to YouTube pe daalta hai (pehle private).
7. Progress `state/` mein save.

## GitHub Secrets (Settings -> Secrets and variables -> Actions -> Secrets)
GEMINI_API_KEY, YT_CLIENT_ID, YT_CLIENT_SECRET, YT_REFRESH_TOKEN
(optional) POLLINATIONS_TOKEN

## Variables (same jagah, Variables tab)
UPLOAD = 1   (jab test video theek lage tab)
PRIVACY = private

## Pehli baar
Actions -> "Manhwa Part (daily)" -> Run workflow -> test = 1  (sirf video-builder ka test)
Phir Run workflow -> test = 0 (asli bhaag, UPLOAD abhi 0 rakho, video "part-video" mein download hoga)

## Badalne ki jagah
- config.json : awaaz, kitne hisse/scene
- series/a.json, b.json, c.json : kahani, hero ke roop, outline
