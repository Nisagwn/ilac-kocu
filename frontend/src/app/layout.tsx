import type { Metadata, Viewport } from "next";
import { headers } from "next/headers";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import { ServiceWorkerRegister } from "@/components/push-toggle";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin", "latin-ext"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin", "latin-ext"] });

export const metadata: Metadata = {
  title: "İlaç Koçu",
  description: "Tip 2 diyabet ve hipertansiyon için ilaç uyumu ve yaşam tarzı koçu",
  manifest: "/manifest.webmanifest",
  icons: { apple: "/icons/apple-touch-icon.png" },
  appleWebApp: { capable: true, title: "İlaç Koçu", statusBarStyle: "default" },
};

export const viewport: Viewport = { themeColor: "#0f766e", width: "device-width", initialScale: 1 };

export default async function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  // Nonce tabanlı CSP (middleware.ts): nonce istek başına üretildiği için sayfalar dinamik render
  // edilmelidir; statik ön-render edilmiş HTML'deki betiklerde nonce olmaz ve 'strict-dynamic' onları
  // engeller. headers() okumak tüm ağacı dinamik yapar. Özel bir <Script> / satır içi betik eklenirse
  // nonce şöyle okunup verilmelidir: const nonce = (await headers()).get("x-nonce"); <Script nonce={nonce} />.
  await headers();
  return (
    <html lang="tr" className={`${geistSans.variable} ${geistMono.variable}`}>
      <body className="antialiased">
        {children}
        <ServiceWorkerRegister />
      </body>
    </html>
  );
}
