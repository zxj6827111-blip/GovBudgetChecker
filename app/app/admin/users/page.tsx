import { redirect } from "next/navigation";

// 旧入口：直接进入新版系统设置页的「用户与权限」分区（SystemManagementPanel 读取 section 参数）。
export default function DeprecatedAdminUsersPage() {
  redirect("/settings?section=users");
}
