import { defineConfig } from "vite"
import react from "@vitejs/plugin-react"
import tailwindcss from "@tailwindcss/vite"
import path from "node:path"

export default defineConfig(({ mode }) => {
  const isDev = mode === "development"

  return {
    base: "/",
    build: {
      outDir: "dist",
      emptyOutDir: true,
      sourcemap: isDev ? "inline" : false,
      minify: !isDev,
    },
    plugins: [react(), tailwindcss()],
    resolve: {
      alias: {
        "@": path.resolve(import.meta.dirname, "./src"),
      },
    },
    server: {
      host: "0.0.0.0",
      port: 8443,
      strictPort: true,
      proxy: {
        "/api": {
          target: "http://localhost:8000",
          changeOrigin: true,
        },
      },
    },
    preview: {
      host: "0.0.0.0",
      port: 8443,
    },
  }
})
