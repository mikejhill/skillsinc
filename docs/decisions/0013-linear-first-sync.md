# 0013: A first sync stays linear in the size of the library

This record is for anyone changing how entry changes are planned or how the applier records ownership, or wondering why the manifest has a journal beside it.

- **Status:** Accepted
- **Date:** 2026-10-21

## Context

A first sync grew quadratically with the library ([issue #25](https://github.com/mikejhill/skillsinc/issues/25)): 400 items of each kind into every profile took 30 seconds. Two steps repeated work proportional to everything done so far:

- Each entry in a shared file was planned as its own change, chained to the bytes the previous one wrote, so every entry re-rendered and re-parsed the whole file (`tomlkit` for `config.toml`, a full rescan for JSONC `mcp.json`).
- The applier saved the whole manifest after every change, so a run writing thousands of files serialized thousands of growing manifests.

Both were deliberate: the chain let the applier stop at the first drifted entry, and saving after every change kept the manifest exact after an interruption.

## Decision

- **Entry changes to one file are planned together.** The planner decides each entry against one parse of the file, then edits the document once (`EntryFile.edit`) and checks the result once. Every entry change still appears in the plan and report, but all of them expect the file's current bytes and carry the final bytes. The applier verifies the file once, writes it once, and records each entry. A file that changed since planning blocks all of its entries instead of the ones after the drift, which is the safer outcome. JSONC sets for one container are spliced in one pass; removals, which are rare in bulk, are still spliced one at a time.
- **Ownership is journaled, and the manifest saved once per destination.** Each applied change appends one JSON line to `manifests/<destination>.journal`; the first line names the destination root. The manifest is saved when the destination finishes, successfully or not, and the journal is then deleted. Loading a manifest replays a leftover journal, so an interrupted run loses nothing it wrote. A last line cut short by the interruption is ignored and removed before the next append; any other unreadable line fails closed like a corrupt manifest.
- **CI keeps it linear.** `tests/perf/test_budgets.py` times first syncs of 100 and 400 items of each kind and fails if four times the items takes seven times as long or more (linear measures 4-5x, the old code 10-11x).

## Consequences

- 400 items of each kind into every profile: first sync from 30 s to about 2 s.
- `plan --json` and the report are unchanged: one change per entry. A `Change` for an entry now carries the whole file's result, not the result of that entry alone.
- Removing hundreds of entries from one JSONC file in one run is still quadratic in that file. That needs a library to drop hundreds of MCP servers at once.
