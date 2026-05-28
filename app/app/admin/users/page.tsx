import { redirect } from "next/navigation";

export default function DeprecatedAdminUsersPage() {
  redirect("/?page=settings&section=users");
}
