from __future__ import annotations

import base64
import csv
import hashlib
import json
import os
import re
import secrets
import shutil
import threading
import time
import traceback
import urllib.parse
import uuid
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

import requests
from flask import Flask, jsonify, redirect, render_template, request, send_file, session, url_for

APP_NAME = "Spotify Library Manager"
APP_VERSION = "2.0.0-github"
DEFAULT_CLIENT_ID = "b8db26e7cfff4475804ce186878db270"
DEFAULT_REDIRECT_URI = "http://127.0.0.1:8765/callback"
SPOTIFY_API = "https://api.spotify.com/v1"
SPOTIFY_ACCOUNTS = "https://accounts.spotify.com"

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
EXPORT_DIR = DATA_DIR / "exports"
CONFIG_FILE = DATA_DIR / "config.json"
TOKEN_FILE = DATA_DIR / "token.json"
JOBS_FILE = DATA_DIR / "jobs.json"

DEFAULT_SCOPES = " ".join(
    [
        "user-read-private",
        "user-read-email",
        "user-library-read",
        "user-library-modify",
        "playlist-read-private",
        "playlist-read-collaborative",
        "playlist-modify-private",
        "playlist-modify-public",
        "user-follow-read",
        "user-follow-modify",
    ]
)

RATE_MODES = {
    "fast": 0.25,
    "gentle": 0.65,
    "slow": 1.25,
    "ultra": 2.50,
}

DATA_DIR.mkdir(exist_ok=True)
EXPORT_DIR.mkdir(exist_ok=True)

app = Flask(__name__)
app.secret_key = os.environ.get("SPOTIFY_MANAGER_SECRET", secrets.token_hex(32))

store_lock = threading.RLock()
job_threads: Dict[str, threading.Thread] = {}


class SpotifyError(RuntimeError):
    """User-facing Spotify/API error."""


class JobCancelled(RuntimeError):
    """Raised when a running job was cancelled by the user."""


def now_ts() -> int:
    return int(time.time())


def read_json(path: Path, default: Any) -> Any:
    with store_lock:
        if not path.exists():
            return default
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default


def write_json(path: Path, data: Any) -> None:
    with store_lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)


def get_config() -> Dict[str, Any]:
    config = read_json(CONFIG_FILE, {})
    if not isinstance(config, dict):
        config = {}
    config.setdefault("client_id", os.environ.get("SPOTIFY_CLIENT_ID", DEFAULT_CLIENT_ID))
    config.setdefault("redirect_uri", os.environ.get("SPOTIFY_REDIRECT_URI", DEFAULT_REDIRECT_URI))
    config.setdefault("rate_mode", "gentle")
    config.setdefault("min_delay_seconds", RATE_MODES["gentle"])
    config.setdefault("max_retries", 10)
    config.setdefault("library_batch_size", 40)
    config.setdefault("playlist_batch_size", 100)
    return config


def save_config(config: Dict[str, Any]) -> None:
    write_json(CONFIG_FILE, config)


def get_token() -> Optional[Dict[str, Any]]:
    token = read_json(TOKEN_FILE, None)
    return token if isinstance(token, dict) else None


def save_token(token: Dict[str, Any]) -> None:
    write_json(TOKEN_FILE, token)


def delete_token() -> None:
    with store_lock:
        if TOKEN_FILE.exists():
            TOKEN_FILE.unlink()


def get_jobs() -> Dict[str, Any]:
    jobs = read_json(JOBS_FILE, {})
    return jobs if isinstance(jobs, dict) else {}


def save_jobs(jobs: Dict[str, Any]) -> None:
    write_json(JOBS_FILE, jobs)


def update_job(job_id: str, **updates: Any) -> None:
    with store_lock:
        jobs = get_jobs()
        job = jobs.setdefault(job_id, {})
        job.update(updates)
        job["updated_at"] = now_ts()
        save_jobs(jobs)


def append_log(job_id: str, message: str) -> None:
    with store_lock:
        jobs = get_jobs()
        job = jobs.setdefault(job_id, {})
        logs = job.setdefault("logs", [])
        timestamp = time.strftime("%H:%M:%S")
        logs.append(f"[{timestamp}] {message}")
        job["logs"] = logs[-350:]
        job["updated_at"] = now_ts()
        save_jobs(jobs)


def unique_preserve_order(values: Iterable[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            out.append(value)
    return out


def chunked(values: List[str], size: int) -> Iterable[List[str]]:
    size = max(1, int(size))
    for i in range(0, len(values), size):
        yield values[i : i + size]


def safe_filename(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._ -]+", "_", name).strip().replace(" ", "_")
    return cleaned or "export"


def normalize_spotify_id(value: str, expected_type: Optional[str] = None) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    if value.startswith("spotify:"):
        parts = value.split(":")
        if len(parts) >= 3 and (expected_type is None or parts[1] == expected_type):
            return parts[2]
    match = re.search(r"open\.spotify\.com/(artist|album|track|playlist|user)/([A-Za-z0-9]+)", value)
    if match and (expected_type is None or match.group(1) == expected_type):
        return match.group(2)
    if re.fullmatch(r"[A-Za-z0-9]{10,64}", value):
        return value
    return ""


def uri_from_item(kind: str, item_id: str) -> str:
    return f"spotify:{kind}:{item_id}"


def artist_names(artists: List[Dict[str, Any]]) -> str:
    return ", ".join(a.get("name", "") for a in artists or [] if a.get("name"))


def split_lines(value: str) -> List[str]:
    return [line.strip() for line in re.split(r"[\n;]+", value or "") if line.strip()]


def generate_code_verifier() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(64)).decode("ascii").rstrip("=")[:128]


def generate_code_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def require_confirm(params: Dict[str, Any], phrase: str = "DELETE") -> None:
    if str(params.get("confirm", "")).strip() != phrase:
        raise SpotifyError(f"This destructive action requires confirm={phrase!r}.")


@dataclass
class JobContext:
    job_id: str

    def snapshot(self) -> Dict[str, Any]:
        return get_jobs().get(self.job_id) or {}

    def cancel_requested(self) -> bool:
        job = self.snapshot()
        return bool(job.get("cancel_requested")) or job.get("status") == "cancelling"

    def check_cancelled(self) -> None:
        if self.cancel_requested():
            raise JobCancelled("Job cancelled by user.")

    def sleep(self, seconds: float) -> None:
        end = time.monotonic() + max(0.0, float(seconds))
        while True:
            self.check_cancelled()
            remaining = end - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(0.5, remaining))

    def log(self, message: str) -> None:
        append_log(self.job_id, message)

    def progress(self, done: int, total: int, status: Optional[str] = None) -> None:
        self.check_cancelled()
        pct = 0 if total <= 0 else min(100, round(done * 100 / total, 1))
        update_job(self.job_id, done=done, total=total, progress=pct, status=status or "running")


class SpotifyClient:
    def __init__(self, job: Optional[JobContext] = None):
        self.config = get_config()
        self.job = job
        self.session = requests.Session()
        self.last_call = 0.0

    def _sleep_for_rate_limit(self) -> None:
        if self.job:
            self.job.check_cancelled()
        delay = max(0.0, float(self.config.get("min_delay_seconds", RATE_MODES["gentle"])))
        elapsed = time.monotonic() - self.last_call
        if elapsed < delay:
            if self.job:
                self.job.sleep(delay - elapsed)
            else:
                time.sleep(delay - elapsed)
        if self.job:
            self.job.check_cancelled()

    def _set_last_call(self) -> None:
        self.last_call = time.monotonic()

    def ensure_token(self) -> Dict[str, Any]:
        token = get_token()
        if not token:
            raise SpotifyError("Not logged in. Open the app and click Log in with Spotify.")
        if token.get("expires_at", 0) <= now_ts() + 60:
            token = self.refresh_token(token)
        return token

    def refresh_token(self, token: Dict[str, Any]) -> Dict[str, Any]:
        refresh_token = token.get("refresh_token")
        client_id = self.config.get("client_id") or DEFAULT_CLIENT_ID
        if not refresh_token or not client_id:
            raise SpotifyError("Missing refresh token or Client ID. Reset token and log in again.")
        response = requests.post(
            f"{SPOTIFY_ACCOUNTS}/api/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": client_id,
            },
            timeout=30,
        )
        if response.status_code >= 400:
            raise SpotifyError(f"Token refresh failed: {response.status_code} {response.text[:1000]}")
        new_token = response.json()
        if "refresh_token" not in new_token:
            new_token["refresh_token"] = refresh_token
        new_token["expires_at"] = now_ts() + int(new_token.get("expires_in", 3600))
        save_token(new_token)
        return new_token

    def _auth_headers(self) -> Dict[str, str]:
        token = self.ensure_token()
        return {"Authorization": f"Bearer {token['access_token']}"}

    def request(
        self,
        method: str,
        path_or_url: str,
        *,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Any] = None,
        expected: Tuple[int, ...] = (200, 201, 202, 204),
    ) -> Any:
        url = path_or_url if path_or_url.startswith("http") else f"{SPOTIFY_API}{path_or_url}"
        max_retries = int(self.config.get("max_retries", 10))
        last_error = ""

        for attempt in range(max_retries + 1):
            if self.job:
                self.job.check_cancelled()
            self._sleep_for_rate_limit()
            try:
                response = self.session.request(
                    method,
                    url,
                    headers={**self._auth_headers(), "Content-Type": "application/json"},
                    params=params,
                    json=json_body,
                    timeout=60,
                )
                self._set_last_call()
                if self.job:
                    self.job.check_cancelled()
            except requests.RequestException as exc:
                last_error = str(exc)
                wait = min(60, 2**attempt)
                if self.job:
                    self.job.log(f"Network error; retrying in {wait}s: {last_error}")
                    self.job.sleep(wait)
                else:
                    time.sleep(wait)
                continue

            if response.status_code == 401 and attempt < max_retries:
                if self.job:
                    self.job.log("Access token expired; refreshing token.")
                self.refresh_token(get_token() or {})
                continue

            if response.status_code == 429 and attempt < max_retries:
                retry_after = response.headers.get("Retry-After")
                try:
                    wait = int(float(retry_after)) if retry_after else min(120, 5 + 2**attempt)
                except ValueError:
                    wait = min(120, 5 + 2**attempt)
                wait += 1
                if self.job:
                    self.job.log(f"Spotify rate-limited the app. Waiting {wait}s before retrying.")
                    self.job.sleep(wait)
                else:
                    time.sleep(wait)
                continue

            if response.status_code in (500, 502, 503, 504) and attempt < max_retries:
                wait = min(90, 2**attempt)
                if self.job:
                    self.job.log(f"Spotify server error {response.status_code}. Retrying in {wait}s.")
                    self.job.sleep(wait)
                else:
                    time.sleep(wait)
                continue

            if response.status_code not in expected:
                raise SpotifyError(f"Spotify API error {response.status_code}: {response.text[:1000]}")

            if response.status_code == 204 or not response.content:
                return None
            try:
                return response.json()
            except ValueError:
                return response.text

        raise SpotifyError(f"Request failed after retries: {last_error or path_or_url}")

    def paginate(self, path: str, params: Optional[Dict[str, Any]] = None, item_key: str = "items") -> Iterable[Dict[str, Any]]:
        next_url: Optional[str] = None
        first = True
        params = dict(params or {})
        while first or next_url:
            if self.job:
                self.job.check_cancelled()
            first = False
            data = self.request("GET", next_url or path, params=params if not next_url else None)
            if not data:
                break
            for item in data.get(item_key, []):
                if self.job:
                    self.job.check_cancelled()
                yield item
            next_url = data.get("next")
            params = None

    def me(self) -> Dict[str, Any]:
        return self.request("GET", "/me")

    def save_to_library(self, uris: List[str], ctx: JobContext, label: str = "items") -> int:
        uris = unique_preserve_order([u for u in uris if u])
        batch_size = min(40, int(self.config.get("library_batch_size", 40)))
        total = len(uris)
        done = 0
        for batch in chunked(uris, batch_size):
            ctx.check_cancelled()
            self.request("PUT", "/me/library", params={"uris": ",".join(batch)}, expected=(200, 201, 204))
            done += len(batch)
            ctx.progress(done, total)
            ctx.log(f"Saved/followed {done}/{total} {label}.")
        return total

    def remove_from_library(self, uris: List[str], ctx: JobContext, label: str = "items") -> int:
        uris = unique_preserve_order([u for u in uris if u])
        batch_size = min(40, int(self.config.get("library_batch_size", 40)))
        total = len(uris)
        done = 0
        for batch in chunked(uris, batch_size):
            ctx.check_cancelled()
            self.request("DELETE", "/me/library", params={"uris": ",".join(batch)}, expected=(200, 202, 204))
            done += len(batch)
            ctx.progress(done, total)
            ctx.log(f"Removed/unfollowed {done}/{total} {label}.")
        return total

    def add_items_to_playlist(self, playlist_id: str, uris: List[str], ctx: JobContext) -> int:
        uris = unique_preserve_order([u for u in uris if u])
        batch_size = min(100, int(self.config.get("playlist_batch_size", 100)))
        total = len(uris)
        done = 0
        for batch in chunked(uris, batch_size):
            ctx.check_cancelled()
            self.request("POST", f"/playlists/{playlist_id}/items", json_body={"uris": batch}, expected=(200, 201))
            done += len(batch)
            ctx.progress(done, total)
            ctx.log(f"Added {done}/{total} tracks to playlist.")
        return total


def collect_liked_tracks(sp: SpotifyClient, ctx: Optional[JobContext] = None) -> List[Dict[str, Any]]:
    tracks: List[Dict[str, Any]] = []
    for item in sp.paginate("/me/tracks", {"limit": 50}):
        track = item.get("track") or {}
        if track.get("type") == "track" and track.get("uri"):
            tracks.append(item)
            if ctx and len(tracks) % 500 == 0:
                ctx.log(f"Read {len(tracks)} liked songs...")
    if ctx:
        ctx.log(f"Found {len(tracks)} liked songs.")
    return tracks


def collect_saved_albums(sp: SpotifyClient, ctx: Optional[JobContext] = None) -> List[Dict[str, Any]]:
    albums: List[Dict[str, Any]] = []
    for item in sp.paginate("/me/albums", {"limit": 50}):
        album = item.get("album") or {}
        if album.get("uri"):
            albums.append(item)
            if ctx and len(albums) % 500 == 0:
                ctx.log(f"Read {len(albums)} saved albums...")
    if ctx:
        ctx.log(f"Found {len(albums)} saved albums.")
    return albums


def collect_playlists(sp: SpotifyClient, ctx: Optional[JobContext] = None) -> List[Dict[str, Any]]:
    playlists: List[Dict[str, Any]] = []
    for item in sp.paginate("/me/playlists", {"limit": 50}):
        if item.get("uri"):
            playlists.append(item)
            if ctx and len(playlists) % 500 == 0:
                ctx.log(f"Read {len(playlists)} playlists...")
    if ctx:
        ctx.log(f"Found {len(playlists)} playlists.")
    return playlists


def collect_playlist_tracks(sp: SpotifyClient, playlist_id: str, ctx: Optional[JobContext] = None) -> List[str]:
    uris: List[str] = []
    for item in sp.paginate(f"/playlists/{playlist_id}/items", {"limit": 50, "additional_types": "track"}):
        track = item.get("track") or item.get("item") or {}
        if track.get("type") == "track" and track.get("uri"):
            uris.append(track["uri"])
            if ctx and len(uris) % 500 == 0:
                ctx.log(f"Read {len(uris)} playlist tracks...")
    if ctx:
        ctx.log(f"Found {len(uris)} playlist tracks.")
    return unique_preserve_order(uris)


def resolve_artist(sp: SpotifyClient, value: str) -> Dict[str, Any]:
    artist_id = normalize_spotify_id(value, "artist")
    if artist_id:
        return sp.request("GET", f"/artists/{artist_id}")
    data = sp.request("GET", "/search", params={"q": value, "type": "artist", "limit": 10})
    items = ((data.get("artists") or {}).get("items") or []) if isinstance(data, dict) else []
    if not items:
        raise SpotifyError(f"Could not find artist: {value}")
    return items[0]


def job_remove_all_saved_albums(ctx: JobContext, params: Dict[str, Any]) -> Dict[str, Any]:
    require_confirm(params)
    sp = SpotifyClient(ctx)
    total_removed = 0
    pass_number = 1
    while True:
        ctx.check_cancelled()
        albums = collect_saved_albums(sp, ctx)
        uris = [item["album"]["uri"] for item in albums if item.get("album", {}).get("uri")]
        if not uris:
            ctx.log("Saved albums are empty.")
            break
        ctx.log(f"Removal pass {pass_number}: removing {len(uris)} saved albums.")
        total_removed += sp.remove_from_library(uris, ctx, "albums")
        pass_number += 1
    return {"removed_albums": total_removed}


def job_save_albums_from_liked_songs(ctx: JobContext, params: Dict[str, Any]) -> Dict[str, Any]:
    sp = SpotifyClient(ctx)
    liked = collect_liked_tracks(sp, ctx)
    album_uris = []
    for item in liked:
        album = (item.get("track") or {}).get("album") or {}
        if album.get("uri"):
            album_uris.append(album["uri"])
    album_uris = unique_preserve_order(album_uris)
    ctx.log(f"Saving {len(album_uris)} unique albums found in liked songs.")
    saved = sp.save_to_library(album_uris, ctx, "albums")
    return {"unique_albums": len(album_uris), "saved_albums": saved}


def job_save_artist_catalog_albums(ctx: JobContext, params: Dict[str, Any]) -> Dict[str, Any]:
    sp = SpotifyClient(ctx)
    artist_inputs = split_lines(params.get("artists", ""))
    include_groups = (params.get("include_groups") or "album,single").strip()
    if not artist_inputs:
        raise SpotifyError("Enter at least one artist name, URL, URI, or ID.")
    all_album_uris: List[str] = []
    artists_done = []
    for index, artist_input in enumerate(artist_inputs, 1):
        ctx.check_cancelled()
        artist = resolve_artist(sp, artist_input)
        artist_id = artist.get("id")
        name = artist.get("name", artist_input)
        artists_done.append({"input": artist_input, "name": name, "id": artist_id})
        ctx.log(f"[{index}/{len(artist_inputs)}] Reading catalog for {name}...")
        for album in sp.paginate(
            f"/artists/{artist_id}/albums",
            {"limit": 50, "include_groups": include_groups, "market": "from_token"},
        ):
            if album.get("uri"):
                all_album_uris.append(album["uri"])
    all_album_uris = unique_preserve_order(all_album_uris)
    ctx.log(f"Saving {len(all_album_uris)} unique albums/singles from artist catalogs.")
    saved = sp.save_to_library(all_album_uris, ctx, "artist catalog albums/singles")
    return {"artists": artists_done, "unique_album_items": len(all_album_uris), "saved_items": saved}


def job_make_liked_song_playlists(ctx: JobContext, params: Dict[str, Any]) -> Dict[str, Any]:
    sp = SpotifyClient(ctx)
    me = sp.me()
    user_id = me.get("id")
    if not user_id:
        raise SpotifyError("Could not read current Spotify user ID.")
    base_name = (params.get("base_name") or "Saved Songs Backup").strip() or "Saved Songs Backup"
    split_size = int(params.get("split_size") or 2500)
    split_size = max(1, min(split_size, 10000))
    is_public = bool(params.get("public"))

    liked = collect_liked_tracks(sp, ctx)
    track_uris = unique_preserve_order([(item.get("track") or {}).get("uri", "") for item in liked])
    if not track_uris:
        return {"created_playlists": [], "tracks": 0}

    created = []
    parts = list(chunked(track_uris, split_size))
    for part_index, part_uris in enumerate(parts, 1):
        ctx.check_cancelled()
        name = f"{base_name} ({part_index} of {len(parts)})"
        description = f"Backup made by {APP_NAME}. Part {part_index}/{len(parts)}."
        playlist = sp.request(
            "POST",
            f"/users/{user_id}/playlists",
            json_body={"name": name, "public": is_public, "description": description},
            expected=(200, 201),
        )
        playlist_id = playlist.get("id")
        ctx.log(f"Created playlist: {name}")
        sp.add_items_to_playlist(playlist_id, part_uris, ctx)
        created.append(
            {
                "name": name,
                "id": playlist_id,
                "tracks": len(part_uris),
                "url": (playlist.get("external_urls") or {}).get("spotify"),
            }
        )
    return {"created_playlists": created, "tracks": len(track_uris), "split_size": split_size}


def job_remove_all_liked_songs(ctx: JobContext, params: Dict[str, Any]) -> Dict[str, Any]:
    require_confirm(params)
    sp = SpotifyClient(ctx)
    total_removed = 0
    pass_number = 1
    while True:
        ctx.check_cancelled()
        liked = collect_liked_tracks(sp, ctx)
        uris = [(item.get("track") or {}).get("uri", "") for item in liked]
        uris = unique_preserve_order(uris)
        if not uris:
            ctx.log("Liked Songs is empty.")
            break
        ctx.log(f"Removal pass {pass_number}: removing {len(uris)} liked songs.")
        total_removed += sp.remove_from_library(uris, ctx, "liked tracks")
        pass_number += 1
    return {"removed_liked_songs": total_removed}


def job_delete_saved_albums_by_artist(ctx: JobContext, params: Dict[str, Any]) -> Dict[str, Any]:
    require_confirm(params)
    sp = SpotifyClient(ctx)
    artist_input = (params.get("artist") or "").strip()
    if not artist_input:
        raise SpotifyError("Enter an artist name, URL, URI, or ID.")
    artist_id = normalize_spotify_id(artist_input, "artist")
    artist_name = artist_input.lower()
    if artist_id:
        artist = sp.request("GET", f"/artists/{artist_id}")
        artist_name = (artist.get("name") or artist_input).lower()
    albums = collect_saved_albums(sp, ctx)
    to_remove: List[str] = []
    matched_names = []
    for item in albums:
        album = item.get("album") or {}
        names = [a.get("name", "") for a in album.get("artists") or []]
        ids = [a.get("id", "") for a in album.get("artists") or []]
        if (artist_id and artist_id in ids) or any(artist_name in n.lower() for n in names):
            if album.get("uri"):
                to_remove.append(album["uri"])
                matched_names.append(f"{artist_names(album.get('artists') or [])} - {album.get('name', '')}")
    ctx.log(f"Matched {len(to_remove)} saved albums containing artist: {artist_input}")
    removed = sp.remove_from_library(to_remove, ctx, "matched albums") if to_remove else 0
    return {"artist": artist_input, "matched_albums": len(to_remove), "removed_albums": removed, "examples": matched_names[:25]}


def job_save_tracks_from_playlist(ctx: JobContext, params: Dict[str, Any]) -> Dict[str, Any]:
    sp = SpotifyClient(ctx)
    playlist_input = (params.get("playlist") or "").strip()
    playlist_id = normalize_spotify_id(playlist_input, "playlist")
    if not playlist_id:
        raise SpotifyError("Enter a valid playlist URL, URI, or ID.")
    uris = collect_playlist_tracks(sp, playlist_id, ctx)
    ctx.log(f"Saving {len(uris)} playlist tracks to Liked Songs.")
    saved = sp.save_to_library(uris, ctx, "playlist tracks")
    return {"playlist_id": playlist_id, "tracks_found": len(uris), "saved_tracks": saved}


def job_unfollow_playlists_by_phrase(ctx: JobContext, params: Dict[str, Any]) -> Dict[str, Any]:
    require_confirm(params)
    sp = SpotifyClient(ctx)
    phrase = (params.get("phrase") or "Saved Songs").strip()
    if not phrase:
        raise SpotifyError("Enter a phrase to match playlists.")
    playlists = collect_playlists(sp, ctx)
    phrase_lower = phrase.lower()
    matches = [p for p in playlists if phrase_lower in (p.get("name") or "").lower()]
    uris = [p.get("uri", "") for p in matches if p.get("uri")]
    ctx.log(f"Matched {len(uris)} playlists containing phrase: {phrase}")
    removed = sp.remove_from_library(uris, ctx, "playlists") if uris else 0
    return {
        "phrase": phrase,
        "matched_playlists": len(matches),
        "unfollowed_playlists": removed,
        "names": [p.get("name") for p in matches[:50]],
        "note": "Spotify library removal unfollows/removes the playlist from your library. It may not permanently delete a playlist object owned by you.",
    }


def job_export_playlist_share_links(ctx: JobContext, params: Dict[str, Any]) -> Dict[str, Any]:
    sp = SpotifyClient(ctx)
    playlists = collect_playlists(sp, ctx)
    filename = f"playlist_share_links_{time.strftime('%Y%m%d_%H%M%S')}.csv"
    path = EXPORT_DIR / safe_filename(filename)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["name", "owner", "tracks_total", "public", "collaborative", "spotify_url", "playlist_uri", "playlist_id"])
        writer.writeheader()
        for index, playlist in enumerate(playlists, 1):
            if index % 100 == 0:
                ctx.check_cancelled()
            writer.writerow(
                {
                    "name": playlist.get("name", ""),
                    "owner": (playlist.get("owner") or {}).get("display_name", ""),
                    "tracks_total": (playlist.get("tracks") or {}).get("total", ""),
                    "public": playlist.get("public", ""),
                    "collaborative": playlist.get("collaborative", ""),
                    "spotify_url": (playlist.get("external_urls") or {}).get("spotify", ""),
                    "playlist_uri": playlist.get("uri", ""),
                    "playlist_id": playlist.get("id", ""),
                }
            )
    ctx.log(f"Exported playlist share links: {filename}")
    return {"file": filename, "download_url": f"/download/{filename}", "rows": len(playlists)}


def job_export_liked_songs_csv(ctx: JobContext, params: Dict[str, Any]) -> Dict[str, Any]:
    sp = SpotifyClient(ctx)
    liked = collect_liked_tracks(sp, ctx)
    filename = f"liked_songs_{time.strftime('%Y%m%d_%H%M%S')}.csv"
    path = EXPORT_DIR / safe_filename(filename)
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "added_at",
                "track_name",
                "artist_names",
                "album_name",
                "release_date",
                "duration_ms",
                "explicit",
                "spotify_url",
                "track_uri",
                "track_id",
                "album_uri",
                "album_id",
            ],
        )
        writer.writeheader()
        for index, item in enumerate(liked, 1):
            if index % 250 == 0:
                ctx.check_cancelled()
            track = item.get("track") or {}
            album = track.get("album") or {}
            writer.writerow(
                {
                    "added_at": item.get("added_at", ""),
                    "track_name": track.get("name", ""),
                    "artist_names": artist_names(track.get("artists") or []),
                    "album_name": album.get("name", ""),
                    "release_date": album.get("release_date", ""),
                    "duration_ms": track.get("duration_ms", ""),
                    "explicit": track.get("explicit", ""),
                    "spotify_url": (track.get("external_urls") or {}).get("spotify", ""),
                    "track_uri": track.get("uri", ""),
                    "track_id": track.get("id", ""),
                    "album_uri": album.get("uri", ""),
                    "album_id": album.get("id", ""),
                }
            )
    ctx.log(f"Exported liked songs CSV: {filename}")
    return {"file": filename, "download_url": f"/download/{filename}", "rows": len(liked)}


def job_reset_local_app_files(ctx: JobContext, params: Dict[str, Any]) -> Dict[str, Any]:
    require_confirm(params, "RESET")
    deleted = []
    for path in [TOKEN_FILE, JOBS_FILE, CONFIG_FILE]:
        if path.exists():
            path.unlink()
            deleted.append(path.name)
    if EXPORT_DIR.exists():
        shutil.rmtree(EXPORT_DIR)
        deleted.append("exports/")
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    ctx.log("Reset local app files: token, config, job history, and exports. Source code was not deleted.")
    return {"deleted": deleted, "note": "Source code files were intentionally not deleted. Delete the repo folder to uninstall the app."}


JOB_HANDLERS: Dict[str, Tuple[str, Callable[[JobContext, Dict[str, Any]], Dict[str, Any]]]] = {
    "remove_all_saved_albums": ("Unlike/remove ALL saved albums", job_remove_all_saved_albums),
    "save_albums_from_liked_songs": ("Like/save albums from Liked Songs", job_save_albums_from_liked_songs),
    "save_artist_catalog_albums": ("Like/save albums from artists' catalogs", job_save_artist_catalog_albums),
    "make_liked_song_playlists": ("Make split playlists from current Liked Songs", job_make_liked_song_playlists),
    "remove_all_liked_songs": ("Unlike/remove ALL liked songs until empty", job_remove_all_liked_songs),
    "delete_saved_albums_by_artist": ("Delete saved albums containing an artist", job_delete_saved_albums_by_artist),
    "save_tracks_from_playlist": ("Like/save every song from selected playlist", job_save_tracks_from_playlist),
    "unfollow_playlists_by_phrase": ("Delete/unfollow playlists by phrase", job_unfollow_playlists_by_phrase),
    "export_playlist_share_links": ("Make playlist share links chart", job_export_playlist_share_links),
    "export_liked_songs_csv": ("Export liked songs CSV", job_export_liked_songs_csv),
    "reset_local_app_files": ("Reset ALL local app files", job_reset_local_app_files),
}


def run_job(job_id: str, action: str, params: Dict[str, Any]) -> None:
    ctx = JobContext(job_id)
    label, handler = JOB_HANDLERS[action]
    update_job(job_id, status="running", progress=0, done=0, total=0, result=None, error=None)
    ctx.log(f"Started: {label}")
    try:
        result = handler(ctx, params)
        update_job(job_id, status="done", progress=100, result=result, cancel_requested=False)
        ctx.log("Done.")
    except JobCancelled as exc:
        update_job(job_id, status="cancelled", error=None, cancel_requested=False)
        ctx.log(str(exc))
    except Exception as exc:
        update_job(job_id, status="error", error=str(exc), traceback=traceback.format_exc(), cancel_requested=False)
        ctx.log(f"ERROR: {exc}")


@app.route("/")
def index():
    config = get_config()
    logged_in = bool(get_token())
    return render_template(
        "index.html",
        app_name=APP_NAME,
        app_version=APP_VERSION,
        config=config,
        logged_in=logged_in,
        redirect_uri=config.get("redirect_uri", DEFAULT_REDIRECT_URI),
        scopes=DEFAULT_SCOPES,
        rate_modes=RATE_MODES,
    )


@app.route("/save-settings", methods=["POST"])
def save_settings():
    config = get_config()
    client_id = (request.form.get("client_id") or DEFAULT_CLIENT_ID).strip()
    redirect_uri = (request.form.get("redirect_uri") or DEFAULT_REDIRECT_URI).strip()
    rate_mode = request.form.get("rate_mode") or "gentle"
    custom_delay = request.form.get("min_delay_seconds")
    config["client_id"] = client_id
    config["redirect_uri"] = redirect_uri
    config["rate_mode"] = rate_mode
    try:
        config["min_delay_seconds"] = float(custom_delay) if custom_delay else RATE_MODES.get(rate_mode, RATE_MODES["gentle"])
    except ValueError:
        config["min_delay_seconds"] = RATE_MODES.get(rate_mode, RATE_MODES["gentle"])
    save_config(config)
    return redirect(url_for("index"))


@app.route("/login")
def login():
    config = get_config()
    client_id = config.get("client_id") or DEFAULT_CLIENT_ID
    redirect_uri = config.get("redirect_uri", DEFAULT_REDIRECT_URI)
    if not client_id:
        return redirect(url_for("index"))
    verifier = generate_code_verifier()
    challenge = generate_code_challenge(verifier)
    state = secrets.token_urlsafe(24)
    session["code_verifier"] = verifier
    session["oauth_state"] = state
    params = {
        "response_type": "code",
        "client_id": client_id,
        "scope": DEFAULT_SCOPES,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge_method": "S256",
        "code_challenge": challenge,
    }
    return redirect(f"{SPOTIFY_ACCOUNTS}/authorize?{urllib.parse.urlencode(params)}")


@app.route("/callback")
def callback():
    config = get_config()
    error = request.args.get("error")
    if error:
        return f"Spotify login failed: {error}", 400
    state = request.args.get("state")
    if not state or state != session.get("oauth_state"):
        return "OAuth state mismatch. Reset token and try again.", 400
    code = request.args.get("code")
    verifier = session.get("code_verifier")
    if not code or not verifier:
        return "Missing authorization code or verifier.", 400
    response = requests.post(
        f"{SPOTIFY_ACCOUNTS}/api/token",
        data={
            "client_id": config.get("client_id") or DEFAULT_CLIENT_ID,
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": config.get("redirect_uri", DEFAULT_REDIRECT_URI),
            "code_verifier": verifier,
        },
        timeout=30,
    )
    if response.status_code >= 400:
        return f"Token exchange failed: {response.status_code} {response.text}", 400
    token = response.json()
    token["expires_at"] = now_ts() + int(token.get("expires_in", 3600))
    save_token(token)
    session.pop("code_verifier", None)
    session.pop("oauth_state", None)
    return redirect(url_for("index"))


@app.route("/api/me")
def api_me():
    try:
        sp = SpotifyClient()
        profile = sp.me()
        return jsonify({"logged_in": True, "profile": profile})
    except Exception as exc:
        return jsonify({"logged_in": False, "error": str(exc)})


@app.route("/api/run", methods=["POST"])
def api_run():
    payload = request.get_json(force=True, silent=True) or {}
    action = payload.get("action")
    params = payload.get("params") or {}
    if action not in JOB_HANDLERS:
        return jsonify({"error": "Unknown action"}), 400
    job_id = uuid.uuid4().hex[:12]
    label, _ = JOB_HANDLERS[action]
    jobs = get_jobs()
    jobs[job_id] = {
        "id": job_id,
        "action": action,
        "label": label,
        "params": params,
        "status": "queued",
        "progress": 0,
        "done": 0,
        "total": 0,
        "logs": [],
        "created_at": now_ts(),
        "updated_at": now_ts(),
        "result": None,
        "error": None,
        "cancel_requested": False,
    }
    save_jobs(jobs)
    thread = threading.Thread(target=run_job, args=(job_id, action, params), daemon=True)
    job_threads[job_id] = thread
    thread.start()
    return jsonify({"job_id": job_id})


@app.route("/api/jobs")
def api_jobs():
    jobs = list(get_jobs().values())
    jobs.sort(key=lambda j: j.get("created_at", 0), reverse=True)
    return jsonify({"jobs": jobs[:80]})


@app.route("/api/jobs/<job_id>/cancel", methods=["POST"])
def api_cancel_job(job_id: str):
    jobs = get_jobs()
    job = jobs.get(job_id)
    if not job:
        return jsonify({"error": "Job not found"}), 404
    if job.get("status") in {"done", "error", "cancelled"}:
        return jsonify({"ok": True, "status": job.get("status"), "message": "Job already finished."})
    job["cancel_requested"] = True
    job["status"] = "cancelling"
    job["updated_at"] = now_ts()
    save_jobs(jobs)
    append_log(job_id, "Cancel requested. The job will stop after the current Spotify request or batch finishes.")
    return jsonify({"ok": True, "status": "cancelling"})


@app.route("/api/reset-token", methods=["POST"])
def api_reset_token():
    delete_token()
    session.clear()
    return jsonify({"ok": True})


@app.route("/download/<path:filename>")
def download(filename: str):
    path = (EXPORT_DIR / filename).resolve()
    export_root = EXPORT_DIR.resolve()
    if not str(path).startswith(str(export_root)) or not path.exists():
        return "File not found", 404
    return send_file(path, as_attachment=True)


def open_browser_once() -> None:
    if os.environ.get("NO_BROWSER"):
        return
    def _open() -> None:
        time.sleep(1.0)
        try:
            webbrowser.open("http://127.0.0.1:8765")
        except Exception:
            pass
    threading.Thread(target=_open, daemon=True).start()


if __name__ == "__main__":
    print(f"\n{APP_NAME} {APP_VERSION}")
    print("Open: http://127.0.0.1:8765")
    print(f"Spotify Redirect URI to register: {DEFAULT_REDIRECT_URI}")
    print("Press Ctrl+C in this terminal to stop the local app.\n")
    open_browser_once()
    app.run(host="127.0.0.1", port=8765, debug=False, threaded=True)
