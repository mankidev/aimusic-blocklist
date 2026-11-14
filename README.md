# aimusic-blocklist

List of YouTube Music artist channels that publish AI-generated music. A crawler on a server updates it once a day.

## Files

The list is in `data/` and is also published as a [release](https://github.com/mankidev/aimusic-blocklist/releases/latest). The release title is the number of listed channels. The files:

- `blocklist.json`: channels the crawler marked as AI.
- `suspects.json`: channels with some AI signals, not enough to list. They are checked again after 30 days.
- `stats.json`: counts from the run.

When the list changes, the server commits `data/` and a new release comes out. The last 10 releases are kept. This URL always points to the newest list:

```
https://github.com/mankidev/aimusic-blocklist/releases/latest/download/blocklist.json
```

Each entry looks like this:

```json
{
 "name": "Sirian Nox",
 "channelId": "UCEClyTqxY56zlHSJjCNRgIA",
 "score": 9,
 "signals": ["all_recent", "fast_output", "many_releases_few_fans", "no_description"],
 "subscribers": 5420,
 "releases": 33,
 "firstRelease": 2026,
 "listed": "2026-11-14"
}
```

`artists[].name` and `artists[].channelId` use the same keys as the [Ammit](https://github.com/CappyT/ammit) list, so code that reads Ammit can read this file too.

## How a channel gets scored

`src/crawl.py` opens the channel page on YouTube Music and adds points:

| Signal | Points |
| --- | --- |
| `disclosed`: description mentions Suno, Udio, "AI generated" and similar | 4 |
| `all_recent`: 5+ releases, the oldest from last year or this year, under 300K subscribers | 4 |
| `fast_output`: 1.5+ releases per month | 2 |
| `very_fast_output`: 4+ releases per month | 1 |
| `many_releases_few_fans`: 20+ releases, under 20K subscribers | 2 |
| `ai_neighborhood`: half or more of "Fans might also like" is already listed | 2 |
| `no_description`: empty description | 1 |

A channel with a "Live performances" shelf scores 0. A score of 7 or more puts the channel on the list, and so does a score of 5 or more when the description discloses AI. Scores of 5 and 6 go to suspects.

## Where channels come from

The crawler starts from three places: the Ammit list, artist searches for the genres in `src/queries.txt`, and the "Fans might also like" shelf of channels that scored 3 or more. Neighbors of listed channels are checked first. Ammit channels go through the same scoring.

Listed channels are checked again after 120 days, so a channel can leave the list.

## Running it

```
python3 src/crawl.py --db state.db --repo . --out out --checks 200 --no-publish
python3 src/crawl.py --channel UCEClyTqxY56zlHSJjCNRgIA
```

The second command scores one channel and prints the result. `deploy/` holds the systemd timer used on the server, and `.github/workflows/release.yml` turns the tag on that commit into a release.

## Wrong entry?

Open an issue with the channel link. Real artists who release a lot of singles can score high.

## License

MIT. The crawler reads the Ammit list (GPL-3.0) as a starting point and copies no entries from it.
