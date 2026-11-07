// The signed-in user and their effective permissions, as reported by GET /api/auth/me.
// UI controls are hidden or disabled from these permissions for clarity only; the backend
// enforces every permission on every request (hidden buttons are not a security control).

import { createContext, useContext, type ReactNode } from "react";
import type { MeOut } from "./api/schema.gen";

export const PERM = {
  read: "company:read",
  write: "company:write",
  lock: "dataset:lock",
  importData: "data:import",
  userManage: "user:manage",
} as const;

/**
 * Permissions that exist in the backend but are granted to no role until the owner decides
 * (P15, D60). Listed here only to explain *why* a control is unavailable; whether a user
 * holds them is always read from /auth/me.
 */
export const PENDING_APPROVAL: Record<string, string> = {
  [PERM.lock]:
    "Dataset locking awaits the owner's approval (decision P15): the dataset:lock permission is not granted to any role yet, administrators included.",
  [PERM.importData]:
    "File ingestion awaits the owner's approval (decision P15): the data:import permission is not granted to any role yet, administrators included. Preview is available; ingestion runs through the CLI until then.",
};

export interface Session {
  me: MeOut;
  can: (permission: string) => boolean;
  /** Explanation when `permission` is missing (pending approval vs. role). */
  whyNot: (permission: string) => string;
}

const SessionContext = createContext<Session | null>(null);

export function makeSession(me: MeOut): Session {
  const perms = new Set(me.permissions);
  return {
    me,
    can: (p) => perms.has(p),
    whyNot: (p) =>
      perms.has(p)
        ? ""
        : (PENDING_APPROVAL[p] ??
          `Your role (${me.user.roles.join(", ") || "none"}) does not include the ${p} permission.`),
  };
}

export function SessionProvider({ me, children }: { me: MeOut; children: ReactNode }) {
  return <SessionContext.Provider value={makeSession(me)}>{children}</SessionContext.Provider>;
}

export function useSession(): Session {
  const s = useContext(SessionContext);
  if (!s) throw new Error("useSession outside SessionProvider");
  return s;
}
