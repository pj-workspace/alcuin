import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  agentRules: false,
  devIndicators: false,
  distDir: process.env.ALCUIN_NEXT_DIST_DIR ?? ".next",
  transpilePackages: ["@alcuin/contracts"],
  experimental: {
    optimizePackageImports: ["lucide-react"],
  },
};

export default nextConfig;
