import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";
import { VitePWA } from "vite-plugin-pwa";

export default defineConfig({
  plugins: [
    react(),
    VitePWA({
      registerType: "prompt",
      injectRegister: null,
      manifest: false,
      workbox: {
        // Registration is manual; there is no update-prompt UI. Activate the new
        // shell for the next navigation without reloading an in-progress review.
        skipWaiting: true,
        clientsClaim: true,
        globPatterns: ["**/*.{js,css,html,svg,png,webmanifest,woff2}"],
        navigateFallback: "index.html",
        navigateFallbackDenylist: [/^\/auth\//],
        // Authentication, runtime configuration, and API responses are never cached.
        runtimeCaching: [],
        cleanupOutdatedCaches: true,
      },
    }),
  ],
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test-setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
