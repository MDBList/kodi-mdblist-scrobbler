import json
import os

import xbmcaddon
import xbmcvfs


def _addon():
    return xbmcaddon.Addon()


def _state_path():
    profile = xbmcvfs.translatePath(_addon().getAddonInfo("profile"))
    return os.path.join(profile, "sync_state.json")


def _load():
    try:
        with open(_state_path(), "r") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _write(data):
    path = _state_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f)


def get_synced_at(category: str):
    return _load().get(category, {}).get("synced_at")


def set_synced_at(category: str, timestamp: str):
    data = _load()
    data.setdefault(category, {})["synced_at"] = timestamp
    _write(data)


def get_known_items(category: str):
    """Returns {key: identity_dict} for the last-pushed state of a category.
    Storing the identity (not just the key) means a removal payload can still
    be built even after the item has left the Kodi library snapshot entirely
    (e.g. the file was deleted, not just marked unwatched)."""
    items = _load().get(category, {}).get("known_items")
    return items if isinstance(items, dict) else {}


def set_known_items(category: str, items: dict):
    data = _load()
    data.setdefault(category, {})["known_items"] = items
    _write(data)


def update_known_item(category: str, key: str, item):
    """Patch a single key in place rather than replacing the whole known_items
    dict -- for a single-item live push (see push_single in watched_sync.py /
    ratings_sync.py), which only ever examines one item, not the full library."""
    data = _load()
    bucket = data.setdefault(category, {}).setdefault("known_items", {})
    if item is None:
        bucket.pop(key, None)
    else:
        bucket[key] = item
    _write(data)


def get_last_activities_seen():
    activities = _load().get("last_activities_seen")
    return activities if isinstance(activities, dict) else {}


def set_last_activities_seen(activities: dict):
    data = _load()
    data["last_activities_seen"] = activities
    _write(data)


def get_last_sync_summary():
    return _load().get("last_run")


def set_last_sync_summary(summary: dict):
    data = _load()
    data["last_run"] = summary
    _write(data)
