
## [0.6.0] - 2026-11-23

### Features / Changes

- installer: verify-cmrelay-signature checks install files signatures.

### Fixes

- status: count a unit restarting in a loop as not running.
- make sure unbound is restarted after hand back.
- prechecks: refuse a deploy while another service uses port 53.
- bump nixpkgs 26.05 to 2026-11-21.

### Miscellaneous Tasks

- also use the cache for releases for now.
- installer: chore-bump-nixpkgs lists the packages whose versions changed.
- improve chore-sync-from-relay to highlight doc changes.
- update to chatmail/relay 8a7dcba595b397473b6db7637876f1b79fd22bb5.

### Other

- simplify getting started and create a dedicated takeover page.

### Tests

- installer: run chatmail/relay cmdeploy tests in the full cmlxc round.


## [0.5.0] - 2026-11-21

initial signed release of cmrelay, the single-file chatmail relay installer
