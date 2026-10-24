import { redirect } from "next/navigation";

// 旧入口：根路由会再重定向到 /workbench 并丢掉 page 参数，所以直接指向新版系统设置页。
export default function DeprecatedAdminPage() {
  redirect("/settings");
}
