import { useRef } from "react";
import { newIdempotencyKey } from "../api/client";

/**
 * Idempotency keys that follow the request: retrying the *same* request (e.g. after a network
 * failure) reuses its key, so the backend replays the first outcome instead of acting twice;
 * a changed request gets a new key (reusing a key for a different request is refused with
 * 422 idempotency_key_reused). `reset` after success makes an intentional repeat a new action.
 */
export function useIdempotency() {
  const last = useRef<{ fingerprint: string; key: string } | null>(null);
  return {
    keyFor(request: unknown): string {
      const fingerprint = JSON.stringify(request);
      if (!last.current || last.current.fingerprint !== fingerprint) {
        last.current = { fingerprint, key: newIdempotencyKey() };
      }
      return last.current.key;
    },
    reset(): void {
      last.current = null;
    },
  };
}
