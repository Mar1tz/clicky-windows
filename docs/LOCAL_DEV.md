# Local development

HeyTora has real users, so development never touches the live service.
Everything runs on your PC: a local Supabase in Docker (database, sign-in,
the `heytora` and `paddle` functions, an email inbox), and the app from
source pointed at it. Live changes only on a release.

`python main.py` on its own refuses to start from a source folder, because
the live address is the app's default. That is deliberate. `TORA_ALLOW_LIVE=1`
overrides it for the rare time testing against live is really meant.

## Once

1. Install **Docker Desktop** and start it. Node 22+ is needed too (`npx`
   fetches the pinned Supabase CLI, so there is nothing else to install).
2. `pip install -r requirements.txt` if this is a new PC.
3. `python tools/dev.py setup`

   The first time, it downloads the local Supabase (a few minutes), applies
   every migration in `supabase/migrations`, and adds the test accounts
   (`supabase/seed.sql`). It writes `.env.development` and lists anything
   that is still empty. A Gemini key is copied from `.env`'s `GOOGLE_API_KEY`;
   with none there, add `GEMINI_API_KEY` by hand (free:
   https://aistudio.google.com/app/apikey).
4. `python tools/dev.py doctor` says whether everything is right, and what
   to do about anything that isn't.

## Every day

```
python tools/dev.py                        # the stack, the functions, signed in, the app
python tools/dev.py --as pro@heytora.test  # signed in as this account
python tools/dev.py --tour                 # with the first-launch tour
python tools/dev.py --signed-out           # at the sign-in window
```

Close the installed HeyTora first: only one HeyTora runs at a time (they
would share the hotkey and the microphone). Settings' foot says
**HeyTora 1.x · local** while it runs against this copy.

Migrations someone else added are applied by itself when you start, after a
`git pull`.

| Account | What it is for |
|---|---|
| `free@heytora.test` | Free, the default |
| `pro@heytora.test`, `max@heytora.test` | The paid plans |
| `out@heytora.test` | Free with this month's credits used up: Home's warning strip. Comes back next month until a `reset` |
| any new email | A new account |

| What | Where |
|---|---|
| Sign in as someone else | `python tools/dev.py signin pro@heytora.test`, or `--as` above |
| The sign-in code | Mailpit, http://127.0.0.1:57324 (every email the local stack sends) |
| Tables, users, SQL | Studio, http://127.0.0.1:57323 |
| The app's data | `%LOCALAPPDATA%\HeyTora-dev` (live's stays in `HeyTora`) |
| Function logs | the terminal you started `tools/dev.py` in |

Editing a file in `supabase/functions` reloads it. Editing the app needs a
restart of the app, as before.

| Command | What it does |
|---|---|
| `python tools/dev.py doctor` | Checks everything; changes nothing |
| `python tools/dev.py reset` | Empties the local database, then all migrations and the seed again. The dev app signs in again next start |
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

* `main` is the shared code, and what the next release ships. Merging to it
  reaches no user: only `tools/release.py` does that.
* Work on a branch of your own from `main`, and open a pull request. Merge
  `main` into your branch first and run the tests.
* Never push straight to `main`.

## Releasing

From an up-to-date `main`. `.env.production` holds the live database
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

To work on the sign-in page or the referral page, with `heytora-web` next to
this folder:

```
python tools/dev.py          # writes heytora-web/.env.development.local
cd ../heytora-web; pnpm dev  # http://localhost:3500
```

Sign-in window > Sign in with your browser then works end to end against the
local stack, by **email**: the link is in Mailpit. Google and Microsoft are
not set up locally. Without the website running, use `--as` as above.

`.env.development.local` is deliberate: `next dev` reads it and a production
build never does. Never put a local address in `.env.local`, which a build
would ship to real users.
