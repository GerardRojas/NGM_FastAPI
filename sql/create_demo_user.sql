-- =============================================================================
-- Demo login user  (username "Demo", password "12345678")
-- =============================================================================
-- Creates a self-contained demo account for the NGM HUB React app:
--   * a dedicated "Demo" role (so we never touch real roles / CEO / COO)
--   * the "Demo" user, password "12345678" (bcrypt / passlib hash)
--   * can_view = true on EXACTLY these modules, can_view = false elsewhere:
--       expenses, estimator, dashboard, pipeline, analytics,
--       ngm-board (Process / Operation Manager board),
--       ngm-cam, messages, workspace (NGM Connect),
--       art, projects, reporting, budget-vs-actuals, pnl-report
--       (the assistant + the report/projects surfaces Art drives in the demo)
--
-- HOW THE SIDEBAR WORKS (why we link menu_item_id) -----------------------------
-- The React hub builds its sidebar in api/routers/permissions.py::_build_user_menu
-- from role_permissions rows whose menu_item_id is NOT NULL, joined to
-- menu_items -> menu_categories. The menu module_key/slug is taken from
-- menu_items.slug (NOT from role_permissions.module_key). So for a module to
-- appear, its role_permissions row needs BOTH can_view = true AND a
-- menu_item_id linked to the matching menu_items row. We resolve that link by
-- slug below, mirroring sql/calendar_menu_item.sql and client_portal_menu_items.sql.
--
-- PASSWORD HASH ----------------------------------------------------------------
-- Login (api/auth.py -> utils/auth.py) verifies with passlib CryptContext(bcrypt).
-- passlib happily verifies a standard $2b$ bcrypt hash, so we can ship the hash
-- inline and keep this a pure, dependency-free SQL artifact. The hash below was
-- produced by the app's own utils.auth.hash_password("12345678") and verified to
-- round-trip. To rotate the password, replace the hash literal (generate one via:
--   .venv/Scripts/python -c "from utils.auth import hash_password; print(hash_password('NEWPASS'))"
-- ).
--
-- IDEMPOTENT. Safe to re-run. Run in the Supabase SQL editor (staging first,
-- then prod). After running, the user can log in immediately; if they were
-- already logged in they must refresh to pick up the cached menu.
-- Path: C:\Users\germa\Desktop\NGM_API\sql\create_demo_user.sql
-- =============================================================================

-- Outer dollar-quote tag is `$demo$` (not `$$`) on purpose: the bcrypt hash
-- below contains `$2b$`, `$12$`, ... sequences, and a `$$` block can confuse a
-- statement splitter into ending the block early. A named tag never collides.
DO $demo$
DECLARE
    -- All three are uuid in the LIVE schema (rols.rol_id, users.user_id,
    -- menu_items.id), even though the older sql/create_role_permissions.sql
    -- declares rol_id as bigint — the live DB won (the bigint cast error on the
    -- first run returned a uuid). Declared explicitly (not %TYPE) because the
    -- Supabase SQL editor choked compiling schema.table.column%TYPE references.
    v_rol_id     uuid;
    v_user_id    uuid;
    -- bcrypt hash of "12345678" (passlib bcrypt, cost 12). Verifies via
    -- utils.auth.verify_password("12345678", <this>) == True.
    v_pw_hash    text := '$2b$12$73aP1Yh4YzrlgsjSRW4WIeKGjHcLl5iNLJr8LF5HedL9unSB2IkSy';
    r            record;
    v_menu_id    uuid;
BEGIN
    -- -------------------------------------------------------------------------
    -- 1) Demo role  (table: rols, PK rol_id uuid w/ default, name rol_name)
    -- -------------------------------------------------------------------------
    -- NOTE: uses `:=` with a scalar subquery instead of `SELECT ... INTO var`.
    -- The Supabase SQL editor's statement parser misreads `SELECT ... INTO
    -- v_rol_id` as "SELECT INTO <relation>" and fails with
    -- 'relation "v_rol_id" does not exist'. The `:=` form is unambiguous.
    v_rol_id := (SELECT rol_id FROM public.rols WHERE rol_name = 'Demo' LIMIT 1);
    IF v_rol_id IS NULL THEN
        INSERT INTO public.rols (rol_name) VALUES ('Demo');
        v_rol_id := (SELECT rol_id FROM public.rols WHERE rol_name = 'Demo' LIMIT 1);
    END IF;

    -- -------------------------------------------------------------------------
    -- 2) Demo user  (table: users, PK user_id uuid default gen_random_uuid())
    --    Required-for-login columns: user_name, password_hash, user_rol (FK rols).
    --    The /auth/create_user endpoint inserts with exactly these 3 columns, so
    --    no other NOT NULL-without-default column exists to fill.
    -- -------------------------------------------------------------------------
    v_user_id := (SELECT user_id FROM public.users WHERE user_name = 'Demo' LIMIT 1);
    IF v_user_id IS NULL THEN
        INSERT INTO public.users (user_name, password_hash, user_rol, account_type, is_external)
        VALUES ('Demo', v_pw_hash, v_rol_id, 'internal', false);
        v_user_id := (SELECT user_id FROM public.users WHERE user_name = 'Demo' LIMIT 1);
    ELSE
        -- Keep an existing Demo user in sync (password + role) without creating a dup.
        UPDATE public.users
           SET password_hash = v_pw_hash,
               user_rol       = v_rol_id,
               account_type   = COALESCE(account_type, 'internal')
         WHERE user_id = v_user_id;
    END IF;

    -- -------------------------------------------------------------------------
    -- 3) Permissions: grant the 9 allowed modules, revoke everything else.
    --    For each allowed module we try a list of candidate slugs (hyphen +
    --    underscore + legacy variants) so we link to whatever menu_items row the
    --    live DB actually has. module_key is stored as the canonical slug.
    -- -------------------------------------------------------------------------

    -- 3a) First wipe any pre-existing perms for the Demo role so a re-run yields
    --     EXACTLY the 9 grants and nothing stale (the demo role is ours alone).
    DELETE FROM public.role_permissions WHERE rol_id = v_rol_id;

    -- 3b) Insert the 9 grants. (canonical_key, module_name, [candidate slugs...])
    FOR r IN
        SELECT * FROM (VALUES
            ('dashboard',  'Dashboard',   ARRAY['dashboard']),
            ('expenses',   'Expenses',    ARRAY['expenses']),
            ('estimator',  'Estimator',   ARRAY['estimator']),
            ('pipeline',   'Pipeline',    ARRAY['pipeline','pipeline-manager','pipeline_manager']),
            ('analytics',  'Analytics',   ARRAY['analytics','reporting']),
            ('ngm-board',  'NGM Board',   ARRAY['ngm-board','process-manager','process_manager']),
            ('ngm-cam',    'NGM Cam',     ARRAY['ngm-cam','ngm_cam']),
            ('messages',   'Messages',    ARRAY['messages']),
            ('workspace',  'NGM Connect', ARRAY['workspace','connect','ngm-connect','ngm_connect']),
            -- Art (assistant) + the report/projects surfaces it drives in the demo.
            ('art',                'Art',               ARRAY['art']),
            ('projects',           'Projects',          ARRAY['projects','projects-management']),
            ('reporting',          'Reporting',         ARRAY['reporting']),
            ('budget-vs-actuals',  'Budget Vs Actuals', ARRAY['budget-vs-actuals','budget_vs_actuals']),
            ('pnl-report',         'P&L COGS',          ARRAY['pnl-report','pnl_report','pnl'])
        ) AS t(canonical_key, module_name, slugs)
    LOOP
        -- Resolve the real menu_items row by trying each candidate slug in order.
        v_menu_id := (
            SELECT mi.id
              FROM public.menu_items mi
             WHERE mi.slug = ANY(r.slugs)
             ORDER BY array_position(r.slugs, mi.slug)
             LIMIT 1
        );

        -- Plain INSERT: step 3a already deleted every Demo perm, so there is no
        -- row to conflict with. This avoids depending on a UNIQUE(rol_id,
        -- module_key) index existing (some installs lack it; other permission
        -- seeds in this folder use NOT EXISTS rather than ON CONFLICT).
        INSERT INTO public.role_permissions
            (rol_id, module_key, module_name, module_url, menu_item_id,
             can_view, can_edit, can_delete)
        VALUES
            (v_rol_id, r.canonical_key, r.module_name, r.canonical_key, v_menu_id,
             true, false, false);

        IF v_menu_id IS NULL THEN
            RAISE NOTICE 'Demo: no menu_items row found for "%" (tried %). Permission '
                'granted, but it will not appear in the React sidebar until a '
                'matching menu_items.slug exists.', r.canonical_key, r.slugs;
        END IF;
    END LOOP;

    RAISE NOTICE 'Demo user ready: user_id=%, rol_id=%, 9 modules granted.', v_user_id, v_rol_id;
END $demo$;

-- =============================================================================
-- VERIFICATION (optional — run after the block above)
-- =============================================================================
-- Confirms login wiring + the exact 9 visible modules, with menu link status.
--
-- SELECT u.user_id, u.user_name, u.account_type, r.rol_name
--   FROM public.users u
--   JOIN public.rols r ON r.rol_id = u.user_rol
--  WHERE u.user_name = 'Demo';
--
-- SELECT rp.module_key,
--        rp.can_view, rp.can_edit, rp.can_delete,
--        mi.slug AS linked_menu_slug,
--        (rp.menu_item_id IS NOT NULL) AS shows_in_sidebar
--   FROM public.role_permissions rp
--   JOIN public.rols r          ON r.rol_id = rp.rol_id
--   LEFT JOIN public.menu_items mi ON mi.id = rp.menu_item_id
--  WHERE r.rol_name = 'Demo'
--  ORDER BY rp.module_key;
-- =============================================================================
