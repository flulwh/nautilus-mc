import { useQuery } from "@tanstack/react-query";

import { api } from "./api";

/** Panel version from /meta — the backend `_version.py` is the only source. */
export function useVersion(): string {
  const q = useQuery<{ version: string }>({
    queryKey: ["meta"],
    queryFn: () => api.get("/meta"),
    staleTime: Infinity,
    retry: false,
  });
  return q.data ? `v${q.data.version}` : "";
}
