import { redirect } from "next/navigation";

export default function DeprecatedAdminPage() {
  redirect("/?page=settings");
}
