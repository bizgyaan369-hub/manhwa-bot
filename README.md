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

## Image provider fallback (optional keys)
Image generation now tries providers in this order:
1. Cloudflare Workers AI FLUX.1 Schnell (`CLOUDFLARE_ACCOUNT_ID` + `CLOUDFLARE_API_TOKEN`)
2. SiliconFlow (`SILICONFLOW_API_KEY`; optional variable `SILICONFLOW_IMAGE_MODEL`, default `Kwai-Kolors/Kolors`)
3. Pollinations (`POLLINATIONS_TOKEN` optional)

Add keys only in GitHub: Settings -> Secrets and variables -> Actions -> New repository secret.
No API key is included in this ZIP. Free quotas and eligibility can change; a free tier is not a promise of unlimited images. If no provider credentials/service are available, image generation stops instead of silently producing an empty video. SiliconFlow account/model limits may differ; the script spaces its requests by at least 31 seconds.

The script now requires exactly the configured number of sections and scenes (default 12 x 12 = 144) so a short AI response cannot silently create a short episode. New custom series use expandable IDs instead of stopping after 26 series.
