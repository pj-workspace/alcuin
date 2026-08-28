import type { Metadata } from "next";
import { Geist, Instrument_Serif } from "next/font/google";

import "./globals.css";

const geist = Geist({ subsets: ["latin"], variable: "--font-geist" });
const instrumentSerif = Instrument_Serif({
  subsets: ["latin"],
  variable: "--font-instrument-serif",
  weight: "400",
});

export const metadata: Metadata = {
  title: "Alcuin — Intelligence, composed.",
  description: "A domain-neutral studio and extension platform for governed AI agents.",
  icons: { icon: "/brand/alcuin-mark.svg" },
};

export default function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className={`${geist.variable} ${instrumentSerif.variable}`}>{children}</body>
    </html>
  );
}
