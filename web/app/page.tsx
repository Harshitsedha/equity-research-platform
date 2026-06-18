import { redirect } from "next/navigation";

// The viewer's entry point is the stock list.
export default function Home() {
  redirect("/stocks");
}
