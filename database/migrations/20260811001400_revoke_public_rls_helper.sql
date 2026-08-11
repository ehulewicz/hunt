-- The Supabase RLS event-trigger helper is owned and invoked by postgres. It is
-- not an application RPC and must not be executable through the public API.

revoke all on function public.rls_auto_enable() from public, anon, authenticated, service_role;
