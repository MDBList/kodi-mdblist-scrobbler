# Changelog

## [1.3.3] - 2026-09-07

* Fixed a rare failure mode where a sync running right after Kodi starts, a library scan/clean finishes, or a temporarily unreadable video library could be misread as "everything was removed" and push that as a mass removal to MDBList (or, in the reverse direction, unwatch a batch of items locally). Removals are now only ever applied on a deliberate sync (the 24-hour background sync or manual "Sync now"), and a suspiciously empty or oversized removal batch is held and retried on the next sync instead of applied.

## [1.3.2] - 2026-09-05

* Fixed hitting MDBList's rate limit on a large first sync: requests are now paced under the server's short-window write budget, and a 429 is retried using the server's own requested wait instead of aborting the whole sync.
* Fixed losing sync progress if a run is interrupted: push progress is now saved per-batch instead of only at the end, so a later failure no longer forces redoing already-pushed items.

## [1.3.1] - 2026-09-04

* Fixed watched sync: a rewatch that only updated the watched date (without changing membership) could be silently dropped by the scheduled full sync, and would only reach MDBList via the live push on stop.

## [1.3.0] - 2026-09-02

* Renamed from "MDBList Scrobbler" to "MDBList" -- the addon now does a lot more than scrobble.
* Added two-way watched status sync between Kodi and MDBList.
* Added two-way ratings sync between Kodi and MDBList.
* Added library/collection sync: MDBList reflects exactly what's in your Kodi library, including when you remove something (Kodi to MDBList only).
* Watched/rating changes made in Kodi (native "mark as watched", the rate dialog, another addon) now reach MDBList right away instead of waiting for the next scheduled sync.
* Changes made on MDBList, or on another device, are checked for and pulled into Kodi in the background.
* New **Settings > Sync** category: a toggle per feature (all off by default), a "Sync now" action, and a "Last sync" status field.
* The rating prompt's "Save to MDBList" is now controlled by the Sync settings' ratings toggle instead of its own separate setting.

## [1.0.0] - pending

* Initial release