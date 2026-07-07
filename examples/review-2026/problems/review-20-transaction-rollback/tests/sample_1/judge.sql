select section, item, value
from (
  select 'account' as section, account_id::text as item, balance::text as value from accounts
  union all
  select 'audit', log_id::text, message from audit_logs
) x
order by section, item;
