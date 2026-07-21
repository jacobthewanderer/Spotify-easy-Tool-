# Spotify Album Playlist Builder

GitHub Pages app that reads every saved Spotify album and creates private playlists named `Album Playlist 1`, `Album Playlist 2`, etc., with up to 2,500 songs each. Albums stay together whenever possible.

## Deploy
1. Create a GitHub repository.
2. Upload all files to the repository root.
3. Settings → Pages → Deploy from branch → main → root.
4. Open the Pages URL.
5. Copy the Redirect URI shown by the app into your Spotify app settings.
6. Paste your Client ID, log in, and press Start / Resume.

## Scopes
`user-library-read playlist-modify-private playlist-read-private`

## Endpoints
`GET /me/albums`
`GET /albums/{id}/tracks`
`POST /me/playlists`
`POST /playlists/{id}/items`

Uses Authorization Code with PKCE. Never put a Client Secret into GitHub Pages. Progress is stored in browser localStorage.
