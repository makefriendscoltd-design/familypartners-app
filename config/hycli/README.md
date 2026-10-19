# Hycli source reader

Verified scope: public Lounge catalog metadata only. X/Reddit search, article bodies behind login, comments and posting are not implemented here.

Pinned runtime: official Hycli v0.1.0. Mac launcher: `/Users/apple/.local/bin/fp-hycli`, isolated state under `/Users/apple/.local/share/familypartners/hycli/state`. No site account is imported for this reader.

```sh
fp-hycli spec validate config/hycli/lounge.json
fp-hycli spec install config/hycli/lounge.json
fp-hycli run fp-lounge catalog --format json
python3 deploy/collect_drop_sources.py --lounge-cli /Users/apple/.local/bin/fp-hycli --out /tmp/fp-research-unique
```

`collect_drop_sources.py` auto-selects `fp-hycli` when installed on PATH; otherwise it retains HTTP collection. `--lounge-cli ''` explicitly selects HTTP. A selected Hycli failure remains an error; no silent fallback. No daemon or scheduler is needed for this read.

The spec reads embedded Next.js Flight data, including records beyond the visible first page. Both `articles` and `resources` are required. The Python reader rejects empty/partial/login payloads, invalid slugs and duplicates. Dates remain unknown and `body_verified` stays false.

Live verification 2026-10-19: 43 overseas cases + 1 free resource; all 44 slugs/titles matched the independent existing HTTP parser. The visible-page extraction design was rejected because it exposed only 12 unique overseas cases.

Hycli Reddit prepare job did not yet produce a verified reader; anonymous Reddit rate-limit and X HTTP307 are not success evidence. Existing Exa/manual original-read paths remain.
