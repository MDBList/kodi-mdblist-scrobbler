from collections import OrderedDict

from resources.lib import mdblist_api, sync_state

BATCH_SIZE = 100


def build_shows_payload(episode_entries):
    """Group flat (show_ids, season, episode, extra_fields) tuples into the
    nested {"ids", "seasons": [{"number", "episodes": [{"number", **extra}]}]}
    shape every /sync/* endpoint (watched, ratings, collection) expects for
    episodes -- confirmed against api.mdblist's resolve_show_payload, which
    every one of those POST handlers routes through."""
    shows_by_key = OrderedDict()

    for show_ids, season, episode, extra in episode_entries:
        key = tuple(sorted(show_ids.items()))
        show = shows_by_key.setdefault(key, {"ids": show_ids, "seasons": OrderedDict()})
        entries = show["seasons"].setdefault(season, [])
        entry = {"number": episode}
        entry.update(extra)
        entries.append(entry)

    return [
        {
            "ids": show["ids"],
            "seasons": [
                {"number": season_number, "episodes": episodes}
                for season_number, episodes in show["seasons"].items()
            ],
        }
        for show in shows_by_key.values()
    ]


def chunked(items, size=100):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def push_items(endpoint, field_name, items):
    """Shared push body for watched_sync/ratings_sync/collection_sync's
    _push_add: split by type, set `field_name` (watched_at/rating/
    collected_at) on each, chunk, POST to `endpoint`. The three call sites
    were previously identical modulo that field name and endpoint string."""
    movies_payload = [{"ids": item["ids"], field_name: item[field_name]} for item in items if item["type"] == "movie"]
    episode_entries = [
        (item["show_ids"], item["season"], item["episode"], {field_name: item[field_name]})
        for item in items if item["type"] == "episode"
    ]

    for batch in chunked(movies_payload, BATCH_SIZE):
        mdblist_api.push_sync_items(endpoint, {"movies": batch})
    for batch in chunked(episode_entries, BATCH_SIZE):
        mdblist_api.push_sync_items(endpoint, {"shows": build_shows_payload(batch)})


def push_items_remove(endpoint, items):
    """Shared push body for the three modules' _push_remove -- identity only,
    no value field."""
    movies_payload = [{"ids": item["ids"]} for item in items if item["type"] == "movie"]
    episode_entries = [
        (item["show_ids"], item["season"], item["episode"], {})
        for item in items if item["type"] == "episode"
    ]

    for batch in chunked(movies_payload, BATCH_SIZE):
        mdblist_api.push_sync_items(endpoint, {"movies": batch})
    for batch in chunked(episode_entries, BATCH_SIZE):
        mdblist_api.push_sync_items(endpoint, {"shows": build_shows_payload(batch)})


def diff_and_reconcile(category, current_items, push_add, push_remove, value_changed=None):
    """Shared push+reconcile skeleton used by watched_sync/ratings_sync/
    collection_sync's push(): diff `current_items` (key -> item) against
    sync_state's known_items for `category`, call push_add(items)/
    push_remove(items) for the deltas, persist the new known_items, and
    return a summary dict.

    `value_changed(known_item, item)` -- optional -- adds an item to to_add
    even when its key is already known, if the value differs. Only
    ratings_sync needs this: a rating can change without membership changing,
    unlike watched/collection, which are membership-only (a rewatch/re-add
    with the same value doesn't need a fresh push)."""
    known = sync_state.get_known_items(category)

    if value_changed:
        to_add = [item for key, item in current_items.items() if key not in known or value_changed(known[key], item)]
    else:
        to_add = [item for key, item in current_items.items() if key not in known]
    to_remove = [item for key, item in known.items() if key not in current_items]

    if to_add:
        push_add(to_add)
    if to_remove:
        push_remove(to_remove)

    sync_state.set_known_items(category, current_items)
    return {"pushed_add": len(to_add), "pushed_remove": len(to_remove)}
