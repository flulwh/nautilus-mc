/**
 * Permission checks against the flat list returned by `/auth/me`.
 *
 * The backend expands Casbin policies (which contain regular expressions and
 * wildcards) into concrete `object:action` pairs. Re-implementing that match
 * on the client - `perms.includes("server:control")` against a policy string
 * like `"server:read|control|console"` - never matched, so every non-admin
 * operator lost the start/stop buttons.
 */
export function hasPerm(permissions: string[], object: string, action: string): boolean {
  return permissions.includes(`${object}:${action}`) || permissions.includes("*:.*");
}

export function isAdmin(role: string | undefined, permissions: string[]): boolean {
  return role === "admin" || permissions.includes("*:.*") || hasPerm(permissions, "users", "manage");
}
