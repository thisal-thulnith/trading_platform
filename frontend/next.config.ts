import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // `npm run build` / `npm run start` use .next-prod so a `next dev` in the same folder (which writes .next)
  // can never break the production server's static files.
  distDir: process.env.NEXT_DIST_DIR || ".next",
};

export default nextConfig;
