"""Credits (migration 0012, 21 Oct 2026): the month's pot, shown, and more
that can be bought instead of waiting.

1 credit = 1 US cent of what Tora costs us. A plan's month is its
budget_usd in credits (Free 50, Pro 900); packs (675 for $10, 1,750 for
$25, 3,500 for $50) are Paddle's prices of one product, on Pro and Max, and
never expire. The month's credits go first, then bought ones; nothing new
opens once both are used up.

The app's side is tested against the fake server; Paddle's message with
Node (billing.test.ts, run by test_e2e_billing); the database's rules by
reading the migration. (On 21 Oct they were also played through on a
throwaway Postgres; running them for real belongs to the local Supabase
setup in docs/ROADMAP_BILLING.md.)
"""
from __future__ import annotations

from pathlib import Path

from tests.agents.test_e2e_heytora_account import _signed_in, heytora  # noqa: F401 - fixture

ROOT = Path(__file__).resolve().parents[2]
SQL = (ROOT / "supabase/migrations/0012_heytora_credits.sql").read_text(encoding="utf-8")
FN = (ROOT / "supabase/functions/heytora/index.ts").read_text(encoding="utf-8")
HOOK = (ROOT / "supabase/functions/paddle/index.ts").read_text(encoding="utf-8")
BILLING = (ROOT / "supabase/functions/_shared/billing.ts").read_text(encoding="utf-8")

PRO = {"plan": "pro", "plan_name": "Pro", "period_start": "2026-10-06T00:00:00+00:00",
       "period_end": "2026-11-06T00:00:00+00:00", "blocked": {}, "fair_use": 0.4,
       "credits": {"month": 900, "month_used": 360, "month_left": 540, "bought_left": 0, "can_buy": True}}


# -- what the user sees -----------------------------------------------------------------------------------

def test_the_balance_in_words():
    from account.service import credits_left, credits_words
    assert credits_words(PRO) == "540 of 900 credits left this month"
    bought = {**PRO, "credits": {**PRO["credits"], "month_left": 0, "bought_left": 675}}
    assert credits_words(bought) == "0 of 900 credits left this month · 675 added"
    assert credits_left(bought) == 675
    assert credits_words({"plan": "pro"}) == "" and credits_left({}) is None, "no credits: nothing said"


def test_running_low_and_running_out_say_what_to_do():
    from account.service import alerts
    low = {**PRO, "fair_use": 0.85, "credits": {**PRO["credits"], "month_left": 135}}
    assert alerts(low)[-1][1] == "135 of this month's credits left on the Pro plan. More on 6 Nov."
    low_bought = {**low, "credits": {**low["credits"], "bought_left": 675}}
    assert alerts(low_bought)[-1][1] == ("135 of this month's credits left on the Pro plan. "
                                         "Then your 675 added credits.")
    out = {**PRO, "fair_use": 1, "blocked": {"all": "budget"}}
    assert alerts(out)[-1][1] == ("This month's credits on the Pro plan are used up. They come back on 6 Nov. "
                                  "Add credits or move to Max in Settings → Plans & billing.")


def test_buying_credits_opens_paddles_checkout(heytora):
    _signed_in(heytora)
    heytora.fake.paying = "pro"
    assert heytora.account.buy_credits("pri_1750") == {
        "ok": True, "url": "https://heytora.example/pay?_ptxn=txn_9", "credits": 1750}
    assert heytora.fake.calls[-1][3] == {"price": "pri_1750"}, "the pack chosen, nothing else"


def test_on_free_buying_credits_says_why_not(heytora):
    from account.service import AccountError
    _signed_in(heytora)
    try:
        heytora.account.buy_credits("pri_675")
    except AccountError as e:
        assert "Pro and Max" in str(e)
    else:
        raise AssertionError("Free can't buy credits")


def test_the_packs_are_paddles_and_shown_with_the_plan():
    """A pack added in Paddle shows in the app with no release."""
    me = FN[FN.index('path === "me"'):FN.index('path === "account"')]
    assert "summary.credit_packs = (await creditPacks())" in me and ".can_buy" in me
    assert "`/prices?product_id=${encodeURIComponent(creditsProduct)}&status=active" in FN
    assert "`/prices/${PADDLE_CREDITS.anyPack}`" in FN, "the product, found from a known pack"
    assert "packsCache.at < 10 * 60_000" in FN, "Paddle asked at most every 10 minutes"
    assert 'anyPack: "pri_01m5g1j3j5crwascanyj3vec7c"' in BILLING, "the sandbox's $10 pack"


# -- the server and the webhook ---------------------------------------------------------------------------

def test_credits_are_sold_on_paid_plans_only():
    i = FN.index('path === "credits"')
    block = FN[i:FN.index('path === "billing"', i)]
    assert "hasLivePlan(profile)" in block, "Pro and Max, active"
    assert '(await creditPacks()).find((p) => p.price === String(body?.price ?? ""))' in block, \
        "only a pack on sale - never a price the app makes up"
    assert "price_id: pack.price" in block and "custom_data: { user_id: user.id" in block, \
        "whose they are is the sign-in's, never the app's"


def test_the_webhook_adds_paid_credits_once():
    assert HOOK.index("creditGrant(event)") < HOOK.index("planUpdate(event, PRICES)")
    assert 'admin.rpc("heytora_credit_grant"' in HOOK
    assert 'reply(500, { error: "could not add credits" })' in HOOK, "a hiccup: Paddle retries"
    assert "paddle_txn  text        not null unique" in SQL and "on conflict (paddle_txn) do nothing" in SQL


# -- the database -----------------------------------------------------------------------------------------

def test_the_months_credits_go_first_then_bought_ones():
    split = SQL[SQL.index("function public._heytora_cost_split()"):]
    assert "v_rest := greatest(new.cost_micro - (v_c ->> 'plan_left')::bigint, 0);" in split
    assert "new.topup_micro := least(v_rest, (v_c ->> 'topup_left')::bigint);" in split
    assert "pg_advisory_xact_lock(hashtext(new.user_id::text || ':credits'))" in split, \
        "two costs can't spend the same credit"
    assert "before insert on public.usage_costs" in SQL


def test_refused_only_when_both_are_used_up():
    spend = SQL[SQL.index("function public._heytora_spend("):SQL.index("function public.heytora_credit_grant(")]
    assert "(v_c ->> 'plan_left')::bigint <= 0 and (v_c ->> 'topup_left')::bigint <= 0" in spend
    assert "perform public._heytora_book(p_uid, p_kind, v_id, p_quantity);" in spend, "0011's booking stays"


def test_a_credit_is_a_cent_and_the_summary_shows_them():
    summ = SQL[SQL.index("function public.get_usage_summary()"):]
    assert "'month',      (v_c ->> 'plan_micro')::bigint / 10000" in summ
    assert "'can_buy',    (v_p ->> 'plan') in ('pro', 'max')" in summ
    assert "'credits',        v_credits" in summ


def test_only_the_webhook_may_add_credits():
    assert "grant execute on function public.heytora_credit_grant(uuid, int, text) to service_role;" in SQL
    assert "revoke all on function public.heytora_credit_grant(uuid, int, text)  from public, anon, authenticated;" \
        in SQL


def test_free_is_50_credits_and_its_count_is_only_a_brake():
    assert """'{"budget_usd": 0.5, "talk": 100}'::jsonb where key = 'free'""" in SQL


def test_the_plans_are_sold_in_credits_here_and_on_the_website():
    from ui.settings_tabs import PLANS
    feats = {p["key"]: p["features"] for p in PLANS}
    assert feats["free"][0] == "50 credits a month" and feats["pro"][0] == "900 credits a month"
    assert feats["max"][0] == "4,700 credits a month"
    web = ROOT.parent / "heytora-web/src/lib/site.ts"
    if web.exists():                                   # the website's repo, beside this one
        site = web.read_text(encoding="utf-8")
        for f in ("50 credits a month", "900 credits a month", "4,700 credits a month"):
            assert f'"{f}"' in site, f
        assert "300 asks" not in site and "150 tasks" not in site
