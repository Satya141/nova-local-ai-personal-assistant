import type { Metadata } from "next";
import "./globals.css";
import "./character.css";

export const metadata: Metadata = {
  title: "NOVA",
  description: "One AI. One Memory. Any Device.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
