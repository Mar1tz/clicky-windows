# Local development

HeyTora has real users, so development never touches the live service.
Everything runs on your PC: a local Supabase in Docker (database, sign-in,
the `heytora` and `paddle` functions, an email inbox), and the app from
source pointed at it. Live changes only on a release.

## Once

1. Install **Docker Desktop** and start it. Node 22+ is needed too (`npx`
   fetches the pinned Supabase CLI, so there is nothing else to install).
2. `python tools/dev.py setup`

   The first time, it downloads the local Supabase (a few minutes), applies
   every migration in `supabase/migrations`, and adds two test accounts
   (`supabase/seed.sql`). It writes `.env.development` and lists anything
   that is still empty.

## Every day

```
python tools/dev.py            # the local stack, the functions, and the app
```

Close the installed HeyTora first: only one HeyTora runs at a time (they
would share the hotkey and the microphone).

| What | Where |
|---|---|
| Sign in | `python tools/dev.py signin` (free@heytora.test), or `signin pro@heytora.test`, or any new email. The app's window only signs in through the website, which doesn't run against the local database yet - this signs the dev app in for you |
| The sign-in code | Mailpit, http://127.0.0.1:57324 (every email the local stack sends) |
| Tables, users, SQL | Studio, http://127.0.0.1:57323 |
| The app's data | `%LOCALAPPDATA%\HeyTora-dev` (live's stays in `HeyTora`) |
| Function logs | the terminal you started `tools/dev.py` in |

Editing a file in `supabase/functions` reloads it. Editing the app needs a
restart of the app, as before.

| Command | What it does |
|---|---|
| `python tools/dev.py reset` | Empties the local database, then all migrations and the seed again |
| `python tools/dev.py status` | Prints the addresses above |
| `python tools/dev.py stop` | Stops the local stack (its data is kept) |

## Changing the database

1. Add `supabase/migrations/00NN_heytora_<what>.sql`, the next number.
2. `python tools/dev.py reset`: it must run from an empty database, in
   order, after all the others. That is exactly what live will do.
3. Try it in the app. Commit the migration with the code that needs it.

Never edit a migration that is already live. Fix it with a new one.

## `.env.development`

Local and **test** keys only. It is git-ignored, like every `.env.*`.

| Key | Filled by |
|---|---|
| `TORA_SUPABASE_URL`, `TORA_SUPABASE_ANON_KEY` | `tools/dev.py`, from `supabase status` |
| `TORA_WEBSITE_URL` | `http://localhost:3500` (heytora-web's `pnpm dev`) |
| `GEMINI_API_KEY` | copied once from `.env`'s `GOOGLE_API_KEY` |
| `HEYTORA_TICKET_SECRET` | made up once by `tools/dev.py` |
| `PADDLE_ENV` | always `sandbox`; `tools/dev.py` refuses anything else |
| `PADDLE_API_KEY`, `PADDLE_WEBHOOK_SECRET`, `PADDLE_PRICES`, `PADDLE_CREDITS_PRODUCT` | you, from the Paddle **sandbox** account, to try checkout (test card `4242 4242 4242 4242`) |
| `OMNIROUTE_API_KEY`, `OMNIROUTE_BASE_URL` | you, to try agents through the gateway |

Paddle can't reach your PC, so its webhook doesn't arrive locally. To try a
plan change, set the plan in Studio (`profiles.plan`), or send the webhook
yourself.

## Branches

* `main` is what users have. Only a release puts anything there.
* `dev` is where work happens. Commit and push to `dev`.
* To release: merge `dev` into `main`, then release from `main`.

## Releasing

From `main`, after merging `dev`. `.env.production` holds the live database
password (`SUPABASE_DB_PASSWORD`) and nothing else is read from it.

```
python tools/release.py --check                              # what live lacks; changes nothing
python tools/release.py 1.7.0 --notes "..." --dry-run        # everything checked, nothing changed
python tools/release.py 1.7.0 --notes "..."                  # the server, then the app
python tools/release.py --server-only                        # a server fix, no new app
```

In order, stopping at the first problem: `main` is clean and the same as
GitHub's; the tests pass; live's migrations are compared with
`supabase/migrations` (an extra one on live stops it; the missing ones are
listed and applied after you type `yes`); the functions are deployed; then
the app is built and published as before.

The server goes live before the app, so for a few minutes the old app talks
to the new server. A server change must keep the last app working.

### Live's history (30 Oct 2026)

Migrations 0001-0013 were run on live by hand (the SQL editor), so live's
migration history was empty and `--check` listed every one as missing. A
read-only check in the SQL editor showed 0001-0013 really are on live and
0014 is not. `python tools/release.py --mark-applied 0001-0013` records them
as applied - live's history only, after you type "yes" - so the next release
applies 0014 alone. From now on, change live only through `tools/release.py`:
anything run by hand has to be recorded the same way, or a release runs it again.

## The website

`heytora-web` uses the database only for its Google / Microsoft sign-in
page and the referral page. Dev runs send the app's website links to
`http://localhost:3500`, never to heytora.co.za, so nothing reaches live by
accident. If you're working on those pages, the website needs to read its
Supabase address from a setting, the way the app does. That change hasn't
been made yet.
