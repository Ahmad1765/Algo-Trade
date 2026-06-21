import { redirect } from "next/navigation";

// Root has no UI of its own — send users to the dashboard.
// (Static export can't use next.config redirects, so do it here.)
export default function HomePage() {
  redirect("/dashboard");
}
