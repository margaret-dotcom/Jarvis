# domos

A mirror of the DOM OS knowledge base, one note per active entry, sorted into `atwc/`, `qca/` and `both/` by the entry's business. `python -m brain.domos_sync` rebuilds this folder every night at 9 and any time you run it.

Do not edit anything in here. The next sync overwrites it. If a note is wrong, fix the entry in DOM OS and it lands here on the next run. Do not file new notes here either; they belong in the other areas, and ingest never puts anything in this folder.

Each note carries `source: domos` and `external_id` (the DOM OS row id) in its frontmatter, which is how the sync knows which files are its own. This README is the one file it leaves alone.
