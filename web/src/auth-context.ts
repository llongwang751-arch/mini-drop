/**
 * 会话用户来自 /auth/me，由应用外壳统一下发。
 * 角色判断只用于按钮和表单的可用性提示，不能替代后端逐资源授权。
 */
import { createContext, useContext } from "react";
import type { User } from "./types";
export const UserContext = createContext<User | null>(null);
export function useCanWrite() {
  const user = useContext(UserContext);
  return (
    !!user &&
    ["admin", "platform_admin", "group_admin", "operator"].includes(user.role)
  );
}
