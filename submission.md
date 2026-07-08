# Mixtape — Codebase Map

## Overview

Mixtape is a Flask + SQLAlchemy social music app. Friends share songs, build
collaborative playlists, rate songs, and track listening streaks. The app
follows a **routes → services → models** layering: HTTP handlers stay thin,
business logic lives in `services/`, and persistence/schema lives in
`models.py`.

## Main Files and Their Roles

### App setup

- **`app.py`** — Application factory (`create_app`). Configures the
  SQLAlchemy database URI (`DATABASE_URL` env var, defaults to
  `sqlite:///mixtape.db`), initializes the `db` extension, registers the four
  route blueprints (`songs`, `playlists`, `users`, `feed`) under their URL
  prefixes, and calls `db.create_all()` at startup. Also the entry point
  (`flask run` / `python app.py`).
- **`models.py`** — All SQLAlchemy models and association tables:
  - `User` — profile, `listening_streak`, `last_listened_at`, and
    relationships to songs, ratings, listening events, notifications,
    playlists, and a symmetric many-to-many `friends` relationship (via the
    `friendships` table).
  - `Song` — title/artist/album/genre, who shared it (`shared_by`), and a
    many-to-many `tags` relationship (via `song_tags`).
  - `Tag` — simple name lookup for song tags.
  - `ListeningEvent` — a timestamped record of a user listening to a song
    (drives streaks and feeds).
  - `Rating` — a user's 1–5 score for a song, unique per `(user_id, song_id)`.
  - `Playlist` — name, creator, collaborative flag, and a many-to-many
    `songs` relationship via `playlist_entries` (which also stores
    `position`, `added_by`, and `added_at`).
  - `Notification` — a message to a user about friend activity
    (`notification_type`, `body`, `read`).
  - Every model has a `to_dict()` used to serialize it for JSON responses.
- **`seed_data.py`** — Populates the DB with 5 users/friendships, 25 songs
  (with 0/1/3+ tags), listening events (recent + historical, to exercise the
  "listening now" feed), 3 playlists, and sample notifications. Run via
  `python seed_data.py`.

### Routes (`routes/`) — thin HTTP layer

Each blueprint parses the request, calls into a service function, and maps
the result (or a raised `ValueError`) to a JSON response.

- **`routes/songs.py`** (`/songs` prefix) — `GET /search`, `GET /<id>`,
  `POST /<id>/rate`, `POST /<id>/listen`. Delegates to `search_service`,
  `notification_service.rate_song`, and `streak_service.record_listening_event`.
- **`routes/playlists.py`** (`/playlists` prefix) — `POST /` (create),
  `GET /<id>` (metadata), `GET /<id>/songs` (ordered song list),
  `POST /<id>/songs` (add a song). Delegates to `playlist_service` and
  `notification_service.add_to_playlist`.
- **`routes/users.py`** (`/users` prefix) — `GET /<id>` (profile, queries
  `User` directly), `GET /<id>/streak`, `GET /<id>/notifications`,
  `POST /notifications/<id>/read`. Delegates to `streak_service.get_streak`
  and `notification_service`.
- **`routes/feed.py`** (`/feed` prefix) — `GET /<user_id>/listening-now`,
  `GET /<user_id>/activity`. Delegates to `feed_service`.

### Services (`services/`) — business logic

- **`streak_service.py`** — `record_listening_event()` creates a
  `ListeningEvent` and calls `update_listening_streak()`, which increments
  the streak on consecutive calendar days, resets it after a gap, and leaves
  it unchanged for repeat listens on the same day. `get_streak()` reads the
  current value.
- **`feed_service.py`** — `get_friends_listening_now()` returns each friend's
  most recent song listened to within the last 24 hours (deduplicated per
  friend). `get_activity_feed()` returns the most recent N listening events
  from friends regardless of recency.
- **`search_service.py`** — `search_songs()` case-insensitively matches
  title/artist and returns song dicts (with tags). `get_song()` fetches one
  song by id.
- **`notification_service.py`** — `create_notification()` is the shared
  helper that persists a `Notification`. `add_to_playlist()` adds a song to a
  playlist's song list and notifies the original sharer. `rate_song()`
  creates/updates a `Rating` (upsert on `(user_id, song_id)`).
  `get_notifications()` / `mark_as_read()` support the notification inbox.
- **`playlist_service.py`** — `create_playlist()`, `get_playlist()`
  (metadata only), `get_playlist_songs()` (songs ordered by their
  `playlist_entries.position`), `get_user_playlists()`.

### Tests (`tests/`)

`test_streaks.py`, `test_search.py`, `test_playlists.py` — one file per
service under test, exercising the "Five Open Issues" documented in
`README.md`.

## Data Flow Example: Adding a Song to a Playlist

This traces `POST /playlists/<playlist_id>/songs`, the flow called out in
the README, end to end:

1. **Client** sends `POST /playlists/<playlist_id>/songs` with JSON body
   `{"song_id": ..., "added_by": ...}`.
2. **`routes/playlists.py:add_song`** parses `song_id` and `added_by` from
   the request body, validates both are present (400 if not), and calls
   `notification_service.add_to_playlist(playlist_id, song_id, added_by)`.
3. **`services/notification_service.py:add_to_playlist`**:
   - Loads the `Song`, the adding `User`, and the `Playlist` via
     `db.session.get`, raising `ValueError` (→ 400 in the route) if any is
     missing.
   - If the song isn't already in `playlist.songs`, appends it — SQLAlchemy
     inserts a row into the `playlist_entries` association table (this is
     where `Playlist.songs`, defined in **`models.py`**, is mutated) and
     commits.
   - If the adder isn't the song's original sharer (`song.shared_by`), it
     calls `create_notification()` to persist a new `Notification` row for
     `song.shared_by`, with `notification_type="song_added_to_playlist"` and
     a human-readable `body`.
4. **Route** returns `{"message": "Song added to playlist"}` with status 201
   on success, or `{"error": ...}` with 400 if a `ValueError` was raised.
5. **Downstream read paths** pick up the effects of this write:
   - `GET /playlists/<id>/songs` → `playlist_service.get_playlist_songs()`
     queries `playlist_entries` ordered by `position` to return the updated
     song list.
   - `GET /users/<id>/notifications` → `notification_service.get_notifications()`
     returns the new notification (visible to the song's original sharer)
     ordered most-recent-first.

This flow spans all three layers: **route** (`routes/playlists.py`) for
request parsing/validation, **service** (`services/notification_service.py`,
with a supporting read from `services/playlist_service.py`) for business
logic, and **models** (`models.py`'s `Playlist`, `Song`, `User`,
`playlist_entries`, `Notification`) for persistence.
