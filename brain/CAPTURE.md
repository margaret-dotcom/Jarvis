# Capture habits

The brain only works if things go in. Each moment below has the exact command and the exact sentence to say to Hermes. Both do the same thing. Run commands from the repo root.

## End of a call

Right after you hang up, while you still remember what was said.

Command:
```
python -m brain.ingest --person "Jane Smith" --note "Call about the Lopez roof. She wants the estimate by Friday. Mentioned her HOA needs a copy of the permit."
```

Say to Hermes: "Remember this about Jane Smith: call about the Lopez roof, she wants the estimate by Friday, her HOA needs a copy of the permit."

## After a decision

Any time you settle something, big or small. Include the reason if you have one.

Command:
```
python -m brain.ingest --note "Decided to move ATWC intake calls to 15 minute slots going forward. The 30 minute slots were running half empty."
```

Say to Hermes: "File this decision: intake calls move to 15 minute slots going forward, the 30 minute slots were running half empty."

## A piece of content worth studying

A reel, a video, a post you want to come back to. A link is enough.

Command:
```
echo "https://www.instagram.com/reel/EXAMPLE/" > brain/inbox/reel-hook-example.txt
python -m brain.ingest --area marketing
```

Say to Hermes: "Save this to marketing: https://www.instagram.com/reel/EXAMPLE/ . The hook is the first three seconds of silence."

## When a number matters

A price, a count, a percentage, a date. Numbers are the first thing you forget and the first thing you need.

Command:
```
python -m brain.ingest --note "#qca Hail job average ticket this month came in at 14200 across 9 jobs."
```

Say to Hermes: "Note for QCA: hail job average ticket this month was 14200 across 9 jobs."

## A document or email you want to keep

Save it to the inbox and let ingest sort it. PDFs, Word files, email exports, screenshots all work.

Command:
```
cp ~/Downloads/insurance-update.pdf brain/inbox/
python -m brain.ingest
```

Say to Hermes: "I dropped a file in the brain inbox, please file it."

## Forcing a folder

Put `#atwc`, `#qca`, `#marketing`, `#people`, `#decisions` or `#personal` as the first word of the note. Or add `--area qca` to the command. Or tell Hermes "file this under qca".

## Checking your work

```
python -m brain.ingest --dry-run
python -m brain.search "permit"
```

Open `brain/INDEX.md` to see everything grouped by area, newest first.
