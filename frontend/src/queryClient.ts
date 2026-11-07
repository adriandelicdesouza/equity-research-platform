import { MutationCache, QueryCache, QueryClient } from "@tanstack/react-query";
import { ApiError } from "./api/client";

export const ME_KEY = ["me"] as const;

/**
 * One client for the app. A 401 from any request means the session ended (expiry, sign-out
 * elsewhere): the cached identity is cleared so the sign-in page is shown, instead of each
 * view handling it. 4xx responses are not retried — they will not change on retry.
 */
export function createQueryClient(): QueryClient {
  const client: QueryClient = new QueryClient({
    queryCache: new QueryCache({ onError: (err) => onAuthError(err) }),
    mutationCache: new MutationCache({ onError: (err) => onAuthError(err) }),
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        refetchOnWindowFocus: false,
        retry: (count, err) =>
          !(err instanceof ApiError && err.status >= 400 && err.status < 500) && count < 2,
      },
    },
  });
  function onAuthError(err: unknown) {
    if (err instanceof ApiError && err.status === 401 && client.getQueryData(ME_KEY)) {
      client.setQueryData(ME_KEY, null);
    }
  }
  return client;
}
