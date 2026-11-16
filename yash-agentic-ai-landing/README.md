# YASH Agentic AI – Landing Page (static site)

Plain HTML/CSS/JS. No build step. All paths are relative.

```
index.html          page markup (all sections)
css/styles.css      styles (palette in :root)
js/main.js          destinations dialog, nav, menu, scroll reveal, parallax
destinations.js     button targets (window.YASH_DESTINATIONS)
assets/             genie image, hero loop video + poster, product video + poster, favicon, OG image
manifest.yml        Cloud Foundry manifest (staticfile buildpack)
Staticfile          staticfile buildpack config
```

## Update the agent cards
In `index.html`, section `id="agents"`: each agent is an `<article class="card agent">` with an icon,
`<h3>` name, `<p>` description and status pills:
`<span class="pill dev">In Development</span>`, `<span class="pill ready">Demo Ready</span>`.
Copy an `<article>` to add an agent. Text currently comes from the SAP AI Tools Catalog
(13-Nov-2026, "Purpose Of The Tool", "Development Status", "Is it Demo Ready?").

## Case studies and contact
Case study cards (`id="cases"`) and the footer contact are `[placeholders]` – replace with real content.

## Button destinations
Edit `destinations.js`:
```js
window.YASH_DESTINATIONS = { dashboard: "https://…", caseStudies: "https://…" };
```
An empty value shows the "not connected yet" dialog. Buttons use `data-destination="dashboard"` / `"caseStudies"`.

## Videos
- Hero background loop: `assets/hero-loop.mp4` (H.264) + `assets/hero-loop.webm` (VP9), poster `assets/hero-poster.jpg`.
  Muted, autoplay, loop, playsinline; hidden (poster only) with prefers-reduced-motion. Keep both files under ~4 MB.
- Product video: `assets/capacity-bottleneck-agent.mp4` (+ `capacity-bottleneck-agent-poster.jpg`), controls, no autoplay.
  To replace: `ffmpeg -i new.mp4 -vf scale=1920:-2 -c:v libx264 -crf 26 -c:a aac -b:a 128k -movflags +faststart assets/capacity-bottleneck-agent.mp4`

## Run locally
`python3 -m http.server 8080` in this folder, then open http://localhost:8080

## Deploy to SAP BTP Cloud Foundry
```
cf login -a https://api.cf.eu10-004.hana.ondemand.com
cf push                      # uses manifest.yml (staticfile_buildpack)
```
To update the existing app, change `name:` in manifest.yml to the current app name and remove `random-route`
(or set `routes:`) before `cf push`.
