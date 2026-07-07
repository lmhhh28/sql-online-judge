select section, key, value
from (
  select 'part_total' as section, pno as key, sum(qty)::text as value
  from v_sanjian_spj
  group by pno
  union all
  select 's1_supply', pno, qty::text
  from v_sanjian_spj
  where sno = 'S1'
) x
order by section, key, value;
