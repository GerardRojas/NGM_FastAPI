-- ============================================================
-- NGM HUB - Cost Codes
-- Cost codes are assigned to line items in an estimate.
-- They map to CSI divisions or company-specific codes.
-- ============================================================

-- Shared trigger function to keep updated_at fresh (idempotent).
create or replace function set_updated_at()
returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

create table if not exists cost_codes (
  id          uuid primary key default gen_random_uuid(),
  code        text not null unique,                    -- e.g. '01-100', '03-300', '26-050'
  description text not null,                           -- e.g. 'General Conditions', 'Cast-in-Place Concrete'
  division    text,                                    -- CSI division: '01', '03', '26'
  category    text,                                    -- grouping: 'General', 'Concrete', 'Electrical'
  unit        text,                                    -- default unit for this code: SF, LF, EA
  is_active   boolean not null default true,
  sort_order  integer default 0,
  created_at  timestamptz not null default now(),
  updated_at  timestamptz not null default now()
);

create index if not exists idx_cost_codes_code on cost_codes(code);
create index if not exists idx_cost_codes_division on cost_codes(division);
create index if not exists idx_cost_codes_active on cost_codes(is_active);

drop trigger if exists trg_cost_codes_updated on cost_codes;
create trigger trg_cost_codes_updated before update on cost_codes
  for each row execute function set_updated_at();

-- Seed common CSI divisions (full list can be imported via CSV later)
insert into cost_codes (code, description, division, category, sort_order) values
  ('01-100', 'General Conditions',          '01', 'General',        10),
  ('02-100', 'Site Preparation',            '02', 'Sitework',       20),
  ('03-100', 'Concrete Forming',            '03', 'Concrete',       30),
  ('03-300', 'Cast-in-Place Concrete',      '03', 'Concrete',       31),
  ('04-100', 'Masonry',                     '04', 'Masonry',        40),
  ('05-100', 'Structural Steel',            '05', 'Metals',         50),
  ('06-100', 'Rough Carpentry',             '06', 'Wood & Plastic', 60),
  ('06-200', 'Finish Carpentry',            '06', 'Wood & Plastic', 61),
  ('07-100', 'Waterproofing',               '07', 'Thermal & Moist',70),
  ('07-200', 'Insulation',                  '07', 'Thermal & Moist',71),
  ('07-300', 'Roofing',                     '07', 'Thermal & Moist',72),
  ('08-100', 'Doors',                       '08', 'Doors & Windows',80),
  ('08-500', 'Windows',                     '08', 'Doors & Windows',81),
  ('09-100', 'Drywall',                     '09', 'Finishes',       90),
  ('09-300', 'Tile',                        '09', 'Finishes',       91),
  ('09-500', 'Acoustical Ceilings',         '09', 'Finishes',       92),
  ('09-900', 'Painting',                    '09', 'Finishes',       93),
  ('09-600', 'Flooring',                    '09', 'Finishes',       94),
  ('10-100', 'Specialties',                 '10', 'Specialties',    100),
  ('11-100', 'Equipment',                   '11', 'Equipment',      110),
  ('12-100', 'Furnishings',                 '12', 'Furnishings',    120),
  ('15-100', 'Plumbing',                    '15', 'Mechanical',     150),
  ('15-500', 'HVAC',                        '15', 'Mechanical',     151),
  ('16-100', 'Electrical',                  '16', 'Electrical',     160),
  ('16-500', 'Lighting',                    '16', 'Electrical',     161),
  ('26-050', 'Electrical General',          '26', 'Electrical',     260),
  ('31-100', 'Earthwork',                   '31', 'Earthwork',      310),
  ('32-100', 'Exterior Improvements',       '32', 'Exterior',       320),
  ('32-900', 'Landscaping',                 '32', 'Exterior',       321),
  ('33-100', 'Utilities',                   '33', 'Utilities',      330)
on conflict (code) do nothing;
