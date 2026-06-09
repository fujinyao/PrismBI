import type { NextConfig } from "next";

const apiInternalUrl = process.env.API_INTERNAL_URL || process.env.NEXT_PUBLIC_API_URL || "http://localhost:8400";
const wsInternalUrl = process.env.WS_INTERNAL_URL || process.env.NEXT_PUBLIC_WS_URL || "http://localhost:8400";

const withBundleAnalyzer = process.env.ANALYZE === "1"
  ? require("@next/bundle-analyzer")({ enabled: true })
  : (config: NextConfig) => config;

const nextConfig: NextConfig = {
  output: "standalone",

  turbopack: {},

  serverExternalPackages: ["ws"],

  experimental: {
    serverActions: {
      bodySizeLimit: "4mb",
    },
  },

  async rewrites() {
    return [
      {
        source: "/api/:path*",
        destination: `${apiInternalUrl}/api/:path*`,
      },
      {
        source: "/ws/:path*",
        destination: `${wsInternalUrl}/ws/:path*`,
      },
    ];
  },

  async headers() {
    return [
      {
        source: "/api/ask/stream",
        headers: [
          { key: "Cache-Control", value: "no-cache, no-transform" },
          { key: "X-Accel-Buffering", value: "no" },
        ],
      },
    ];
  },
};

export default withBundleAnalyzer(nextConfig);
