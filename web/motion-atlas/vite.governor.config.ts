import { defineConfig } from "vite";

export default defineConfig({
  define: {
    __GOV_VERSION__: JSON.stringify("motion-atlas")
  },
  build: {
    target: "es2022",
    outDir: ".",
    emptyOutDir: false,
    lib: {
      entry: "governor.ts",
      formats: ["es"],
      fileName: () => "governor.js"
    },
    rollupOptions: {
      output: { assetFileNames: "governor.[ext]" }
    }
  }
});
