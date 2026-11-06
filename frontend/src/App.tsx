import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, ApiError } from "./api";
import { HomePage } from "./HomePage";
import { LoginPage } from "./LoginPage";

export function App() {
  const qc = useQueryClient();
  const me = useQuery({
    queryKey: ["me"],
    queryFn: async () => {
      try {
        return await api.me();
      } catch (err) {
        if (err instanceof ApiError && err.status === 401) return null;
        throw err;
      }
    },
    retry: false,
  });
  const login = useMutation({
    mutationFn: ({ u, p }: { u: string; p: string }) => api.login(u, p),
    onSuccess: (data) => qc.setQueryData(["me"], data),
  });
  const logout = useMutation({
    mutationFn: api.logout,
    onSettled: () => qc.setQueryData(["me"], null),
  });

  if (me.isPending) return <p className="center muted">Loading…</p>;
  if (me.isError) return <p className="center error">Unable to reach the server.</p>;
  if (!me.data) return <LoginPage onLogin={(u, p) => login.mutateAsync({ u, p })} />;
  return <HomePage me={me.data} onLogout={() => logout.mutate()} />;
}
