import "./globals.css";
import { NavBar } from "@/components/NavBar";
import Providers from "./providers";

export const metadata = { title: "Paper Trading" };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-gray-950 text-gray-200 antialiased">
        <Providers>
          <NavBar />
          <main className="mx-auto max-w-7xl p-4">{children}</main>
        </Providers>
      </body>
    </html>
  );
}
