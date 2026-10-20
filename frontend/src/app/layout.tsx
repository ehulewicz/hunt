import type { Metadata } from "next";
import type { ReactNode } from "react";
import localFont from "next/font/local";

import "@/styles/tokens.css";
import "@/styles/base.css";
import "@/styles/app.css";
import "@/styles/hunt.css";

const instrument = localFont({
  src: "./fonts/instrument-sans-latin.woff2",
  variable: "--font-instrument",
  display: "swap",
  weight: "400 700",
});

export const metadata: Metadata = {
  title: "Personal OS",
  description: "Private hosted workspace",
};

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return <html className={instrument.variable} lang="en"><body>{children}</body></html>;
}
