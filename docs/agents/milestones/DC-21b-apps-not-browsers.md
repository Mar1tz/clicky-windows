# "It only thinks of things on the browsers, not actual apps"

## The complaint

> "I ask it to do things, it only thinks of things on the browsers, not
> actually apps."

The log named the cause in one line — which is the first time that has been
true, and is what DC-01/DC-02 were for:

```
16:04:56 ERROR agents.control: adapter windows failed to describe itself
         TypeError: Action.__init__() got an unexpected keyword argument 'snapshot'
16:04:56 ERROR agents.control: adapter spotify failed to describe itself
         TypeError: Action.__init__() got an unexpected keyword argument 'snapshot'
16:04:56 tool desktop_action -> error: windows has no action 'open_app'. It can:
16:04:57 tool desktop_action -> error: spotify has no action ''. It can:
16:05:07 edge.open_url args={'url': 'https://open.spotify.com/search/Love Story'} -> ok
```

Windows and Spotify registered **zero** actions, so the only apps left were
the three browsers — and Tora dutifully opened a web page instead of playing
a song.

## The immediate cause, and why it is not the interesting part

`Action.snapshot` was added to `model.py` while the app was already running.
`agents.control.model` was in `sys.modules` from startup; `apps/windows_os.py`
is imported lazily, on the first `desktop_action` of the session, and so came
off disk with the new keyword against the old class. A restart fixes that
instance. Editing a running app is not a bug in the app.

**The interesting part is everything that happened next.** One adapter
raising should not be able to look, from the outside, like "Tora can only use
browsers".

## What was actually wrong

1. **A broken adapter disappeared in silence.** The registry caught the
   exception per adapter — right, so one bad app cannot hide the rest — and
   then forgot it. The app simply had no actions.
2. **`suggestions()` said `It can: ` and stopped.** An empty list, handed to
   the model, reads as "this app can't do it, find another way".
3. **A browser is always another way.** Nothing preferred a real app, so
   `edge.open_url` to `open.spotify.com` looked like a reasonable answer to
   "play Love Story". It opens a page about a song; it does not play one.
4. **A wrong action name was a dead end.** `spotify.skip_forward_two` got a
   sentence, not the list of real actions to correct against.

## What happens now

* `AppControl` keeps `_broken`, logs `NO actions available this session
  for: …` at ERROR, and exposes `broken()`.
* `suggestions()` for an app with no actions says it failed to load, gives
  the exception, says **"do NOT quietly do it through a browser instead"**,
  and that restarting usually fixes it. `desktop_action` refuses outright
  for such an app rather than letting the model improvise.
* `app_for_url()` knows which domains a real app owns. Asking a browser to
  open one is refused and redirected: *"spotify can do this directly — call
  desktop_action again with app='spotify'"*, with Spotify's actions
  attached.
* Any unfound action now returns the app's real action list alongside the
  message.
* The tool description leads with **REAL APP** and says outright that
  playing a song is `spotify.play`, not `open.spotify.com` in Edge, and that
  `action='list'` comes before assuming an app cannot do something.

## Cost

The `desktop_action` declaration grew from 237 to 386 tokens, so the
per-turn tool payload is 1,736 → 1,885. About $0.0001 a turn, against M28's
measured $0.0049 for a plain spoken exchange — roughly 2%. Worth it: the
failure it prevents is the assistant doing the wrong thing confidently.

## Out of scope

* `_OWNED_DOMAINS` is four entries, hand-written. It should be derived from
  what the adapters declare (an `owns_domains` attribute) once more apps have
  a web face.
* **DC-21** proper — checking an app is installed and running *before*
  offering its actions — is still not done. This milestone only covers an
  adapter that fails to describe itself.

## Proof

`test_control_desktop.py` (21):

* `test_an_app_that_failed_to_load_says_so_instead_of_vanishing`
* `test_a_browser_is_not_how_you_play_a_song`
* `test_opening_an_owned_url_in_a_browser_is_sent_back_to_the_app`
* `test_a_wrong_action_name_comes_back_with_the_real_ones`

640 passing.
