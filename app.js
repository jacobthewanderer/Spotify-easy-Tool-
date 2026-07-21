const API_BASE = "https://api.spotify.com/v1";
const AUTH_URL = "https://accounts.spotify.com/authorize";
const TOKEN_URL = "https://accounts.spotify.com/api/token";
const APP_VERSION = "2.0.0";
const SCOPES = ["user-library-read", "playlist-modify-private", "playlist-read-private"];
const PLAYLIST_CAP = 2500;
const PLAYLIST_ADD_BATCH = 100;
const SAVED_ALBUM_LIMIT = 50;
const ALBUM_TRACK_LIMIT = 50;
const MAX_TRANSIENT_RETRIES = 8;
const RETRY_DELAYS_MS = [2000, 5000, 10000, 20000, 40000, 60000, 120000, 300000];
const RATE_LIMIT_FALLBACK_MS = 12 * 60 * 60 * 1000;

const $ = id => document.getElementById(id);
const ui = {
  clientId: $("clientId"), redirectUri: $("redirectUri"), saveClientBtn: $("saveClientBtn"),
  copyRedirectBtn: $("copyRedirectBtn"), loginBtn: $("loginBtn"), logoutBtn: $("logoutBtn"),
  selfTestBtn: $("selfTestBtn"), startBtn: $("startBtn"), pauseBtn: $("pauseBtn"),
  resetBtn: $("resetBtn"), exportBtn: $("exportBtn"), statusPill: $("statusPill"),
  barFill: $("barFill"), percentText: $("percentText"), albumMetric: $("albumMetric"),
  playlistMetric: $("playlistMetric"), songMetric: $("songMetric"), totalSongMetric: $("totalSongMetric"),
  statusText: $("statusText"), log: $("log"), requestMetric: $("requestMetric"),
  retryMetric: $("retryMetric"), phaseMetric: $("phaseMetric"), versionText: $("versionText")
};

let running = false;
let pauseRequested = false;
let requestCounter = 0;
let retryCounter = 0;

function currentRedirectUri() { return location.origin + location.pathname; }
function sleep(ms) { return new Promise(resolve => setTimeout(resolve, ms)); }
function timestamp() { return new Date().toLocaleString(); }
function clamp(n, min, max) { return Math.max(min, Math.min(max, n)); }

function safeJsonParse(text, fallback = null) {
  try { return JSON.parse(text); } catch { return fallback; }
}

function log(message, level = "INFO") {
  const line = `[${timestamp()}] [${level}] ${message}`;
  ui.log.textContent += `${ui.log.textContent ? "\n" : ""}${line}`;
  ui.log.scrollTop = ui.log.scrollHeight;
  console[level === "ERROR" ? "error" : level === "WARN" ? "warn" : "log"](line);
}

function setStatus(message, level = "INFO") {
  ui.statusText.textContent = message;
  log(message, level);
}

function defaultJobState() {
  return {
    version: APP_VERSION,
    phase: "idle",
    albums: [],
    albumOffset: 0,
    currentAlbumIndex: 0,
    completedAlbumIds: [],
    albumTrackCache: {},
    playlistNumber: 1,
    currentPlaylistId: null,
    currentPlaylistName: null,
    currentPlaylistCount: 0,
    totalSongsAdded: 0,
    pendingAlbum: null,
    knownPlaylists: {},
    retryUntil: 0,
    startedAt: null,
    finishedAt: null,
    lastError: null,
    lastCheckpointAt: null
  };
}

function getJobState() {
  const raw = safeJsonParse(localStorage.getItem("album_playlist_state_v2") || "{}", {});
  return { ...defaultJobState(), ...raw };
}

function saveJobState(state) {
  state.version = APP_VERSION;
  state.lastCheckpointAt = new Date().toISOString();
  localStorage.setItem("album_playlist_state_v2", JSON.stringify(state));
  renderState(state);
}

function clearJobState() {
  localStorage.removeItem("album_playlist_state_v2");
  renderState(defaultJobState());
}

function getClientId() { return localStorage.getItem("spotify_client_id") || ""; }
function getTokenData() { return safeJsonParse(localStorage.getItem("spotify_token_v2") || "null", null); }
function saveTokenData(token) { localStorage.setItem("spotify_token_v2", JSON.stringify(token)); }
function clearToken() { localStorage.removeItem("spotify_token_v2"); }

function renderState(state = getJobState()) {
  const total = state.albums.length;
  const done = clamp(state.currentAlbumIndex, 0, total);
  const pct = total ? Math.floor(done / total * 100) : 0;
  ui.barFill.style.width = `${pct}%`;
  ui.percentText.textContent = `${pct}%`;
  ui.albumMetric.textContent = `${done} / ${total}`;
  ui.playlistMetric.textContent = state.currentPlaylistName || "—";
  ui.songMetric.textContent = `${state.currentPlaylistCount || 0} / ${PLAYLIST_CAP}`;
  ui.totalSongMetric.textContent = String(state.totalSongsAdded || 0);
  ui.phaseMetric.textContent = state.phase || "idle";
  ui.requestMetric.textContent = String(requestCounter);
  ui.retryMetric.textContent = String(retryCounter);
}

function updateAuthUi() {
  const connected = Boolean(getTokenData());
  ui.statusPill.textContent = connected ? "Connected" : "Not connected";
  ui.statusPill.classList.toggle("connected", connected);
  ui.startBtn.disabled = !connected || running;
  ui.selfTestBtn.disabled = !connected || running;
  ui.pauseBtn.disabled = !running;
}

function randomString(length = 96) {
  const alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~";
  const bytes = new Uint8Array(length);
  crypto.getRandomValues(bytes);
  return [...bytes].map(b => alphabet[b % alphabet.length]).join("");
}

function bytesToBase64Url(bytes) {
  let binary = "";
  for (const b of bytes) binary += String.fromCharCode(b);
  return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/g, "");
}

async function sha256(text) {
  return new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text)));
}

async function beginLogin() {
  const clientId = getClientId();
  if (!clientId) return alert("Save your Spotify Client ID first.");
  const verifier = randomString();
  const challenge = bytesToBase64Url(await sha256(verifier));
  const oauthState = randomString(32);
  sessionStorage.setItem("spotify_pkce_verifier", verifier);
  sessionStorage.setItem("spotify_oauth_state", oauthState);
  const params = new URLSearchParams({
    client_id: clientId,
    response_type: "code",
    redirect_uri: currentRedirectUri(),
    scope: SCOPES.join(" "),
    code_challenge_method: "S256",
    code_challenge: challenge,
    state: oauthState,
    show_dialog: "true"
  });
  location.href = `${AUTH_URL}?${params}`;
}

async function finishLoginIfNeeded() {
  const params = new URLSearchParams(location.search);
  const oauthError = params.get("error");
  if (oauthError) throw new Error(`Spotify authorization failed: ${oauthError}`);
  const code = params.get("code");
  if (!code) return;
  if (params.get("state") !== sessionStorage.getItem("spotify_oauth_state")) {
    throw new Error("OAuth security check failed. Start login again.");
  }
  const verifier = sessionStorage.getItem("spotify_pkce_verifier");
  if (!verifier) throw new Error("PKCE verifier is missing. Start login again.");
  const body = new URLSearchParams({
    client_id: getClientId(),
    grant_type: "authorization_code",
    code,
    redirect_uri: currentRedirectUri(),
    code_verifier: verifier
  });
  const response = await fetch(TOKEN_URL, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body
  });
  const text = await response.text();
  if (!response.ok) throw new Error(`Token exchange failed (${response.status}): ${text}`);
  const token = safeJsonParse(text);
  token.expires_at = Date.now() + token.expires_in * 1000 - 60000;
  saveTokenData(token);
  history.replaceState({}, document.title, currentRedirectUri());
  sessionStorage.removeItem("spotify_pkce_verifier");
  sessionStorage.removeItem("spotify_oauth_state");
  setStatus("Spotify login approved.");
}

async function accessToken() {
  let token = getTokenData();
  if (!token) throw new Error("Not logged in. Press Log in with Spotify.");
  if (Date.now() < Number(token.expires_at || 0)) return token.access_token;
  if (!token.refresh_token) {
    clearToken();
    throw new Error("Spotify login expired. Log in again.");
  }
  const body = new URLSearchParams({
    client_id: getClientId(),
    grant_type: "refresh_token",
    refresh_token: token.refresh_token
  });
  const response = await fetch(TOKEN_URL, {
    method: "POST",
    headers: { "Content-Type": "application/x-www-form-urlencoded" },
    body
  });
  const text = await response.text();
  if (!response.ok) {
    clearToken();
    throw new Error(`Token refresh failed (${response.status}): ${text}`);
  }
  const fresh = safeJsonParse(text);
  token = { ...token, ...fresh, refresh_token: fresh.refresh_token || token.refresh_token };
  token.expires_at = Date.now() + token.expires_in * 1000 - 60000;
  saveTokenData(token);
  return token.access_token;
}

function describeFetchFailure(error, method, url) {
  const online = navigator.onLine ? "online" : "offline";
  return `${method} ${url} failed before Spotify returned an HTTP response. Browser reports: ${error?.message || error}. Network state: ${online}. This is commonly caused by a temporary connection problem, privacy/content blocking, VPN filtering, or the page being opened outside HTTPS/GitHub Pages.`;
}

async function spotifyRequest(pathOrUrl, options = {}) {
  const url = pathOrUrl.startsWith("http") ? pathOrUrl : `${API_BASE}${pathOrUrl}`;
  const method = (options.method || "GET").toUpperCase();
  let transientAttempt = 0;

  while (true) {
    if (pauseRequested && running) throw new Error("PAUSE_REQUESTED");

    const job = getJobState();
    if (job.retryUntil && Date.now() < job.retryUntil) {
      const remaining = job.retryUntil - Date.now();
      setStatus(`Spotify rate limit wait is active. Retrying in about ${Math.ceil(remaining / 60000)} minute(s).`, "WARN");
      await sleep(Math.min(60000, remaining));
      continue;
    }

    requestCounter += 1;
    renderState(job);
    const headers = new Headers(options.headers || {});
    headers.set("Authorization", `Bearer ${await accessToken()}`);
    headers.set("Accept", "application/json");
    if (options.body && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");

    let response;
    const started = performance.now();
    try {
      response = await fetch(url, { ...options, method, headers, cache: "no-store" });
    } catch (error) {
      if (transientAttempt >= MAX_TRANSIENT_RETRIES) {
        throw new Error(describeFetchFailure(error, method, url));
      }
      const delay = RETRY_DELAYS_MS[transientAttempt] || 300000;
      transientAttempt += 1;
      retryCounter += 1;
      renderState(getJobState());
      setStatus(`Network request could not load. Retry ${transientAttempt}/${MAX_TRANSIENT_RETRIES} in ${Math.round(delay / 1000)} seconds.`, "WARN");
      log(describeFetchFailure(error, method, url), "WARN");
      await sleep(delay);
      continue;
    }

    const elapsed = Math.round(performance.now() - started);
    const responseText = await response.text();
    log(`${method} ${url} → ${response.status} in ${elapsed} ms`);

    if (response.status === 429) {
      const retryAfterSeconds = Number(response.headers.get("Retry-After") || 0);
      const waitMs = retryAfterSeconds > 0 ? retryAfterSeconds * 1000 : RATE_LIMIT_FALLBACK_MS;
      const state = getJobState();
      state.retryUntil = Date.now() + waitMs;
      state.lastError = { status: 429, url, method, response: responseText, at: new Date().toISOString() };
      saveJobState(state);
      retryCounter += 1;
      setStatus(`Spotify returned 429. Progress saved. Retrying after ${Math.ceil(waitMs / 60000)} minute(s).`, "WARN");
      continue;
    }

    if (response.status === 401) {
      const token = getTokenData();
      if (token) {
        token.expires_at = 0;
        saveTokenData(token);
      }
      if (transientAttempt < 1) {
        transientAttempt += 1;
        retryCounter += 1;
        await accessToken();
        continue;
      }
    }

    if ([500, 502, 503, 504].includes(response.status) && transientAttempt < MAX_TRANSIENT_RETRIES) {
      const delay = RETRY_DELAYS_MS[transientAttempt] || 300000;
      transientAttempt += 1;
      retryCounter += 1;
      setStatus(`Spotify server error ${response.status}. Retry ${transientAttempt}/${MAX_TRANSIENT_RETRIES} in ${Math.round(delay / 1000)} seconds.`, "WARN");
      await sleep(delay);
      continue;
    }

    if (!response.ok) {
      const state = getJobState();
      state.lastError = { status: response.status, url, method, response: responseText, at: new Date().toISOString() };
      saveJobState(state);
      let detail = responseText;
      const parsed = safeJsonParse(responseText);
      if (parsed?.error?.message) detail = parsed.error.message;
      throw new Error(`Spotify API ${response.status} during ${method} ${url}: ${detail}`);
    }

    const state = getJobState();
    if (state.retryUntil) {
      state.retryUntil = 0;
      saveJobState(state);
    }
    return responseText ? safeJsonParse(responseText, responseText) : null;
  }
}

async function selfTest() {
  ui.selfTestBtn.disabled = true;
  try {
    setStatus("Running connection and permission tests…");
    const profile = await spotifyRequest("/me");
    const albums = await spotifyRequest("/me/albums?limit=1&offset=0");
    if (!profile?.id) throw new Error("Profile test returned no user ID.");
    if (!Array.isArray(albums?.items)) throw new Error("Saved-album test returned an unexpected response.");
    setStatus(`Self-test passed for ${profile.display_name || profile.id}. Spotify library access is working.`);
  } catch (error) {
    setStatus(`SELF-TEST FAILED: ${error.message || error}`, "ERROR");
  } finally {
    updateAuthUi();
  }
}

async function collectSavedAlbums(state) {
  state.phase = "collecting albums";
  saveJobState(state);
  let offset = state.albumOffset || 0;
  while (true) {
    const page = await spotifyRequest(`/me/albums?limit=${SAVED_ALBUM_LIMIT}&offset=${offset}`);
    const items = Array.isArray(page?.items) ? page.items : [];
    for (const item of items) {
      const album = item.album;
      if (!album?.id || state.albums.some(existing => existing.id === album.id)) continue;
      state.albums.push({
        id: album.id,
        uri: album.uri,
        name: album.name,
        artist: (album.artists || []).map(a => a.name).join(", "),
        total_tracks: album.total_tracks || 0,
        added_at: item.added_at || ""
      });
    }
    offset += items.length;
    state.albumOffset = offset;
    saveJobState(state);
    setStatus(`Collected ${state.albums.length} saved album(s).`);
    if (!page?.next || items.length === 0) break;
  }
  state.albumOffset = 0;
  state.phase = "building playlists";
  saveJobState(state);
}

async function getAlbumTrackUris(album, state) {
  if (Array.isArray(state.albumTrackCache[album.id])) return state.albumTrackCache[album.id];
  const uris = [];
  let offset = 0;
  while (true) {
    const page = await spotifyRequest(`/albums/${album.id}/tracks?limit=${ALBUM_TRACK_LIMIT}&offset=${offset}`);
    const items = Array.isArray(page?.items) ? page.items : [];
    for (const track of items) if (track?.uri) uris.push(track.uri);
    offset += items.length;
    if (!page?.next || items.length === 0) break;
  }
  state.albumTrackCache[album.id] = uris;
  saveJobState(state);
  return uris;
}

async function getOwnedAlbumPlaylists() {
  const found = {};
  let url = "/me/playlists?limit=50";
  while (url) {
    const page = await spotifyRequest(url);
    for (const playlist of page?.items || []) {
      const match = /^Album Playlist (\d+)$/i.exec(playlist?.name || "");
      if (match && playlist?.id) {
        found[Number(match[1])] = {
          id: playlist.id,
          name: playlist.name,
          total: Number(playlist?.items?.total ?? playlist?.tracks?.total ?? 0)
        };
      }
    }
    url = page?.next || null;
  }
  return found;
}

async function createOrReusePlaylist(number, state) {
  if (!Object.keys(state.knownPlaylists || {}).length) {
    setStatus("Checking for existing Album Playlist playlists…");
    state.knownPlaylists = await getOwnedAlbumPlaylists();
    saveJobState(state);
  }
  const existing = state.knownPlaylists[number];
  if (existing) {
    state.currentPlaylistId = existing.id;
    state.currentPlaylistName = existing.name;
    state.currentPlaylistCount = existing.total;
    saveJobState(state);
    log(`Reusing ${existing.name} with ${existing.total} existing item(s).`);
    return;
  }
  const name = `Album Playlist ${number}`;
  const playlist = await spotifyRequest("/me/playlists", {
    method: "POST",
    body: JSON.stringify({
      name,
      public: false,
      description: "Automatically generated from saved Spotify albums. Albums are kept together where possible."
    })
  });
  if (!playlist?.id) throw new Error(`Spotify did not return an ID for ${name}.`);
  state.currentPlaylistId = playlist.id;
  state.currentPlaylistName = name;
  state.currentPlaylistCount = 0;
  state.knownPlaylists[number] = { id: playlist.id, name, total: 0 };
  saveJobState(state);
  setStatus(`Created ${name}.`);
}

async function ensureCurrentPlaylist(state) {
  if (!state.currentPlaylistId) await createOrReusePlaylist(state.playlistNumber, state);
}

async function advancePlaylist(state) {
  state.playlistNumber += 1;
  state.currentPlaylistId = null;
  state.currentPlaylistName = null;
  state.currentPlaylistCount = 0;
  saveJobState(state);
  await ensureCurrentPlaylist(state);
}

async function addUrisToCurrentPlaylist(uris, state) {
  for (let i = 0; i < uris.length; i += PLAYLIST_ADD_BATCH) {
    const batch = uris.slice(i, i + PLAYLIST_ADD_BATCH);
    await spotifyRequest(`/playlists/${state.currentPlaylistId}/items`, {
      method: "POST",
      body: JSON.stringify({ uris: batch })
    });
    state.currentPlaylistCount += batch.length;
    state.totalSongsAdded += batch.length;
    if (state.pendingAlbum) state.pendingAlbum.added = Number(state.pendingAlbum.added || 0) + batch.length;
    if (state.knownPlaylists[state.playlistNumber]) state.knownPlaylists[state.playlistNumber].total = state.currentPlaylistCount;
    saveJobState(state);
    setStatus(`Added ${batch.length} song(s) to ${state.currentPlaylistName}.`);
  }
}

async function processAlbum(album, state) {
  const fullUris = await getAlbumTrackUris(album, state);
  if (!fullUris.length) {
    log(`Skipped album with no available tracks: ${album.artist} — ${album.name}`, "WARN");
    return;
  }

  let alreadyAdded = 0;
  if (state.pendingAlbum?.albumId === album.id) alreadyAdded = Number(state.pendingAlbum.added || 0);
  else {
    state.pendingAlbum = { albumId: album.id, added: 0, total: fullUris.length };
    saveJobState(state);
  }

  await ensureCurrentPlaylist(state);
  if (alreadyAdded === 0 && state.currentPlaylistCount > 0 && state.currentPlaylistCount + fullUris.length > PLAYLIST_CAP) {
    await advancePlaylist(state);
  }

  let remaining = fullUris.slice(alreadyAdded);
  while (remaining.length) {
    let capacity = PLAYLIST_CAP - state.currentPlaylistCount;
    if (capacity <= 0) {
      await advancePlaylist(state);
      capacity = PLAYLIST_CAP;
    }
    const section = remaining.slice(0, capacity);
    await addUrisToCurrentPlaylist(section, state);
    remaining = remaining.slice(section.length);
  }

  if (!state.completedAlbumIds.includes(album.id)) state.completedAlbumIds.push(album.id);
  state.pendingAlbum = null;
  saveJobState(state);
  log(`Completed album: ${album.artist} — ${album.name}`);
}

async function runBuilder() {
  if (running) return;
  if (!getTokenData()) return alert("Log in with Spotify first.");
  running = true;
  pauseRequested = false;
  updateAuthUi();

  try {
    let state = getJobState();
    if (!state.startedAt) state.startedAt = new Date().toISOString();
    state.lastError = null;
    saveJobState(state);

    if (!state.albums.length || state.phase === "collecting albums") {
      await collectSavedAlbums(state);
      state = getJobState();
    }

    if (!Object.keys(state.knownPlaylists || {}).length) {
      state.knownPlaylists = await getOwnedAlbumPlaylists();
      saveJobState(state);
    }

    state.phase = "building playlists";
    saveJobState(state);

    for (let i = state.currentAlbumIndex; i < state.albums.length; i++) {
      if (pauseRequested) break;
      state = getJobState();
      state.currentAlbumIndex = i;
      saveJobState(state);
      const album = state.albums[i];
      if (state.completedAlbumIds.includes(album.id)) {
        state.currentAlbumIndex = i + 1;
        saveJobState(state);
        continue;
      }
      setStatus(`Processing ${album.artist} — ${album.name}`);
      await processAlbum(album, state);
      state = getJobState();
      state.currentAlbumIndex = i + 1;
      saveJobState(state);
    }

    state = getJobState();
    if (!pauseRequested && state.currentAlbumIndex >= state.albums.length) {
      state.phase = "complete";
      state.finishedAt = new Date().toISOString();
      saveJobState(state);
      setStatus(`Complete. ${state.totalSongsAdded} songs were added through ${state.currentPlaylistName || `Album Playlist ${state.playlistNumber}`}.`);
    } else {
      state.phase = "paused";
      saveJobState(state);
      setStatus("Paused safely. Press Start / Resume to continue.");
    }
  } catch (error) {
    if (error?.message === "PAUSE_REQUESTED") {
      const state = getJobState();
      state.phase = "paused";
      saveJobState(state);
      setStatus("Paused safely. Press Start / Resume to continue.");
    } else {
      const state = getJobState();
      state.phase = "error";
      state.lastError = state.lastError || { message: error?.message || String(error), at: new Date().toISOString() };
      saveJobState(state);
      setStatus(`ERROR: ${error?.message || error}`, "ERROR");
    }
  } finally {
    running = false;
    updateAuthUi();
  }
}

function exportDiagnostics() {
  const payload = {
    appVersion: APP_VERSION,
    exportedAt: new Date().toISOString(),
    location: { origin: location.origin, pathname: location.pathname, protocol: location.protocol },
    browser: navigator.userAgent,
    online: navigator.onLine,
    hasClientId: Boolean(getClientId()),
    hasToken: Boolean(getTokenData()),
    jobState: getJobState(),
    activityLog: ui.log.textContent
  };
  const blob = new Blob([JSON.stringify(payload, null, 2)], { type: "application/json" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `spotify-album-playlist-diagnostics-${Date.now()}.json`;
  a.click();
  URL.revokeObjectURL(a.href);
  setStatus("Diagnostics file exported.");
}

ui.saveClientBtn.addEventListener("click", () => {
  const id = ui.clientId.value.trim();
  if (!id) return alert("Enter your Spotify Client ID.");
  localStorage.setItem("spotify_client_id", id);
  setStatus("Client ID saved.");
});
ui.copyRedirectBtn.addEventListener("click", async () => {
  await navigator.clipboard.writeText(currentRedirectUri());
  setStatus("Redirect URI copied.");
});
ui.loginBtn.addEventListener("click", beginLogin);
ui.logoutBtn.addEventListener("click", () => { clearToken(); updateAuthUi(); setStatus("Spotify login removed from this browser."); });
ui.selfTestBtn.addEventListener("click", selfTest);
ui.startBtn.addEventListener("click", runBuilder);
ui.pauseBtn.addEventListener("click", () => { pauseRequested = true; setStatus("Pause requested. Stopping at the next safe checkpoint.", "WARN"); });
ui.resetBtn.addEventListener("click", () => {
  if (!confirm("Reset saved progress? Existing Spotify playlists will not be deleted.")) return;
  clearJobState();
  setStatus("Saved progress reset. Existing Spotify playlists were not deleted.");
});
ui.exportBtn.addEventListener("click", exportDiagnostics);

(async function init() {
  ui.redirectUri.textContent = currentRedirectUri();
  ui.clientId.value = getClientId();
  ui.versionText.textContent = `v${APP_VERSION}`;
  renderState();
  try { await finishLoginIfNeeded(); }
  catch (error) { setStatus(`LOGIN ERROR: ${error.message || error}`, "ERROR"); }
  updateAuthUi();
  window.addEventListener("online", () => setStatus("Browser is back online."));
  window.addEventListener("offline", () => setStatus("Browser is offline. Work will resume when connectivity returns.", "WARN"));
  window.addEventListener("unhandledrejection", event => log(`Unhandled promise rejection: ${event.reason?.message || event.reason}`, "ERROR"));
  window.addEventListener("error", event => log(`Browser error: ${event.message}`, "ERROR"));
})();
