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

---

# Bug Fixes

## Issue #1: My listening streak keeps resetting

### How I reproduced it

Before touching any code, I looked at `tests/test_streaks.py`, which already
had a test encoding the expected behavior: `test_streak_increments_on_sunday`.
It listens on Saturday, June 15 2024 (`weekday() == 5`), then on Sunday,
June 16 2024 (`weekday() == 6`), and asserts the streak goes from 1 to 2
(consecutive days should always increment, regardless of which day of the
week it is).

I ran the streak suite to confirm this failed *before* making any changes:

```
.venv/bin/python -m pytest tests/test_streaks.py -v
```

Result: 4 tests passed, but `test_streak_increments_on_sunday` failed with
`assert 1 == 2` — the streak was reset to 1 on Sunday instead of
incrementing to 2. This confirmed the bug exists independent of any of my
assumptions, using inputs that isolate the day-of-week as the only variable
(same one-day gap as the passing `test_streak_increments_on_consecutive_day`
case, just shifted to a Sat→Sun boundary).

### How I found the root cause

I opened `services/streak_service.py` (the file the README's issue table
points to for this bug) and read `update_listening_streak()`, the only
function that mutates `listening_streak`. Its docstring states the rule
plainly: increment on a one-day gap, reset on more than a one-day gap, no
day-of-week exception is mentioned anywhere in the spec.

Reading the implementation line by line, the branch that decides between
"increment" and "reset" was:

```python
elif days_since_last == 1 and today.weekday() != 6:
    user.listening_streak += 1
else:
    user.listening_streak = 1
```

The `and today.weekday() != 6` clause was the moment of confidence — it's an
extra condition with no basis in the docstring, no basis in the other tests,
and no comment explaining it. `datetime.weekday()` returns `6` for Sunday, so
this clause silently forces any listen that happens to fall on a Sunday into
the `else` branch — even when `days_since_last == 1`, which is exactly the
"consecutive day" case that should increment. That pinpointed the exact
faulty comparison, not just the general area.

### The root cause

`update_listening_streak()` had an extra, undocumented condition tacked onto
the "consecutive day" check: it only incremented the streak on a one-day gap
if today was *not* Sunday (`today.weekday() != 6`). Since Python's
`date.weekday()` returns `6` for Sunday, any time a user's streak-continuing
listen happened to land on a Sunday, the function skipped the increment
branch entirely and fell through to the `else`, which resets the streak to
1 — identical to the "you skipped a day" case. The bug wasn't in how the day
gap (`days_since_last`) was computed (that arithmetic was correct); it was an
unrelated, incorrect extra clause gating the increment on which weekday it
happened to be.

### My fix and side-effect check

I removed the erroneous weekday clause so the condition matches the
documented rule — a one-day gap always increments, regardless of weekday:

```python
elif days_since_last == 1:
    user.listening_streak += 1
```

This fixes the root cause directly: the increment branch no longer depends
on `today.weekday()` at all, so Saturday→Sunday, Sunday→Monday, and every
other consecutive-day pair now behave identically to any other weekday pair.

To check for side effects, I ran the full `tests/test_streaks.py` suite
(not just the one failing test) plus the full project test suite:

```
.venv/bin/python -m pytest tests/ -v
```

All 5 streak tests now pass, including:
- New-user streak starts at 1
- Same-day repeat listens don't double-count
- A skipped day still resets to 1
- Consecutive weekdays still increment
- Saturday → Sunday now increments (previously failing)

The other two failures in the full suite (`tests/test_playlists.py`) are
pre-existing and unrelated to this change — they correspond to Issue #5
(the last song in a playlist not showing up), which lives in
`playlist_service.py` and is untouched by this fix.

## Issue #2: Friends Listening Now shows people from yesterday

### How I reproduced it

Unlike Issue #1, there was no pre-existing test for the feed service, so I
first read the reported symptom carefully: a friend whose last listen was
"yesterday evening" still shows up under "Friends Listening Now" the next
morning.

I wrote a small reproduction test (`tests/test_feed.py`) mirroring the
fixture conventions already used in `test_streaks.py`/`test_playlists.py`: a
user with one friend, and a `ListeningEvent` for that friend timestamped
`now - timedelta(hours=12)` (simulating "listened yesterday evening, checked
this morning"). I asserted `get_friends_listening_now()` should return `[]`
for that friend, then ran it *before* changing any service code:

```
.venv/bin/python -m pytest tests/test_feed.py -v
```

Result: `test_listening_now_excludes_listen_from_yesterday_evening` failed —
the stale friend was present in the returned feed — confirming the bug with
a concrete, minimal input (a single friend, a single 12-hour-old event)
rather than relying on the full seed dataset.

### How I found the root cause

The README's issue table points at `services/feed_service.py` for this bug,
so I read `get_friends_listening_now()` top to bottom. The function's own
docstring says it should return friends who listened "recently," and the
query filters events with `ListeningEvent.listened_at >= cutoff`, where
`cutoff = datetime.now(timezone.utc) - RECENT_THRESHOLD`. That's the correct
shape for a recency filter — the arithmetic and the comparison operator
(`>=`) are both right, so the bug had to be in what "recent" was actually
defined as.

`RECENT_THRESHOLD` is declared once, at module level: `timedelta(hours=24)`.
That was the moment of confidence — a 24-hour window means any event from
"yesterday evening" checked "this morning" (a gap of well under 24 hours)
passes the filter and is treated as happening "now." I cross-checked this
against `seed_data.py`'s comments, which explicitly describe the intended
behavior: events "within the past 30 minutes" should appear in listening
now, while everything else (including the "1-14 days ago" bucket, which
actually starts at just 2 hours old) should not. A 24-hour cutoff directly
contradicts that 30-minute intent and explains exactly why multi-hour-old
events were leaking through.

### The root cause

`get_friends_listening_now()` filtered listening events using
`RECENT_THRESHOLD = timedelta(hours=24)` as the definition of "right now."
A feature named "listening now" implies real-time presence, but the
implementation was actually answering a different question — "did this
friend listen at all in the last day?" Any event up to just under 24 hours
old passed the `listened_at >= cutoff` check, so a friend who listened at
9pm the previous evening would still show up as "listening now" at 9am the
next morning (a 12-hour gap, comfortably inside the 24-hour window). The
comparison logic itself was correct; the constant it was compared against
was simply set to the wrong order of magnitude for what the feature is
supposed to mean.

### My fix and side-effect check

I narrowed `RECENT_THRESHOLD` from 24 hours down to 30 minutes, matching the
"listening now" semantics documented in `seed_data.py`'s comments:

```python
RECENT_THRESHOLD = timedelta(minutes=30)
```

This fixes the root cause directly: the cutoff now actually represents "a
few minutes ago," so events from hours or days earlier no longer pass the
`listened_at >= cutoff` filter, while genuinely-recent events still do.

To check for side effects, I first reran the two feed tests I wrote — the
previously-failing 12-hours-ago case now returns `[]`, and a second test
with a listen 10 minutes ago still correctly returns that friend — then ran
the full project test suite:

```
.venv/bin/python -m pytest tests/ -v
```

All streak, search, and (new) feed tests pass (13 total). The two remaining
failures in `tests/test_playlists.py` are the pre-existing, unrelated
Issue #5. I also re-read `get_activity_feed()` in the same file to confirm
it wasn't affected — its docstring explicitly states it is "not filtered by
recency," and its query never references `RECENT_THRESHOLD` at all, so
narrowing that constant has no effect on the general activity feed.

## Issue #3: The same song keeps showing up twice (or three times) in search

### How I reproduced it

The report was specific: searching "Anthem" returned "Crown Heights Anthem"
by Borough Kings three times, with no visible difference between the
copies, while other songs appeared once. Per `seed_data.py`, "Crown Heights
Anthem" is exactly the song seeded with **three** tags (`rap`, `hip-hop`,
`boom bap`) — the number of duplicates matching the number of tags was the
first clue.

There was already a pre-written test for this,
`tests/test_search.py::test_search_no_duplicates_multi_tag_song`, whose
comment literally reads "Should be 1, bug causes it to be 3." I ran the
search suite first:

```
.venv/bin/python -m pytest tests/test_search.py -v
```

Surprisingly, all 5 tests passed on this machine's installed SQLAlchemy
version (2.0.51) — the automated test alone did not reproduce the bug. Since
that contradicted your direct report, I didn't take the green test suite at
face value; I dropped down a level and inspected the actual SQL being
executed rather than the ORM's post-processed result. I compiled the
query's raw statement and ran it directly (bypassing the ORM's entity
mapping) against a song with 3 tags:

```
raw row count: 3   (from db.session.execute(q.statement).all())
orm entity count: 1 (from q.all())
```

That confirmed the underlying SQL genuinely returns 3 duplicate rows — the
ORM's legacy `Query.all()` was silently collapsing them back down to 1
distinct entity via its automatic identity-map "uniquing" for full-entity
queries, an implicit, version-dependent behavior that isn't guaranteed
across SQLAlchemy versions/APIs (2.0-style `select()`/`session.execute()`
queries do *not* get this automatic collapsing and would raise an error
demanding an explicit `.unique()` call for the same query shape). That's
consistent with you seeing real duplicates in your run while my first test
pass looked clean — the bug is real in the SQL layer; whether it's visible
depends on incidental ORM/version behavior, not on anything the code
actually guarantees.

### How I found the root cause

The README points at `services/search_service.py` for this issue, and it's
a short file. `search_songs()`'s query does two things: an
`.outerjoin(song_tags, Song.id == song_tags.c.song_id)`, then a `.filter()`
that only checks `Song.title` and `Song.artist`. The moment of confidence
was noticing that the join column (`song_tags`) is never referenced
anywhere in the `.filter()` or anywhere else in the query — it's not used to
filter, sort, or select anything. `song_tags` is a many-to-many association
table (one row per song/tag pair), so outer-joining a song to it multiplies
that song's result rows by however many tags it has: 1 row for a 0-tag song,
1 for a 1-tag song, but 3 rows for a 3-tag song — exactly matching "Crown
Heights Anthem" (3 tags → 3 identical copies) versus every other song in
your report showing up once.

### The root cause

`search_songs()` joined `Song` to the `song_tags` association table even
though the search filter never uses tags at all — title/artist matching is
the only condition. Since `song_tags` has one row per `(song, tag)` pair,
outer-joining to it turns each matching song into as many result rows as it
has tags (a Cartesian product), so a song with 3 tags comes back 3 times
with completely identical data (there's no difference between the copies
because every column being displayed comes from `Song`, not from the tag
join — the "extra" rows purely reflect the different tag join it happened
to match). The tags shown in each result are already fetched separately via
`Song.tags` (a `lazy="subquery"` relationship used inside `to_dict()`), so
the join in the search query wasn't accomplishing anything except
multiplying rows.

### My fix and side-effect check

I removed the unnecessary join, along with the now-unused `Tag`/`song_tags`
imports, since the query never needed to touch the tags table at all:

```python
results = (
    db.session.query(Song)
    .filter(
        db.or_(
            Song.title.ilike(f"%{query}%"),
            Song.artist.ilike(f"%{query}%"),
        )
    )
    .all()
)
```

This fixes the root cause at the SQL level, not just by relying on the
ORM's incidental deduplication: I re-verified by compiling and executing the
raw statement directly, and the "Anthem" search against the 3-tag song now
returns exactly 1 raw row (previously 3), before the ORM even gets a chance
to collapse anything.

To check for side effects, I ran the full search suite plus the whole
project test suite:

```
.venv/bin/python -m pytest tests/ -v
```

All streak, search (5/5, including the multi-tag case), and feed tests pass
(13 total); the two remaining `test_playlists.py` failures are the same
pre-existing, unrelated Issue #5. I also re-checked `get_song()` in the same
file (single-song lookup by ID) — it never touched `song_tags` in the first
place, so it's unaffected — and confirmed `to_dict()`'s tags list (via the
untouched `Song.tags` relationship) still returns the correct tags for
"Crown Heights Anthem" (`rap`, `hip-hop`, `boom bap`) with the join removed.



Issues solved:
Issue #1 - My listening streak keeps resetting


Issue #2 — Friends Listening Now shows people from yesterday


Issue #3 — The same song keeps showing up twice in search