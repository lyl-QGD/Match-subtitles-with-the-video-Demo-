-- 只读检查：001 成功后执行。应返回 19 张表，且全部 rls_enabled=true。
SELECT c.relname AS table_name, c.relrowsecurity AS rls_enabled
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname='dialogue' AND c.relkind='r' ORDER BY c.relname;

SELECT * FROM dialogue.schema_versions;

-- 三个角色都应无 schema/table 权限。
SELECT role_name,
  has_schema_privilege(role_name,'dialogue','USAGE') AS schema_access,
  has_table_privilege(role_name,'dialogue.segments','SELECT') AS read_segments
FROM (VALUES ('anon'), ('authenticated'), ('service_role')) AS roles(role_name);
