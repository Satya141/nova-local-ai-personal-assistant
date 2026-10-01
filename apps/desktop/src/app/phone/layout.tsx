import type { Metadata, Viewport } from "next";

export const metadata: Metadata = {
  title: "NOVA",
  description: "NOVA on your phone, talking to NOVA on your PC.",
  manifest: "/phone.webmanifest",
  icons: { icon: "/phone-icon.png", apple: "/phone-icon.png" },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  // The composer stays above the keyboard and the gesture bar.
  viewportFit: "cover",
  interactiveWidget: "resizes-content",
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#fbfbfa" },
    { media: "(prefers-color-scheme: dark)", color: "#17181c" },
  ],
};

export default function PhoneLayout({ children }: { children: React.ReactNode }) {
  return children;
}
