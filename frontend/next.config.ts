import type { NextConfig } from "next";

// /resume proxies the API's stored resume PDF; the address bar keeps /resume
// and the backend sends the inline disposition itself. RESUME_PDF_URL can point
// elsewhere (e.g. the e2e stub); without either value there is no rewrite.
const resumePdfUrl = process.env.RESUME_PDF_URL
  ?? (process.env.NEXT_PUBLIC_API_URL
    ? `${process.env.NEXT_PUBLIC_API_URL.replace(/\/$/, "")}/v1/public/resume.pdf`
    : undefined);

const nextConfig: NextConfig = {
  devIndicators: false,
  turbopack: {
    root: process.cwd(),
  },
  async rewrites() {
    return [
      ...(resumePdfUrl ? [{ source: "/resume", destination: resumePdfUrl }] : []),
      { source: "/research/tiny-object-tracking", destination: "/Tiny_Object_Tracking.pdf" },
    ];
  },
  async headers() {
    return [
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
