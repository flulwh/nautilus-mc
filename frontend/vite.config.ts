import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  build: {
    outDir: "../nautilus/webui/dist",
    emptyOutDir: true,
    chunkSizeWarningLimit: 900,
    rollupOptions: {
      output: {
        manualChunks: {
          react: ["react", "react-dom", "react-router-dom"],
          mantine: ["@mantine/core", "@mantine/hooks", "@mantine/notifications"],
          data: ["@tanstack/react-query"],
          // The animation runtime is only needed once a route mounts; keeping
          // it in its own chunk stops it from bloating the entry bundle.
          motion: ["motion/react", "motion-dom", "motion-utils"],
        },
      },
    },
  },
  server: {
    port: 5173,
    proxy: {
      "/api": "http://127.0.0.1:8199",
      "/ws": { target: "ws://127.0.0.1:8199", ws: true },
    },
  },
});
