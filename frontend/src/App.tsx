import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ApiError } from "./api/client";
import { api } from "./api/endpoints";
import { LoginPage } from "./LoginPage";
import { ME_KEY } from "./queryClient";
import { SessionProvider } from "./session";
import { Shell } from "./Shell";

export function App() {
  const qc = useQueryClient();
  const me = useQuery({
    queryKey: ME_KEY,
    queryFn: async () => {
      try {
        return await api.me();
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) return null;
        throw err;
      }
    },
    retry: false,
    staleTime: Infinity,
  });
  const login = useMutation({
    mutationFn: ({ u, p }: { u: string; p: string }) => api.login(u, p),
    onSuccess: (data) => {
      qc.removeQueries({ predicate: (q) => q.queryKey[0] !== ME_KEY[0] });
      qc.setQueryData(ME_KEY, data);
    },
  });
  const logout = useMutation({
    mutationFn: api.logout,
    onSettled: () => {
      qc.removeQueries({ predicate: (q) => q.queryKey[0] !== ME_KEY[0] });
      qc.setQueryData(ME_KEY, null);
    },
  });

  if (me.isPending) return <p className="center muted">Loading…</p>;
  if (me.isError) return <p className="center error">Unable to reach the server.</p>;
  if (!me.data) return <LoginPage onLogin={(u, p) => login.mutateAsync({ u, p })} />;
  return (
    <SessionProvider me={me.data}>
      <Shell onLogout={() => logout.mutate()} />
    </SessionProvider>
  );
}
