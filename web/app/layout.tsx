import type { Metadata } from "next";
import "./style.css";
export const metadata: Metadata = { title: "Papertrail — Your private PDF desk", description: "Local PDF tools with original preservation and audit history" };
export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="en"><body>{children}</body></html>;
}
