-- ============================================================
-- Demo workspace — schema setup (unified, idempotent).
--
-- Turns "demo mode" into a real "Demo" company workspace:
--   companies.is_demo  -> flags a company as the Demo workspace (turns on the
--                         guided-tour bubbles + restricts demo accounts to it).
--   users.company_id   -> a user's home workspace; demo users are pinned to the
--                         Demo company. Carried in the JWT (api/auth.py) so the
--                         backend can scope a demo session to its sandbox.
--
-- The Demo company row and its starter data (projects + expenses) are
-- provisioned by the app — on the first demo-user create, or via the Demo
-- Manager "Reset workspace" action (api/routers/demo_admin.py). There is NO
-- seed step here.
--
-- Pairs with:
--   api/routers/companies.py   -> list_companies demo filter
--   api/routers/demo_admin.py  -> creates/links the Demo company
--   api/auth.py                -> company_id JWT claim
--   apps/hub-vite org-store.ts -> Org.isDemo / isActiveOrgDemo()
--
-- Run on staging, then prod.
-- ============================================================

-- 1) companies.is_demo -----------------------------------------------------
alter table companies add column if not exists is_demo boolean not null default false;

-- Hot path: the demo /companies filter and "is this a demo workspace" lookups.
create index if not exists idx_companies_is_demo
  on companies (is_demo)
  where is_demo = true;

-- 2) users.company_id ------------------------------------------------------
-- Nullable: normal internal users have no home workspace (they see all). FK is
-- ON DELETE SET NULL so deleting a company never deletes its users.
alter table users
  add column if not exists company_id uuid references companies(id) on delete set null;

create index if not exists idx_users_company on users (company_id)
  where company_id is not null;
