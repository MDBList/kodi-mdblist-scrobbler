import datetime

from resources.lib import library_snapshot, mdblist_api, sync_payload, sync_state
from resources.lib.utils import jsonrpc_request, local_time_to_utc_iso, utc_iso_to_local_time

CATEGORY = "watched"


def _now_iso():
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def _to_api_datetime(value):
    # Kodi's lastplayed is naive local time; MDBList expects UTC.
    return local_time_to_utc_iso(value)


def _remote_ts_normalized(value):
    if not value:
        return None
    return value.replace(" ", "T")[:19]


# --- push: Kodi -> MDBList -----------------------------------------------

def _movie_item(movie):
    return {"type": "movie", "ids": movie["ids"], "watched_at": _to_api_datetime(movie["lastplayed"])}


def _episode_item(episode):
    return {
        "type": "episode", "show_ids": episode["show_ids"],
        "season": episode["season"], "episode": episode["episode"],
        "watched_at": _to_api_datetime(episode["lastplayed"]),
    }


def _canonical_key(record):
    if record["dbtype"] == "movie":
        return library_snapshot.canonical_movie_key(record["ids"])
    return library_snapshot.canonical_episode_key(record["show_ids"], record["season"], record["episode"])


def _current_watched_items(snapshot):
    items = {}
    for movie in library_snapshot.iter_movies(snapshot):
        if movie["playcount"] > 0:
            key = library_snapshot.canonical_movie_key(movie["ids"])
            if key:
                items[key] = _movie_item(movie)
    for episode in library_snapshot.iter_episodes(snapshot):
        if episode["playcount"] > 0:
            key = library_snapshot.canonical_episode_key(episode["show_ids"], episode["season"], episode["episode"])
            if key:
                items[key] = _episode_item(episode)
    return items


def _push_add(items):
    sync_payload.push_items("/sync/watched", "watched_at", items)


def _push_remove(items):
    sync_payload.push_items_remove("/sync/watched/remove", items)


def push(snapshot):
    """Backfill/membership diff only -- a rewatch that updates lastplayed
    without changing membership is already pushed live via the /scrobble/stop
    event, so this doesn't need ratings_sync's extra "value changed" check."""
    current = _current_watched_items(snapshot)
    return sync_payload.diff_and_reconcile(CATEGORY, current, _push_add, _push_remove)


def push_single(record):
    """Immediate push for one item, triggered by a live VideoLibrary.OnUpdate
    notification (Kodi's native "mark as watched"/"mark as unwatched", not
    just our own scrobble flow). Patches sync_state in place instead of
    replacing it, since this only ever examines one item, not the full
    library -- see sync_state.update_known_item.

    Returns False only when the item genuinely couldn't be pushed (no id this
    addon can map to a provider). Returns {} for "nothing to do, already in
    sync" and a populated dict when something was actually pushed -- see
    ratings_sync.push_single, same contract, fixed for the same reason."""
    key = _canonical_key(record)
    if not key:
        return False

    known_item = sync_state.get_known_items(CATEGORY).get(key)
    is_watched = record["playcount"] > 0

    if is_watched:
        item = _movie_item(record) if record["dbtype"] == "movie" else _episode_item(record)
        if known_item and known_item.get("watched_at") == item.get("watched_at"):
            return {}
        _push_add([item])
        sync_state.update_known_item(CATEGORY, key, item)
        return {"pushed_add": 1}

    if not known_item:
        return {}
    _push_remove([known_item])
    sync_state.update_known_item(CATEGORY, key, None)
    return {"pushed_remove": 1}


# --- pull: MDBList -> Kodi -------------------------------------------------

def _set_watched(record, playcount, lastplayed=None):
    # Only send lastplayed when we have a real value -- e.g. on removal this
    # leaves it untouched, matching Kodi's own "mark unwatched" behavior
    # rather than forcing an empty/invalid date onto the library row.
    params = {"playcount": playcount}
    if lastplayed:
        params["lastplayed"] = lastplayed
    if record["dbtype"] == "movie":
        jsonrpc_request("VideoLibrary.SetMovieDetails", dict(params, movieid=record["dbid"]))
    else:
        jsonrpc_request("VideoLibrary.SetEpisodeDetails", dict(params, episodeid=record["dbid"]))


def _apply_watched(record, status, remote_at):
    """Last-write-wins using Kodi's lastplayed vs the remote timestamp -- the
    one sync category where Kodi actually tracks a comparable local
    timestamp, so real conflict resolution (not just remote-wins) applies.
    Both sides must be normalized to UTC before comparing -- Kodi's
    lastplayed is naive local time, MDBList's timestamps are UTC; comparing
    the raw strings is wrong by the device's UTC offset (confirmed bug: on a
    UTC+3 system a local watch could look "newer" than a later UTC removal,
    silently blocking the removal from ever applying).

    An exact tie (same second on both sides) is resolved the same way in
    both branches below -- remote wins -- rather than local winning ties on
    removal but losing them on activation, which was a real inconsistency
    (both used to claim "last-write-wins" but disagreed on what a tie meant)."""
    local_ts = local_time_to_utc_iso(record.get("lastplayed"))
    remote_ts = _remote_ts_normalized(remote_at)

    if status == "removed":
        if record["playcount"] <= 0:
            return False
        if local_ts and remote_ts and local_ts > remote_ts:
            return False
        _set_watched(record, playcount=0)
        return True

    if record["playcount"] > 0 and local_ts and remote_ts and local_ts > remote_ts:
        return False

    new_lastplayed = utc_iso_to_local_time(remote_at) or record.get("lastplayed")
    _set_watched(record, playcount=max(record["playcount"], 1), lastplayed=new_lastplayed)
    return True


def _apply_movie_entry(snapshot, ids, status, action_at):
    """Returns (applied, canonical_key) -- the key (None if no local match)
    lets _pull_full track which locally-watched items the remote list
    actually mentioned, to reconcile removals for the rest."""
    match = library_snapshot.find_movie_match(snapshot, ids)
    if not match:
        return False, None
    return _apply_watched(match, status, action_at), library_snapshot.canonical_movie_key(match["ids"])


def _apply_episode_entry(snapshot, show_ids, season, episode, status, action_at):
    match = library_snapshot.find_episode_match(snapshot, show_ids, season, episode)
    if not match:
        return False, None
    key = library_snapshot.canonical_episode_key(match["show_ids"], match["season"], match["episode"])
    return _apply_watched(match, status, action_at), key


def _pull_full(snapshot):
    data = mdblist_api.fetch_sync_items("/sync/watched", extended="ids_only")
    applied = 0
    matched_keys = set()

    for entry in data.get("movies", []):
        if not entry.get("tmdb"):
            continue
        applied_ok, key = _apply_movie_entry(snapshot, {"tmdb": entry["tmdb"]}, "active", entry.get("last_watched_at"))
        if key:
            matched_keys.add(key)
        if applied_ok:
            applied += 1

    for entry in data.get("episodes", []):
        if not entry.get("show"):
            continue
        applied_ok, key = _apply_episode_entry(
            snapshot, {"tmdb": entry["show"]}, entry.get("season"), entry.get("episode"),
            "active", entry.get("last_watched_at"),
        )
        if key:
            matched_keys.add(key)
        if applied_ok:
            applied += 1

    # The full list above is authoritative: anything locally watched but not
    # in it was unwatched remotely. Without this, a removal outside the
    # journal's 30-day retention window -- which is what triggers this
    # full-pull fallback in the first place -- could never reach Kodi, and
    # the divergence would never self-correct since push()'s own diff sees
    # the item as unchanged on both sides (confirmed bug).
    for movie in library_snapshot.iter_movies(snapshot):
        if movie["playcount"] > 0:
            key = library_snapshot.canonical_movie_key(movie["ids"])
            if key and key not in matched_keys and _apply_watched(movie, "removed", _now_iso()):
                applied += 1

    for episode in library_snapshot.iter_episodes(snapshot):
        if episode["playcount"] > 0:
            key = library_snapshot.canonical_episode_key(episode["show_ids"], episode["season"], episode["episode"])
            if key and key not in matched_keys and _apply_watched(episode, "removed", _now_iso()):
                applied += 1

    sync_state.set_synced_at(CATEGORY, _now_iso())
    return {"pulled_applied": applied, "mode": "full"}


def _pull_incremental(snapshot, entries):
    applied = 0
    for entry in entries:
        if entry.get("category") != "watched":
            continue

        ids = entry.get("ids") or {}
        status = entry.get("status")
        action_at = entry.get("action_at")

        if entry.get("item_type") == "movie":
            applied_ok, _key = _apply_movie_entry(snapshot, ids, status, action_at)
            if applied_ok:
                applied += 1
        elif entry.get("item_type") == "episode":
            applied_ok, _key = _apply_episode_entry(snapshot, ids, entry.get("season"), entry.get("episode"), status, action_at)
            if applied_ok:
                applied += 1
        # show/season-level rows have no directly writable Kodi field; skipped

    sync_state.set_synced_at(CATEGORY, _now_iso())
    return {"pulled_applied": applied, "mode": "incremental"}


def pull(snapshot):
    since = sync_state.get_synced_at(CATEGORY)
    if not since:
        return _pull_full(snapshot)

    journal = mdblist_api.fetch_journal(since=since)
    if journal.get("requires_full_sync"):
        return _pull_full(snapshot)

    return _pull_incremental(snapshot, journal.get("entries", []))
