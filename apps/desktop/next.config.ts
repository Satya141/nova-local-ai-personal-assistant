import type { NextConfig } from "next";

// Tauri serves the UI from disk, so the app is a static export with no Node server.
const nextConfig: NextConfig = {
  output: "export",
  images: { unoptimized: true },
  // The dev overlay badge would sit on top of a 64px-tall launcher.
  devIndicators: false,
};

export default nextConfig;
