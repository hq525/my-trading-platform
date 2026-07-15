import { LiveGate } from "./live-context";

export default function LiveLayout({ children }: { children: React.ReactNode }) {
  return <LiveGate>{children}</LiveGate>;
}
