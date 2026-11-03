-- Local only: `supabase db reset` runs this after the migrations. Live never
-- sees it (`supabase db push` does not seed).
--
-- Two accounts to sign in with. The app emails a code; the local email is
-- Mailpit, http://127.0.0.1:57324 - nothing leaves this PC.
--   free@heytora.test  Free
--   pro@heytora.test   Pro
--   max@heytora.test   Max
--   out@heytora.test   Free, this month's credits used up (Home's warning strip)
-- A brand-new email works too: signing up is signing in for the first time.
-- The profile row comes from 0001's on_auth_user_created trigger.

insert into auth.users (
  instance_id, id, aud, role, email, encrypted_password, email_confirmed_at,
  raw_app_meta_data, raw_user_meta_data, created_at, updated_at,
  confirmation_token, recovery_token, email_change_token_new, email_change
)
values
  ('00000000-0000-0000-0000-000000000000', '11111111-1111-1111-1111-111111111111',
   'authenticated', 'authenticated', 'free@heytora.test',
   extensions.crypt('password123', extensions.gen_salt('bf')), now(),
   '{"provider": "email", "providers": ["email"]}', '{}', now(), now(), '', '', '', ''),
  ('00000000-0000-0000-0000-000000000000', '22222222-2222-2222-2222-222222222222',
   'authenticated', 'authenticated', 'pro@heytora.test',
   extensions.crypt('password123', extensions.gen_salt('bf')), now(),
   '{"provider": "email", "providers": ["email"]}', '{}', now(), now(), '', '', '', '')
on conflict (id) do nothing;

insert into auth.identities (
  id, user_id, provider_id, provider, identity_data, last_sign_in_at, created_at, updated_at
)
values
  ('11111111-1111-1111-1111-111111111111', '11111111-1111-1111-1111-111111111111',
   '11111111-1111-1111-1111-111111111111', 'email',
   '{"sub": "11111111-1111-1111-1111-111111111111", "email": "free@heytora.test", "email_verified": true}',
   now(), now(), now()),
  ('22222222-2222-2222-2222-222222222222', '22222222-2222-2222-2222-222222222222',
   '22222222-2222-2222-2222-222222222222', 'email',
   '{"sub": "22222222-2222-2222-2222-222222222222", "email": "pro@heytora.test", "email_verified": true}',
   now(), now(), now())
on conflict do nothing;

update public.profiles set plan = 'pro', plan_status = 'active'
where id = '22222222-2222-2222-2222-222222222222';

-- The other two, made the same way.
insert into auth.users (
  instance_id, id, aud, role, email, encrypted_password, email_confirmed_at,
  raw_app_meta_data, raw_user_meta_data, created_at, updated_at,
  confirmation_token, recovery_token, email_change_token_new, email_change
)
select '00000000-0000-0000-0000-000000000000', u.id::uuid, 'authenticated', 'authenticated', u.email,
       extensions.crypt('password123', extensions.gen_salt('bf')), now(),
       '{"provider": "email", "providers": ["email"]}', '{}', now(), now(), '', '', '', ''
from (values ('33333333-3333-3333-3333-333333333333', 'max@heytora.test'),
             ('44444444-4444-4444-4444-444444444444', 'out@heytora.test')) as u(id, email)
on conflict (id) do nothing;

insert into auth.identities (
  id, user_id, provider_id, provider, identity_data, last_sign_in_at, created_at, updated_at
)
select u.id::uuid, u.id::uuid, u.id, 'email',
       jsonb_build_object('sub', u.id, 'email', u.email, 'email_verified', true), now(), now(), now()
from (values ('33333333-3333-3333-3333-333333333333', 'max@heytora.test'),
             ('44444444-4444-4444-4444-444444444444', 'out@heytora.test')) as u(id, email)
on conflict do nothing;

update public.profiles set plan = 'max', plan_status = 'active'
where id = '33333333-3333-3333-3333-333333333333';

-- More than Free's month (50 credits = 500,000 micro-dollars). Dated now: it
-- is used up this month - `python tools/dev.py reset` brings it back next month.
insert into public.usage_costs (user_id, kind, model, cost_micro)
values ('44444444-4444-4444-4444-444444444444', 'talk', 'seed', 600000);
