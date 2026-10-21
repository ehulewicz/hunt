import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  devIndicators: false,
  turbopack: {
    root: process.cwd(),
  },
  async rewrites() {
    return [{ source: "/resume", destination: "/Alex_Example.pdf" }];
  },
  async headers() {
    return [{
      source: "/resume",
      headers: [{ key: "Content-Disposition", value: 'inline; filename="Alex_Example.pdf"' }],
    }];
  },
};

export default nextConfig;
