import type { Metadata } from "next";
import type { ReactNode } from "react";

import "@/styles/tokens.css";
import "@/styles/base.css";
import "@/styles/app.css";

export const metadata: Metadata = {
  title: "Personal OS",
  description: "Private hosted workspace",
};

export default function RootLayout({ children }: Readonly<{ children: ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
