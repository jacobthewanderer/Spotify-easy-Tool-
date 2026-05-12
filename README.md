# Spotify Library Manager

A local Flask web app for managing Spotify library cleanup and backup jobs with a nicer interface, low rate-limit modes, destructive-action grouping, and a cancellable job manager.

> **Included Client ID:** `b8db26e7cfff4475804ce186878db270`

## What it can do

### Library Builders

- Like/save albums from current Liked Songs
- Like/save albums from artists' catalogs
- Make playlists from current Liked Songs, splitting into Part 1, Part 2, etc. when a playlist reaches the chosen size, default `2500`
- Like/save every song from a selected playlist

### Destructive Tools

These are grouped together in the UI and require typed confirmation.

- Unlike/remove **ALL** saved albums
- Unlike/remove **ALL** liked songs until empty
- Delete/remove saved albums containing a selected artist
- Delete/unfollow all playlists containing a phrase, default `Saved Songs`
- Reset local login token
- Reset local app files: token, settings, job history, and CSV exports

### Exports

- Make a playlist share links chart as CSV
- Export liked songs as CSV

### Job Manager

- Runs long operations as local jobs
- Shows progress and logs
- Supports cancelling jobs
- Respects Spotify `429 Retry-After` responses
- Has rate-limit modes: Fast-ish, Gentle, Slow, Ultra-safe

## Important Spotify limitations

This app uses the Spotify Web API. Some destructive wording is user-facing shorthand:

- Spotify does not always provide a true "delete playlist forever" behavior through the public Web API. Removing a playlist from your library generally means unfollowing/removing it from your saved playlists.
- The app uses Spotify's newer `/me/library` save/remove endpoint for saves/removals where possible.
- If your Spotify app is still in **Development Mode**, only allowlisted users can use the app successfully. Spotify says development mode apps are limited to up to 5 authenticated users and non-allowlisted users may receive `403` API errors. To allow unlimited users, the app needs Extended Quota Mode.

## Setup

### 1. Create or open your Spotify Developer app

Open the Spotify Developer Dashboard, then create/open an app.

### 2. Add this redirect URI exactly

```text
http://127.0.0.1:8765/callback
```

Do not use `localhost` unless you also change the app setting and this script to match exactly.

### 3. Run the app

#### Windows

1. Extract the ZIP.
2. Double-click `START_WINDOWS.bat`.
3. The browser should open to `http://127.0.0.1:8765`.

#### Mac/Linux

```bash
chmod +x START_MAC_LINUX.sh
./START_MAC_LINUX.sh
```

### 4. Log in

Click **Log in with Spotify** and grant the requested permissions.

## GitHub publishing

To publish this as a GitHub repo:

```bash
git init
git add .
git commit -m "Initial Spotify Library Manager"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME/spotify-library-manager.git
git push -u origin main
```

The `.gitignore` prevents local tokens, config, job history, and exports from being committed.

## Security notes

- This app is meant to run locally on `127.0.0.1`.
- Do not commit `data/token.json`.
- Do not share your local token files.
- This app intentionally uses Authorization Code with PKCE so it does not need a Client Secret in the repo.

## Environment overrides

The app defaults to the included Client ID, but you can override values with environment variables:

```bash
SPOTIFY_CLIENT_ID=your-client-id
SPOTIFY_REDIRECT_URI=http://127.0.0.1:8765/callback
SPOTIFY_MANAGER_SECRET=any-random-string
NO_BROWSER=1
```

## Troubleshooting

### Clicking `app.py` does nothing

Use the start scripts instead:

- Windows: `START_WINDOWS.bat`
- Mac/Linux: `START_MAC_LINUX.sh`

### Spotify says redirect URI mismatch

Make sure the Spotify Developer Dashboard has this exact value:

```text
http://127.0.0.1:8765/callback
```

Then restart the local app and log in again.

### API calls fail with 403

Your Spotify app is probably in Development Mode and the logging-in user is not allowlisted in the app's Users Management tab.

### Job cancellation did not undo changes

Cancel stops the job from continuing after the current Spotify request or batch. It does **not** undo changes that were already sent to Spotify.
