# people

One file per person: `firstname-lastname.md`, for example `jane-smith.md`. Person files get appended to, never duplicated. Each capture goes under a dated heading at the bottom, so the file reads as a running history of that relationship.

To add to a person's file use `python -m brain.ingest --person "Jane Smith" --note "Call today, she can start the roof on the 12th"`, or drop a file in the inbox and run `python -m brain.ingest --person "Jane Smith"`. If the file does not exist yet, ingest creates it. Notes that mention "meeting with" or "call with" but are filed without `--person` land here as ordinary dated notes.
