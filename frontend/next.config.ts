import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  devIndicators: false,
  turbopack: {
    root: process.cwd(),
  },
  async rewrites() {
    return [
      { source: "/resume", destination: "/Alex_Example.pdf" },
      { source: "/research/tiny-object-tracking", destination: "/Tiny_Object_Tracking.pdf" },
    ];
  },
  async headers() {
    return [
      {
        source: "/resume",
        headers: [{ key: "Content-Disposition", value: 'inline; filename="Alex_Example.pdf"' }],
      },
      {
        source: "/research/tiny-object-tracking",
        headers: [
          {
            key: "Content-Disposition",
            value: 'inline; filename="Tiny_Object_Tracking_Applied_to_Shrimp_Detection.pdf"',
          },
        ],
      },
    ];
  },
};

export default nextConfig;
