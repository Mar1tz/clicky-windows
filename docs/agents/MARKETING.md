# Marketing pack, and pictures for every agent

*Code: `extensions/marketing/` and `agents/tools/design_tools.py`. Tests:
`tests/agents/test_marketing_pack.py` and `tests/agents/test_tools_design.py`,
offline, with a stand-in browser and no model.*

## Pictures: designed, not painted

The agents think with Claude through OmniRoute. Claude does not paint
pixels; it lays things out. So an agent makes a picture by writing it as
one HTML page (CSS and SVG, no scripts), and `render_design` turns that
page into a PNG, JPG, or PDF for print:

* **Edge renders it.** Every Windows PC has Edge, so there is no download
  and no new dependency. Chrome is used if Edge is missing, and
  `TORA_RENDER_BROWSER` names another browser. Edge runs headless in a
  throwaway profile, so a running Edge is never touched. It takes 1 to 2
  seconds a picture.
* **The model sees what it made.** The result carries a 1024px preview, and
  the worker shows it to the model, which fixes anything cut off,
  overlapping or misspelt. The prompts cap this at two more tries a
  picture, because every try writes the whole page out again.
* **Sizes are named.** These include Instagram post, portrait and story;
  Facebook, LinkedIn and X posts; Pinterest; YouTube thumbnails; email
  headers; display ads; A5, A4 and A3 posters and US Letter flyers. Print
  sizes are laid out in millimetres, so a PDF is exactly one sheet. PNGs of
  print sizes are rendered at twice the pixels.
* **It stays inside its folder.** A design may use files in the workspace,
  web addresses and `data:` URIs. A local file outside the workspace is
  refused, and so are scripts. A design that comes out as one flat colour
  is sent back to be fixed.
* **The source is kept.** The HTML is saved beside the picture, so it can
  be changed later.

The File Agent has `render_design`, so "make a poster for the ball" works
without any pack. What it costs is ordinary agent calls, which count toward
the plan's cost ceiling like any other.

Photographs need an image model. OmniRoute offers Gemini 3.1 Flash Image,
FLUX and GPT Image. One can be added beside this when wanted.

## The pack

The pack keeps the brand kit, campaigns, and every post, ad and email with
its picture. All of it lives in one folder (`Marketing/` by default):

    marketing.json       the brand kit: name, tagline, voice, audience, colours, fonts, logo, website, hashtags
    marketing.sqlite     campaigns and pieces
    Brand/               the logo and other brand files
    Campaigns/<name>/    plan.md, images/
    Designs/             pictures made on their own (a poster)
    Exports/<name>-<date>/   the kit

**Rules, as code.** A draft that breaks a rule is saved anyway. The problem
is then raised as a finding to fix:

| Where | Rule |
|---|---|
| X | 280 characters, each link counting 23 |
| Instagram | 2,200 characters, 30 hashtags; links cannot be clicked ("link in bio"); about 125 show before "more" |
| LinkedIn | 3,000 characters; about 210 show before "more" |
| Threads / TikTok / Pinterest / Facebook | 500 / 4,000 / 500 / 63,206 characters |
| Google search ad | 3–15 headlines of 30, 2–4 descriptions of 90, a final URL (hard limits) |
| Meta ad | primary text 125, headline 40, description 30 (cut off past these; advice, not limits) |
| LinkedIn ad | intro 150, headline 70 (advice) |
| Email | subject over 60 is cut off; all capitals or spam words flagged; preview text wanted; an unsubscribe line required |

**The campaign check** finds:

* pieces outside the campaign's dates;
* two posts in one slot;
* pictures missing where the platform needs one, and on every story;
* posts with no date;
* channels with nothing in them;
* a campaign with no plan.

**The export** produces:

* `plan.md`;
* the pictures;
* `schedule.csv` (every post);
* `hootsuite.csv`, in Hootsuite's bulk-upload layout: no header, the date
  as `DD/MM/YYYY HH:mm` on a five-minute mark, then the message and link.
  Check it on first use;
* `ads.csv`;
* each email as safe HTML and as text;
* `calendar.xlsx`.

Re-exporting the same day replaces that day's folder.

**Levels.** Every tool is READ or DRAFT. Nothing is posted, sent, boosted
or bought. Publishing becomes a POST tool once a platform connection
exists (catalogue gate 2).

## Workflows

* **Plan a campaign** ("plan a marketing campaign for our spring sale"):
  1. the brand kit;
  2. the campaign, from the request;
  3. the plan;
  4. the posts;
  5. their pictures;
  6. ads and emails, for those channels;
  7. the check;
  8. the fixes;
  9. the export.
* **This week's posts** ("write this week's social media posts"): a
  week's posts with pictures, checked and exported. It is the dashboard's
  button. With `auto_social_posts` on, it runs on Mondays.
* **Design a picture** ("design a poster for the charity ball"): one
  picture, with a print PDF when it is for print. It never becomes a
  campaign.

Ad copy, newsletters and the brand kit go to the Marketing Agent without
a fixed checklist.

## What the first live run taught

The first live run asked for a poster for "Saturday 14 November". The agent:

* printed 2025, a Friday;
* planned a campaign with a Facebook post;
* redrew its picture eight times, in 15 minutes.

Three fixes came from it:

* every agent is now told today's date, in
  `AgentManager._context_lines_for`;
* a one-off design has its own two-step checklist;
* the prompts cap redraws at two and keep a design's HTML compact.
